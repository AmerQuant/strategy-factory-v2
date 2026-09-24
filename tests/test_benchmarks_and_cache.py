import numpy as np
from conftest import build

from sfactory.engine.cache import TradeCache
from sfactory.evaluation.benchmarks import (
    evaluate_row,
    frozen_first,
    grid_ensemble,
    random_choice,
    rank_ic,
    spearman,
)
from sfactory.policy.grid import build_grid
from sfactory.policy.rsi_row import RowConfig, run_row


def test_spearman_basics():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == 1.0
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == -1.0
    assert np.isnan(spearman([1, np.nan, 3], [1, 2, np.nan]))


def test_benchmarks_are_consistent_and_deterministic():
    cfg = RowConfig(select=True)
    fm, cache, dev, mem = build("mean_revert", seed=11)
    grid = build_grid(fm, cache, dev, mem, cfg)
    pol = run_row(fm, cache, dev, mem, cfg, grid=grid)
    ens = grid_ensemble(grid, cfg)
    assert ens["n"] == sum(len(c.oos[t]) for c in grid for t in cfg.thresholds)
    fz = frozen_first(grid, cfg)
    assert fz["threshold"] == pol.decisions[0]["threshold"]
    a = random_choice(grid, cfg, 30, seed=5)
    b = random_choice(grid, cfg, 30, seed=5)
    assert np.array_equal(a, b) and len(a) == 30
    ic = rank_ic(grid, cfg)
    assert all(np.isnan(x) or -1 <= x <= 1 for x in ic["per_fold"])
    rep = evaluate_row(grid, cfg, n_draws=30)
    assert 0 <= rep["random_choice"]["policy_percentile"] <= 100
    assert rep["policy"] == pol.stats


def test_grid_reuse_does_not_recompute(rw_market):
    fm, cache, dev, mem = rw_market
    cfg = RowConfig(select=True)
    grid = build_grid(fm, cache, dev, mem, cfg)
    before = cache.computed
    evaluate_row(grid, cfg, n_draws=20)
    assert cache.computed == before


def test_parquet_cache_roundtrip(tmp_path):
    _, cache, _, _ = build("random_walk", seed=4)
    c1 = TradeCache(cache.arrays, cache.data_version, cache_dir=tmp_path)
    t1 = c1.rsi_mr("S001", 2, 10.0)
    c2 = TradeCache(cache.arrays, cache.data_version, cache_dir=tmp_path)
    t2 = c2.rsi_mr("S001", 2, 10.0)
    assert c1.computed == 1 and c2.computed == 0 and c2.loaded == 1
    assert t1.equals(t2)
    c3 = TradeCache(cache.arrays, "other-version", cache_dir=tmp_path)
    c3.rsi_mr("S001", 2, 10.0)
    assert c3.computed == 1  # a new data version never reuses stale cache
