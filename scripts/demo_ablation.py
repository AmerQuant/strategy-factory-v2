"""Ablation ladder A0 -> A1 -> A3 -> A4 (+ structural market filter) on synthetic data, with DSR and PBO.

Run: uv run python scripts/demo_ablation.py [mean_revert|random_walk]
"""
from __future__ import annotations

import sys
from datetime import date

from sfactory.data.adjust import add_adj_factor
from sfactory.data.regime import market_up_series
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.ablation import run_ablation
from sfactory.policy.ladder import LadderConfig
from sfactory.signals.specs import STRUCTURAL_MARKET_UP
from sfactory.timeline.folds import FoldConfig, FoldManager


def main(kind: str = "mean_revert") -> dict:
    bars, divs, mem = make_market(30, 2600, seed=11, kind=kind)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(FoldConfig(date(2010, 1, 4), date(2013, 1, 1), date(2018, 1, 1), date(2020, 1, 1),
                                is_years=3))
    dev = fm.dev_view(bars)
    cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), f"syn-{kind}-11")
    d, f = market_up_series(dev)
    cache.set_market_regime(d, f, "eqw-ma200")
    base = LadderConfig(max_positions=10, max_new_per_day=3)
    out = {"ladder": run_ablation(fm, cache, dev, mem, base)}
    out["structural"] = run_ablation(fm, cache, dev, mem, LadderConfig(max_positions=10, max_new_per_day=3,
                                                                       structural=(STRUCTURAL_MARKET_UP,)),
                                     rungs=("A0", "A4"))
    return out


if __name__ == "__main__":
    res = main(sys.argv[1] if len(sys.argv) > 1 else "mean_revert")
    for name in ("ladder", "structural"):
        print(name, "PBO", round(res[name]["pbo"]["pbo"], 3))
        for row in res[name]["table"]:
            print("  ", {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()})
