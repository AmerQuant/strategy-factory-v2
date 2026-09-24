import numpy as np
import polars as pl
from conftest import build

from sfactory.forward.live import book_diff, daily_orders, decide_book
from sfactory.forward.monitor import (
    ForwardRules,
    cusum_lower,
    evaluate_forward,
    implementation_shortfall,
    mc_bands,
)
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.signals.methods import EntrySpec

CFGS = [LadderConfig(rung="A4", method="rsi"), LadderConfig(rung="A3", method="donchian_break", direction=-1)]


def test_live_dp_reproduces_research_decision():
    fm, cache, dev, mem = build("mean_revert", seed=11)
    k = 6
    dp = fm.dev_folds()[k].dp
    book = decide_book(fm, cache, dev.filter(pl.col("date") < dp), mem, CFGS, dp)
    for cfg in CFGS:
        research = run_ladder(fm, cache, dev, mem, cfg).decisions[k]
        live = dict(book[cfg.rid].decision)
        research.pop("fold")
        live.pop("fold")
        assert live == research


def test_book_diff_reports_changes():
    fm, cache, dev, mem = build("mean_revert", seed=11)
    d1, d2 = fm.dev_folds()[3].dp, fm.dev_folds()[4].dp
    b1 = decide_book(fm, cache, dev, mem, CFGS, d1)
    b2 = decide_book(fm, cache, dev, mem, CFGS[:1], d2)
    diff = book_diff(b1, b2)
    assert diff["removed"] == [CFGS[1].rid] and diff["added"] == [] and diff["kept"] == [CFGS[0].rid]


def test_daily_orders_match_engine_signals():
    fm, cache, dev, mem = build("mean_revert", seed=11)
    dp = fm.dev_folds()[5].dp
    cfg = LadderConfig(rung="A0", method="rsi")
    book = decide_book(fm, cache, dev, mem, [cfg], dp)
    be = book[cfg.rid]
    trades = pl.concat([cache.trades(s, EntrySpec("rsi", be.threshold), cfg.neutral) for s in be.eligible])
    day = trades.filter(pl.col("signal_date") >= dp)["signal_date"].min()
    expected = set(trades.filter(pl.col("signal_date") == day)["symbol"].to_list())
    got = {o["symbol"] for o in daily_orders(cache, book, day, {})}
    assert expected <= got
    capped = LadderConfig(rung="A0", method="rsi", max_positions=3, max_new_per_day=1)
    b = decide_book(fm, cache, dev, mem, [capped], dp)
    assert len(daily_orders(cache, b, day, {capped.rid: {"S001", "S002"}})) <= 1


def test_bands_cusum_shortfall_and_rules():
    rng = np.random.default_rng(0)
    dev = rng.normal(100, 1000, 1500)
    bands = mc_bands(dev, 250, n_boot=500)
    assert np.all(bands["cum"][5] <= bands["cum"][50]) and np.all(bands["cum"][50] <= bands["cum"][95])
    assert np.all(bands["dd"][99] >= bands["dd"][50])
    alarms = sum(cusum_lower(rng.normal(1, 1, 300), 1, 1) is not None for _ in range(200))
    assert alarms <= 10
    assert cusum_lower(rng.normal(-1, 1, 300), 1, 1) is not None
    bt = pl.DataFrame({"symbol": ["A", "B"], "signal_date": [1, 2], "net_pnl": [100.0, 60.0]})
    lv = bt.with_columns(pl.col("net_pnl") - 20)
    sf = implementation_shortfall(lv, bt)
    assert sf["matched"] == 2 and sf["mean_shortfall"] == 20 and abs(sf["shortfall_frac"] - 0.25) < 1e-9
    r = ForwardRules(min_days=100)
    good = evaluate_forward(rng.normal(150, 800, 150), rng.normal(1, 1, 60), bands, sf, 1, 1, r)
    assert good["status"] == "promote"
    crash = evaluate_forward(np.r_[np.zeros(20), [-60_000.0]], np.ones(5), bands, {"matched": 0}, 1, 1, r)
    assert crash["status"] == "stop"
    early = evaluate_forward(rng.normal(100, 800, 20), rng.normal(1, 1, 5), bands, {"matched": 0}, 1, 1, r)
    assert early["status"] == "continue"
