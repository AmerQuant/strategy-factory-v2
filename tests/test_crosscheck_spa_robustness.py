import numpy as np
import pytest
from conftest import CFG, build

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.robustness import run_robustness
from sfactory.policy.ladder import LadderConfig
from sfactory.signals.methods import EntrySpec
from sfactory.signals.specs import NEUTRAL_MR_EXIT, ExitSpec
from sfactory.stats.multiple import spa_test
from sfactory.timeline.folds import FoldManager


def test_entry_delay_shifts_fills_by_one_bar(rw_market):
    _, cache, _, _ = rw_market
    a = cache.trades("S001", EntrySpec("rsi", 10), NEUTRAL_MR_EXIT)
    b = cache.trades("S001", EntrySpec("rsi", 10), NEUTRAL_MR_EXIT, delay=1)
    dates = list(cache.arrays["S001"].dates)
    first_a = dates.index(np.datetime64(a["signal_date"][0]))
    first_b = dates.index(np.datetime64(b["signal_date"][0]))
    assert first_b == first_a + 1


def test_cell_engine_matches_vectorbt():
    vbt = pytest.importorskip("vectorbt")
    _, cache, _, _ = build("mean_revert", seed=5)
    a = cache.arrays["S003"]
    ours = cache.trades("S003", EntrySpec("rsi", 15), ExitSpec("prev_high", 0, 10_000))
    ours = ours.filter(~ours["forced_exit"])
    from sfactory.signals.indicators import rsi_wilder
    r = rsi_wilder(a.sig_close, 2)
    entry = np.where(np.isnan(r), False, r < 15)
    exit_ = np.zeros(len(entry), dtype=bool)
    exit_[1:] = a.sig_close[1:] > a.sig_high[:-1]
    ent_fill = np.r_[False, entry[:-1]]      # signal on close t -> fill at open t+1
    ex_fill = np.r_[False, exit_[:-1]]
    pos = False                               # resolve same-bar conflicts only; fills/pnl/fees are vectorbt's
    for b in range(len(ent_fill)):
        if pos:
            ent_fill[b] = False
            if ex_fill[b]:
                pos = False
        else:
            ex_fill[b] = False
            if ent_fill[b]:
                pos = True
    pf = vbt.Portfolio.from_signals(a.ex_open, ent_fill, ex_fill, price=a.ex_open, size=100_000.0,
                                    size_type="value", fees=5e-4, init_cash=1e12)
    rec = pf.trades.records_readable
    rec = rec[rec["Status"] == "Closed"]
    n = min(len(rec), len(ours))
    assert n > 20
    assert np.array_equal(rec["Entry Timestamp"].to_numpy()[:n], np.arange(len(a.dates))[
        [list(a.dates).index(np.datetime64(d)) for d in ours["entry_date"].to_list()[:n]]])
    ours_ex_div = (ours["net_pnl"] - ours["dividends"]).to_numpy()[:n]   # vectorbt has no dividend cash flows
    assert np.allclose(rec["PnL"].to_numpy()[:n], ours_ex_div, rtol=1e-6, atol=1e-6)


def test_spa_rejects_only_when_a_candidate_is_better():
    rng = np.random.default_rng(0)
    bench = rng.normal(0, 1, 1500)
    noise = rng.normal(0, 1, (1500, 15))
    assert spa_test(noise, bench, n_boot=300)["p_value"] > 0.05
    better = noise.copy()
    better[:, 4] += 0.25
    res = spa_test(better, bench, n_boot=300)
    assert res["p_value"] < 0.05 and res["best"] == 4


def test_robustness_suite_on_real_edge_and_on_noise():
    bars, divs, mem = make_market(20, 2600, seed=11, kind="mean_revert")
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(CFG)
    dev, ddev = fm.dev_view(bars), fm.dev_view(divs, "ex_date")
    cache = TradeCache(prepare_arrays(dev, ddev), "syn-mr-11")
    cfg = LadderConfig(rung="A1", method="ibs", max_positions=5, max_new_per_day=2)
    rep = run_robustness(fm, cache, dev, ddev, mem, cfg)
    assert rep["passed"] and rep["mandatory"]["cost_x1.5_profitable"]
    assert set(rep["regime"]) == {"market_up", "market_down"}
    bars, divs, mem = make_market(20, 2600, seed=11, kind="random_walk")
    bars = add_adj_factor(bars, divs)
    dev, ddev = fm.dev_view(bars), fm.dev_view(divs, "ex_date")
    rw = run_robustness(fm, TradeCache(prepare_arrays(dev, ddev), "syn-rw-11"), dev, ddev, mem, cfg)
    assert not rw["passed"]
