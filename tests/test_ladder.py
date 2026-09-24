import numpy as np
import polars as pl
from conftest import build

from sfactory.policy.ladder import LadderConfig, random_removal_pct, run_ladder, years_improved
from sfactory.registry.repo import Registry


def test_random_removal_and_years_helpers():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 1, 500)
    assert random_removal_pct(x, 300, 10.0, 100, 0) == 1.0
    assert random_removal_pct(x, 300, -10.0, 100, 0) == 0.0
    from datetime import date
    base = pl.DataFrame({"entry_date": [date(2015, 1, 5), date(2016, 1, 5)], "net_pnl": [1.0, 1.0]})
    filt = pl.DataFrame({"entry_date": [date(2015, 1, 5), date(2016, 1, 5)], "net_pnl": [2.0, 0.5]})
    assert years_improved(base, filt) == 0.5


def test_ladder_rungs_record_decisions():
    fm, cache, dev, mem = build("mean_revert", seed=11)
    reg = Registry()
    r0 = run_ladder(fm, cache, dev, mem, LadderConfig(rung="A0"), reg)
    r4 = run_ladder(fm, cache, dev, mem, LadderConfig(rung="A4"), reg)
    assert all(d["threshold"] == 10.0 and d["exit"].startswith("prev_high") for d in r0.decisions)
    assert all({"exit", "filters", "entry_scores"} <= d.keys() for d in r4.decisions)
    assert reg.count_trials() == 2
    assert r4.stats["n"] > 0


def test_ladder_future_perturbation_A4():
    fm, cache, dev, mem = build("random_walk", seed=3)
    cfg = LadderConfig(rung="A4")
    base = run_ladder(fm, cache, dev, mem, cfg)
    k = 5
    dp_k = fm.dev_folds()[k].dp
    rng = np.random.default_rng(1)

    def perturb(bars):
        noise = pl.Series(rng.uniform(0.8, 1.2, len(bars)))
        m = pl.when(pl.col("date") >= dp_k).then(noise).otherwise(1.0)
        return bars.with_columns([(pl.col(c) * m) for c in ["open", "high", "low", "close", "volume"]])

    fm2, cache2, dev2, mem2 = build("random_walk", seed=3, bars_transform=perturb)
    pert = run_ladder(fm2, cache2, dev2, mem2, cfg)
    assert base.decisions[: k + 1] == pert.decisions[: k + 1]


def test_ladder_finds_no_edge_on_random_walk():
    for seed in (11, 12):
        fm, cache, dev, mem = build("random_walk", seed=seed)
        assert run_ladder(fm, cache, dev, mem, LadderConfig(rung="A4")).stats["t_stat"] < 2.0
