from dataclasses import replace
from datetime import date

import numpy as np
import polars as pl
import pytest

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.forward.live import BookEntry, daily_orders
from sfactory.policy.catalog import DIVERSE_METHODS, diverse_rows, taxonomy
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.signals.methods import DEFAULT, FAMILY, GRID, EntrySpec, entry_and_score, weekdays_left_in_month
from sfactory.signals.specs import HOLD_EXIT_LIBRARY, NEUTRAL_BRK_EXIT, NEUTRAL_MR_EXIT, neutral_exit_for
from sfactory.timeline.folds import FoldConfig, FoldManager

CFG = FoldConfig(data_start=date(2010, 1, 4), first_dp=date(2013, 1, 1), holdout_start=date(2018, 1, 1),
                 data_end=date(2020, 1, 1), dp_months=6, is_years=3)
BASE = LadderConfig(min_price=0.0, min_dollar_vol=0.0)


def _setup(bars, divs, mem, tag="f"):
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev = fm.dev_view(bars)
    cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), tag, cost_bps=2.0)
    cache.set_index_events(mem)
    return fm, cache, dev, mem


@pytest.fixture(scope="module")
def market():
    return _setup(*make_market(12, 2600, seed=5))


def _drift_market(n=20, n_days=2600, seed=9, spread=0.0015):
    """Random walks plus a persistent per-symbol drift: yesterday's winners keep winning (XS momentum)."""
    bars, divs, mem = make_market(n, n_days, seed=seed, dividends=False, listings=False)
    rng = np.random.default_rng(seed)
    mu = {s: rng.normal(0, spread) for s in sorted(bars["symbol"].unique())}
    out = []
    for (sym,), g in bars.sort(["symbol", "date"]).partition_by("symbol", as_dict=True).items():
        f = pl.Series(np.exp(mu[sym] * np.arange(len(g))))
        out.append(g.with_columns([pl.col(c) * f for c in ("open", "high", "low", "close")]))
    return pl.concat(out), divs, mem


# --- method mechanics ------------------------------------------------------------------------------------
@pytest.mark.parametrize("method", DIVERSE_METHODS)
def test_new_methods_are_causal(market, method):
    _, cache, _, _ = market
    a = cache.arrays["S000"]
    ctx = cache.ctx("S000")
    for d in (1, -1):
        spec = EntrySpec(method, DEFAULT[method], d)
        full, sc = entry_and_score(spec, a.sig_high, a.sig_low, a.sig_close, ctx)
        for k in (400, 900, len(a.dates) - 2):
            part, sp = entry_and_score(spec, a.sig_high[: k + 1], a.sig_low[: k + 1], a.sig_close[: k + 1],
                                       ctx.upto(k))
            assert np.array_equal(part, full[: k + 1])
            assert np.allclose(sp, sc[: k + 1], equal_nan=True)


def test_methods_needing_context_fail_loudly_without_it(market):
    _, cache, _, _ = market
    a = cache.arrays["S000"]
    for m in ("xs_mom", "tom", "post_exdiv", "index_add"):
        with pytest.raises(ValueError):
            entry_and_score(EntrySpec(m, DEFAULT[m]), a.sig_high, a.sig_low, a.sig_close)


def test_turn_of_month_fires_k_weekdays_before_month_end(market):
    _, cache, _, _ = market
    a = cache.arrays["S001"]
    for k in (1, 2, 4):
        e, _ = entry_and_score(EntrySpec("tom", k), a.sig_high, a.sig_low, a.sig_close, cache.ctx("S001"))
        idx = np.nonzero(e)[0]
        assert (weekdays_left_in_month(a.dates[idx]) == k).all()
        months = a.dates[idx].astype("datetime64[M]")
        assert len(np.unique(months)) == len(idx) and len(idx) > 90    # once per month


def test_weekdays_left_is_a_calendar_rule():
    d = np.array(["2024-01-29", "2024-01-30", "2024-01-31", "2024-02-26"], dtype="datetime64[D]")
    assert weekdays_left_in_month(d).tolist() == [2, 1, 0, 3]


def test_xs_mom_enters_on_first_bar_of_month_with_momentum_score(market):
    _, cache, _, _ = market
    a = cache.arrays["S002"]
    e, s = entry_and_score(EntrySpec("xs_mom", 6), a.sig_high, a.sig_low, a.sig_close, cache.ctx("S002"))
    idx = np.nonzero(e)[0]
    mon = a.dates.astype("datetime64[M]")
    assert (mon[idx] != mon[idx - 1]).all() and idx.min() >= 6 * 21
    i = idx[5]
    assert np.isclose(s[i], -(a.sig_close[i - 21] / a.sig_close[i - 126] - 1))


def test_post_exdiv_enters_on_ex_dates(market):
    _, cache, _, _ = market
    a = cache.arrays["S003"]
    e, _ = entry_and_score(EntrySpec("post_exdiv", 1), a.sig_high, a.sig_low, a.sig_close, cache.ctx("S003"))
    assert np.array_equal(np.nonzero(e)[0], np.nonzero(a.div > 0)[0]) and e.sum() > 10


def test_index_add_events_come_from_membership_starts(market):
    _, cache, _, mem = market
    late = mem.filter(pl.col("start") > date(2010, 1, 4))["symbol"].to_list()
    assert late
    for s in cache.arrays:
        flags = cache.ctx(s).index_add
        assert flags.sum() == 0                  # data of a late joiner starts at its membership start
    # an addition inside the data: symbol listed from the start, added to the index later
    mem2 = mem.with_columns(pl.when(pl.col("symbol") == "S001").then(date(2012, 3, 1)).otherwise(pl.col("start"))
                            .alias("start"))
    cache.set_index_events(mem2, "mem2")
    f = cache.ctx("S001").index_add
    assert f.sum() == 1 and cache.arrays["S001"].dates[np.argmax(f)] >= np.datetime64("2012-03-01")
    t = cache.trades("S001", EntrySpec("index_add", 1), neutral_exit_for("index_add"))
    assert len(t) == 1 and t["signal_date"][0] >= date(2012, 3, 1)
    cache.set_index_events(mem)


def test_neutral_exits_and_libraries_per_method():
    assert neutral_exit_for("vol_spike") == NEUTRAL_MR_EXIT
    assert neutral_exit_for("squeeze") == NEUTRAL_BRK_EXIT
    assert neutral_exit_for("xs_mom").max_hold == 21 and neutral_exit_for("tom").max_hold == 5
    assert neutral_exit_for("post_exdiv") in HOLD_EXIT_LIBRARY and neutral_exit_for("index_add") in HOLD_EXIT_LIBRARY
    for m in DIVERSE_METHODS:
        c = replace(BASE, method=m)
        assert c.neutral in c.exit_lib and m in GRID and m in DEFAULT and FAMILY[m] in ("VOL", "XS", "CAL", "EV")


# --- research path ------------------------------------------------------------------------------------------
def test_xs_momentum_ranker_beats_random_on_persistent_drifts():
    fm, cache, dev, mem = _setup(*_drift_market())
    row = replace(BASE, method="xs_mom", rung="A0", fixed_threshold=6.0, max_positions=4, max_new_per_day=4)
    best = run_ladder(fm, cache, dev, mem, row)
    rand = run_ladder(fm, cache, dev, mem, replace(row, ranker="random"))
    short = run_ladder(fm, cache, dev, mem, replace(row, direction=-1))      # sell the weakest cross-section
    assert best.stats["expectancy"] > 2 * rand.stats["expectancy"] and best.stats["sharpe"] > 1.0
    assert best.stats["n"] > 100 and short.stats["expectancy"] > 0


@pytest.mark.parametrize("method", ["vol_spike", "squeeze", "tom"])
def test_new_rows_run_the_full_ladder(market, method):
    fm, cache, dev, mem = market
    for d in (1, -1) if method != "tom" else (1,):
        r = run_ladder(fm, cache, dev, mem, replace(BASE, method=method, direction=d, rung="A3"))
        assert r.stats["n"] > 20 and all(x["exit"] for x in r.decisions)


def test_diverse_catalogue_rows(market):
    rows = diverse_rows(BASE)
    assert {r.method for r in rows} == {"vol_spike", "squeeze", "xs_mom", "tom"} and len(rows) == 7
    assert all(r.max_positions >= 10 for r in rows if r.method == "xs_mom")
    full = diverse_rows(BASE, dividends=True, index_events=True)
    assert len(full) == 9 and next(r for r in full if r.method == "index_add").universe_mode == "top_liquidity"
    ids = {r.rid for r in full}
    assert "XS-XS_MOM-BUY-EQ" in ids and "CAL-TOM-BUY-EQ" in ids and "EV-POST_EXDIV-BUY-EQ" in ids
    fams = {taxonomy(r)["signal_type"] for r in full}
    assert fams == {"volatility state", "cross-sectional", "calendar", "event"}
    fm, cache, dev, mem = market
    for r in full:
        if r.method != "index_add":
            run_ladder(fm, cache, dev, mem, replace(r, rung="A1", universe_mode="membership"))


def test_live_orders_match_research_signals_for_context_methods(market):
    _, cache, _, _ = market
    for m in ("tom", "xs_mom", "post_exdiv"):
        cfg = replace(BASE, method=m)
        spec = EntrySpec(m, DEFAULT[m])
        t = cache.trades("S003", spec, cfg.neutral)
        day = t["signal_date"][len(t) // 2]
        be = BookEntry(cfg.rid, cfg, DEFAULT[m], cfg.neutral.id, (), ("S003",), {})
        orders = daily_orders(cache, {cfg.rid: be}, day, {})
        assert [o["symbol"] for o in orders] == ["S003"]
