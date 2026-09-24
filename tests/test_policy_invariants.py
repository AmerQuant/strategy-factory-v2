import numpy as np
import polars as pl
from conftest import CFG, build

from sfactory.policy.rsi_row import RowConfig, run_row
from sfactory.registry.repo import Registry


def test_future_perturbation_does_not_change_past_decisions():
    """Invariant #1: perturbing data on/after a DP must not change any decision at DPs <= that DP."""
    fm, cache, dev, mem = build("random_walk", seed=3)
    base = run_row(fm, cache, dev, mem, RowConfig(select=True))
    k = 5
    dp_k = fm.dev_folds()[k].dp
    rng = np.random.default_rng(0)

    def perturb(bars):
        noise = pl.Series(rng.uniform(0.8, 1.2, len(bars)))
        m = pl.when(pl.col("date") >= dp_k).then(noise).otherwise(1.0)
        return bars.with_columns([(pl.col(c) * m) for c in ["open", "high", "low", "close", "volume"]])

    fm2, cache2, dev2, mem2 = build("random_walk", seed=3, bars_transform=perturb)
    pert = run_row(fm2, cache2, dev2, mem2, RowConfig(select=True))
    for a, b in zip(base.decisions[: k + 1], pert.decisions[: k + 1]):
        assert a == b


def test_no_trade_touches_holdout(rw_market):
    fm, cache, dev, mem = rw_market
    res = run_row(fm, cache, dev, mem, RowConfig(select=True))
    assert res.oos_trades["exit_date"].max() < CFG.holdout_start


def test_random_walk_yields_no_edge_but_real_edge_is_found():
    for seed in (11, 12, 13):
        fm, cache, dev, mem = build("random_walk", seed=seed)
        assert run_row(fm, cache, dev, mem, RowConfig(select=True)).stats["t_stat"] < 2.0
    fm, cache, dev, mem = build("mean_revert", seed=11)
    assert run_row(fm, cache, dev, mem, RowConfig(select=True)).stats["t_stat"] > 3.0


def test_every_evaluated_config_is_registered(rw_market):
    fm, cache, dev, mem = rw_market
    reg = Registry()
    run_row(fm, cache, dev, mem, RowConfig(select=False), reg)
    run_row(fm, cache, dev, mem, RowConfig(select=True), reg)
    assert reg.count_trials("MR-RSI2-BUY-EQ") == 2
    n_folds = len(fm.dev_folds())
    assert reg.con.execute("select count(*) from fold_decisions").fetchone()[0] == 2 * n_folds


def test_compute_once_slice_many(rw_market):
    fm, cache, dev, mem = rw_market
    run_row(fm, cache, dev, mem, RowConfig(select=True))
    before = cache.computed
    run_row(fm, cache, dev, mem, RowConfig(select=True))
    assert cache.computed == before
