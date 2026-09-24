"""Whole catalogue (18 rows) -> BH + two-path gate -> WF-native combined policy, on synthetic data.

Run: uv run python scripts/demo_catalog.py [mean_revert|trending|random_walk]
"""
from __future__ import annotations

import sys
from datetime import date

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig
from sfactory.timeline.folds import FoldConfig, FoldManager


def main(kind: str = "mean_revert") -> dict:
    bars, divs, mem = make_market(30, 2600, seed=11, kind=kind)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(FoldConfig(date(2010, 1, 4), date(2013, 1, 1), date(2018, 1, 1), date(2020, 1, 1),
                                is_years=3))
    dev = fm.dev_view(bars)
    cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), f"syn-{kind}-11")
    return run_catalog(fm, cache, dev, mem, equity_rows(LadderConfig(max_positions=10, max_new_per_day=3)))


if __name__ == "__main__":
    r = main(sys.argv[1] if len(sys.argv) > 1 else "mean_revert")
    print(f"trials={r['n_trials']} accepted={len(r['accepted'])} combined_sharpe={r['combined']['sharpe']:.2f} "
          f"effective_n={r['combined']['effective_n']:.2f} all_rows_equal={r['benchmark_all_equal']:.2f}")
    for t in r["table"]:
        print(f"  {t['row']:<28} sharpe={t['sharpe']:6.2f} p={t['p']:.3f} bh={t['bh']!s:<5} dsr={t['dsr']:.2f} "
              f"path={t['path']}")
