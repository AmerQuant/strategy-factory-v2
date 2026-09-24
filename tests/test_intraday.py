from dataclasses import replace
from datetime import date, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from sfactory.costs.model import CostModel, SymbolCost
from sfactory.data.adjust import add_adj_factor
from sfactory.data.folds_from_data import fold_config_for
from sfactory.data.resample import alignment_variants, check_no_straddle, resample_bars, to_daily
from sfactory.data.sfac_store import load_store
from sfactory.data.synthetic import make_intraday_market
from sfactory.data.universe import daily_view, eligible_at, eligible_top_liquidity
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.ablation import aligned_daily
from sfactory.evaluation.robustness import regime_breakdown
from sfactory.metrics.core import daily_pnl
from sfactory.policy.edge_state import ActivationConfig, run_activation
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.signals.methods import EntrySpec
from sfactory.signals.specs import ExitSpec
from sfactory.timeline.folds import FoldConfig, FoldManager

CFG_H = FoldConfig(data_start=date(2010, 1, 4), first_dp=date(2013, 1, 1), holdout_start=date(2018, 1, 1),
                   data_end=date(2020, 1, 1), dp_months=6, is_years=3)


def dt(*a) -> datetime:
    """Naive timestamp in the run clock (the intraday convention)."""
    return datetime(*a)  # noqa: DTZ001


NO_DIVS = pl.DataFrame(schema={"symbol": pl.Utf8, "ex_date": pl.Date, "amount": pl.Float64})
ROW_H = LadderConfig(rung="A1", method="rsi", direction=1, min_price=0.0, min_dollar_vol=0.0)


def _hourly(sym="A", day=date(2020, 3, 2), n_days=3, first=14, per_day=7, start_px=100.0, step=0.1):
    rows, px = [], start_px
    for k in range(n_days):
        d = day + timedelta(days=k)
        for h in range(per_day):
            ts = dt(d.year, d.month, d.day) + timedelta(hours=first + h)
            rows.append({"symbol": sym, "date": ts, "open": px, "high": px + 0.5, "low": px - 0.5,
                         "close": px + step, "volume": 100.0})
            px += step
    return pl.DataFrame(rows).with_columns(pl.col("date").cast(pl.Datetime("us")))


def _build(bars, divs, mem, tag, cost=None):
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG_H)
    dev = fm.dev_view(bars)
    return fm, TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), tag, cost_bps=2.0,
                          cost_model=cost), dev, mem


@pytest.fixture(scope="module")
def mr_hourly():
    bars, divs, mem = make_intraday_market(10, 2600, bars_per_day=7, seed=2, kind="mean_revert", mr_strength=0.3)
    return _build(bars, divs, mem, "h-mr")


# --- resampling and clock ------------------------------------------------------------------------------
def test_resample_4h_ohlcv_and_daily():
    b = _hourly(first=12, per_day=8, n_days=2)                       # 12:00 .. 19:00
    four = resample_bars(b, "4h")
    assert four["date"].dt.hour().to_list() == [12, 16, 12, 16]
    g = b.filter((pl.col("date") >= dt(2020, 3, 2, 12)) & (pl.col("date") < dt(2020, 3, 2, 16)))
    first = four.row(0, named=True)
    assert first["open"] == g["open"][0] and first["close"] == g["close"][-1]
    assert first["high"] == g["high"].max() and first["low"] == g["low"].min() and first["volume"] == 400.0
    d = to_daily(b)
    assert d["date"].to_list() == [date(2020, 3, 2), date(2020, 3, 3)] and d["open"][0] == b["open"][0]


def test_straddle_check_and_clock_shift():
    b = _hourly(first=20, per_day=8, n_days=3)                      # 20:00 .. 03:00 next day
    check_no_straddle(b, "1h")                                       # hourly never straddles midnight
    four = resample_bars(b, "4h")                                    # windows at 20,00 -> fine
    check_no_straddle(four, "4h")
    with pytest.raises(ValueError):
        check_no_straddle(b.with_columns(pl.col("date") - timedelta(hours=2)), "4h")  # 18..22, 22..02
    v = alignment_variants(b, "4h", ("0h", "1h", "2h", "3h"))
    assert len({tuple(x["close"].to_list()) for x in v.values()}) > 1   # alignment changes the bars
    for x in v.values():
        check_no_straddle(x, "4h")


# --- adjustment, dividends, swap ------------------------------------------------------------------------
def test_intraday_dividend_adjustment_and_credit():
    b = _hourly(n_days=3, step=0.0)                                  # flat 100
    ex = date(2020, 3, 3)
    b = b.with_columns(pl.when(pl.col("date").cast(pl.Date) >= ex).then(pl.col(c) - 1.0).otherwise(pl.col(c))
                       .alias(c) for c in ("open", "high", "low", "close"))
    divs = pl.DataFrame({"symbol": ["A"], "ex_date": [ex], "amount": [1.0]})
    adj = add_adj_factor(b, divs)
    f = adj["adj_factor"].to_numpy()
    first_ex = int(np.argmax(adj["date"].cast(pl.Date).to_numpy() == np.datetime64(ex)))
    assert np.allclose(f[:first_ex], 0.99) and np.allclose(f[first_ex:], 1.0)
    sig = adj["close"] * adj["adj_factor"]
    assert np.allclose(sig.to_numpy(), sig[0])                        # no ex-date artefact on the signal series
    arr = prepare_arrays(adj, divs)["A"]
    assert arr.div[first_ex] == 1.0 and arr.div.sum() == 1.0


def test_intraday_trades_keep_datetime_and_swap_counts_rollovers():
    b = add_adj_factor(_hourly(n_days=6, step=-0.05), NO_DIVS)
    cm = CostModel(SymbolCost(0, 1, 0, -7.2, -3.6))
    c = TradeCache(prepare_arrays(b, NO_DIVS), "v", cost_model=cm)
    t = c.trades("A", EntrySpec("rsi", 50.0), ExitSpec("time", 3, 3))
    assert t.schema["entry_date"] == pl.Datetime("us") and len(t) > 0
    nights = (t["exit_date"].cast(pl.Date) - t["entry_date"].cast(pl.Date)).dt.total_days().to_numpy()
    assert (nights == 0).any()                                        # same-day trades pay no swap
    expect = -(t["shares"] * t["entry_px"]).to_numpy() * 0.072 / 360 * nights
    assert np.allclose(t["financing"].to_numpy(), expect)


def test_daily_pnl_sums_intraday_exits_per_day():
    b = add_adj_factor(_hourly(n_days=5, step=0.02), NO_DIVS)
    t = TradeCache(prepare_arrays(b, NO_DIVS), "v").trades("A", EntrySpec("ibs", 0.9), ExitSpec("time", 1, 1))
    dp = daily_pnl(t)
    assert np.isclose(dp.sum(), t["net_pnl"].sum()) and len(dp) <= 5
    m = aligned_daily([t], date(2020, 3, 2), date(2020, 3, 6))
    assert np.isclose(m.sum(), t["net_pnl"].sum())


# --- time slicing, folds, universe ---------------------------------------------------------------------
def test_foldmanager_slices_datetime_trades():
    fm = FoldManager(CFG_H)
    f = fm.dev_folds()[0]
    t = pl.DataFrame({"signal_date": [dt(2012, 12, 31, 20), dt(2013, 1, 2, 15)],
                      "entry_date": [dt(2012, 12, 31, 21), dt(2013, 1, 2, 16)],
                      "exit_date": [dt(2013, 1, 2, 14), dt(2013, 1, 3, 15)], "net_pnl": [1.0, 2.0]})
    assert len(fm.slice_is(t, f)) == 0                                # exits after the DP are purged
    assert fm.slice_oos(t, f)["net_pnl"].to_list() == [2.0]


def test_folds_from_datetime_span():
    c = fold_config_for(dt(2005, 1, 3, 14), dt(2026, 7, 31, 20))
    assert c.first_dp == date(2008, 2, 1) and isinstance(c.data_start, date) and c.data_end == date(2026, 8, 1)
    FoldManager(c)


def test_universe_on_intraday_equals_daily_view_and_is_causal():
    bars, _, mem = make_intraday_market(6, 400, bars_per_day=7, seed=4)
    dv = daily_view(bars)
    dp = date(2011, 1, 3)
    a = eligible_at(dp, bars, mem, min_price=0.0, min_dollar_vol=0.0, min_history=200)
    assert a == eligible_at(dp, dv, mem, min_price=0.0, min_dollar_vol=0.0, min_history=200) and len(a) == 6
    pert = bars.with_columns(pl.when(pl.col("date") >= dt(2011, 1, 3)).then(pl.col("volume") * 50)
                             .otherwise(pl.col("volume")))
    assert (eligible_top_liquidity(dp, bars, 3, 0.0, min_history=200)
            == eligible_top_liquidity(dp, pert, 3, 0.0, min_history=200))


# --- the research path on hourly bars -------------------------------------------------------------------
def test_hourly_mr_row_finds_the_edge_and_random_walk_does_not(mr_hourly):
    fm, cache, dev, mem = mr_hourly
    r = run_ladder(fm, cache, dev, mem, ROW_H)
    assert r.stats["sharpe"] > 1.0 and r.oos_trades.schema["signal_date"] == pl.Datetime("us")
    bars, divs, mem2 = make_intraday_market(10, 2600, bars_per_day=7, seed=2, kind="random_walk")
    fm2, c2, dev2, _ = _build(bars, divs, mem2, "h-rw")
    assert run_ladder(fm2, c2, dev2, mem2, ROW_H).stats["sharpe"] < 0.5


def test_hourly_future_perturbation():
    bars, divs, mem = make_intraday_market(6, 2000, bars_per_day=7, seed=6, kind="mean_revert")
    cut = dt(2014, 7, 1)
    rng = np.random.default_rng(0)
    noise = pl.Series(np.exp(rng.normal(0, 0.01, len(bars))))
    pert = bars.with_columns([pl.when(pl.col("date") >= cut).then(pl.col(c) * noise).otherwise(pl.col(c)).alias(c)
                              for c in ("open", "high", "low", "close")])
    pert = pert.with_columns(pl.max_horizontal("open", "high", "close").alias("high"),
                             pl.min_horizontal("open", "low", "close").alias("low"))
    a = run_activation(*_build(bars, divs, mem, "a"), ROW_H, ActivationConfig("two_clock"))
    b = run_activation(*_build(pert, divs, mem, "b"), ROW_H, ActivationConfig("two_clock"))

    def past(res):
        out = []
        for d in res.decisions:
            if date.fromisoformat(d["dp"]) <= cut.date():
                out.append((d["threshold"], d["n_eligible"]))
            out += [s for s in d["sub_dps"] if date.fromisoformat(s["dp"]) <= cut.date()]
        return out

    assert past(a) == past(b) and len(past(a)) > 5


def test_edge_state_always_equals_ladder_on_hourly(mr_hourly):
    fm, cache, dev, mem = mr_hourly
    a = run_activation(fm, cache, dev, mem, ROW_H, ActivationConfig("always"))
    b = run_ladder(fm, cache, dev, mem, ROW_H)
    k = ["symbol", "signal_date"]
    assert a.oos_trades.sort(k)["net_pnl"].to_list() == b.oos_trades.sort(k)["net_pnl"].to_list()


def test_resampled_4h_runs_the_same_pipeline(mr_hourly):
    bars, divs, mem = make_intraday_market(6, 2600, bars_per_day=8, first_hour=12, seed=3, kind="mean_revert",
                                           mr_strength=0.3)
    four = resample_bars(bars, "4h")
    check_no_straddle(four, "4h")
    fm, cache, dev, _ = _build(four, divs, mem, "h4")
    r = run_ladder(fm, cache, dev, mem, replace(ROW_H, rung="A0"))
    assert r.stats["n"] > 100


def test_regime_breakdown_joins_intraday_labels(mr_hourly):
    fm, cache, dev, mem = mr_hourly
    t = run_ladder(fm, cache, dev, mem, ROW_H).oos_trades
    d = dev["date"].unique().sort().to_numpy()
    out = regime_breakdown(t, d, np.ones(len(d), bool))
    assert out["market_up"]["n"] == len(t)


# --- v1 store, hourly ----------------------------------------------------------------------------------
def test_v1_store_reads_hourly_tz_aware_as_naive_utc(tmp_path):
    b = _hourly(n_days=2)
    h = "a" * 64
    d = tmp_path / "alpaca_sip" / "A" / "1H"
    d.mkdir(parents=True)
    b.select(pl.col("date").dt.replace_time_zone("UTC").alias("ts"), "open", "high", "low", "close",
             "volume").write_parquet(d / f"{h}.parquet")
    pl.DataFrame([{"source": "alpaca_sip", "symbol": "A", "asset_class": "us_equity", "timeframe": "1H",
                   "adjustment": "split", "snapshot_hash": h, "is_reference": True, "quality_status": "ok"}]
                 ).write_parquet(tmp_path / "catalog.parquet")
    ld = load_store(tmp_path, "1H")
    assert ld.bars.schema["date"] == pl.Datetime("us") and ld.bars["date"].to_list() == b["date"].to_list()


def test_capacity_daily_budget_is_shared_by_intraday_bars():
    from sfactory.portfolio.capacity import simulate_capacity
    rows = []
    for h in range(4):
        for s in ("A", "B"):
            ts = dt(2020, 3, 2, 14 + h)
            rows.append({"symbol": f"{s}{h}", "entry_date": ts, "exit_date": ts + timedelta(days=2),
                         "net_pnl": 1.0, "score": 0.0})
    c = pl.DataFrame(rows)
    out = simulate_capacity(c, max_positions=10, max_new_per_day=3, capital=100_000, cache_notional=10_000)
    assert len(out) == 3                                             # not 3 per hourly bar
