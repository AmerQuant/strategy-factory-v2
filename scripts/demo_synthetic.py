"""End-to-end demo on synthetic data: row A0/A1 + stage-E benchmarks and rank IC.

Run: uv run python scripts/demo_synthetic.py [random_walk|mean_revert]
"""
from __future__ import annotations

import json
import sys
from datetime import date

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.benchmarks import evaluate_row
from sfactory.policy.grid import build_grid
from sfactory.policy.rsi_row import RowConfig, run_row
from sfactory.registry.repo import Registry
from sfactory.timeline.folds import FoldConfig, FoldManager


def main(kind: str = "mean_revert") -> dict:
    bars, divs, mem = make_market(30, 2600, seed=11, kind=kind)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(FoldConfig(date(2010, 1, 4), date(2013, 1, 1), date(2018, 1, 1), date(2020, 1, 1),
                                is_years=3))
    dev = fm.dev_view(bars)
    cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), f"syn-{kind}-11")
    reg = Registry()
    out = {}
    configs = {
        "A0": RowConfig(select=False),
        "A1": RowConfig(select=True),
        "A1_capacity": RowConfig(select=True, max_positions=10, max_new_per_day=3),
    }
    for name, cfg in configs.items():
        grid = build_grid(fm, cache, dev, mem, cfg)
        run_row(fm, cache, dev, mem, cfg, reg, grid=grid)
        out[name] = evaluate_row(grid, cfg, n_draws=200, n_trials=len(configs))
    out["trials_registered"] = reg.count_trials()
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "mean_revert")
    print(json.dumps(res, indent=1, default=lambda x: round(x, 3) if isinstance(x, float) else str(x)))
