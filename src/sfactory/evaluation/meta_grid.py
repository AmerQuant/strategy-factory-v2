"""Meta-grid (design 8): a small, pre-registered set of policy-level settings (IS length, window type, capacity),
all evaluated and all reported. Every configuration's rows are trials in one registry, so DSR sees them all.

Reported: combined dev-OOS Sharpe per setting, the share of settings that beat the all-rows equal-weight
benchmark (sensitivity), and PBO/CSCV across settings (is the "best" setting just the luckiest?).
The meta-grid is never used to pick a winner by maximum; the pre-registered default setting is kept unless the
sensitivity report says the result depends on it.
"""
from __future__ import annotations

from dataclasses import replace
from itertools import product

import numpy as np

from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.portfolio.combine import combine_rows
from sfactory.registry.repo import Registry
from sfactory.stats.multiple import pbo_cscv
from sfactory.timeline.folds import FoldConfig, FoldManager


def run_meta_grid(base_cfg: FoldConfig, cache, bars_dev, membership, rows, is_years=(3, 4), anchored=(False,),
                  capacity=((5, 2), (10, 3)), registry: Registry | None = None, n_blocks: int = 8) -> dict:
    registry = registry or Registry()
    settings, combos, benches = [], [], []
    for iy, an, (mp, mn) in product(is_years, anchored, capacity):
        fm = FoldManager(replace(base_cfg, is_years=iy, anchored=an))
        cfg_rows = [replace(r, max_positions=mp, max_new_per_day=mn) for r in rows]
        rep = run_catalog(fm, cache, bars_dev, membership, cfg_rows, registry=registry, spa_boot=100)
        folds = fm.dev_folds()
        acc = rep["accepted"]
        daily = (combine_rows(rep["daily"][:, acc], rep["dates"], [f.dp for f in folds],
                              [f.oos_end for f in folds]).daily if acc else np.zeros(len(rep["dates"])))
        settings.append({"is_years": iy, "anchored": an, "max_positions": mp, "max_new_per_day": mn,
                         "accepted": len(acc), "combined_sharpe": rep["combined"]["sharpe"],
                         "benchmark_sharpe": rep["benchmark_all_equal"]})
        combos.append(daily)
        benches.append(rep["benchmark_all_equal"])
    mat = np.column_stack(combos)
    beat = float(np.mean([s["combined_sharpe"] > s["benchmark_sharpe"] for s in settings]))
    pbo = pbo_cscv(mat, n_blocks) if mat.shape[1] >= 2 and mat.std() > 0 else None
    return {"settings": settings, "share_beating_benchmark": beat, "pbo_across_settings": pbo,
            "trials_in_registry": registry.count_trials()}
