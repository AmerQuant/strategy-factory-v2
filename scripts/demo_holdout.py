"""End-to-end on synthetic data: catalogue -> screening -> combined policy -> pre-registered one-shot holdout
-> evidence package (JSON for the Persian report, see docs/templates/report_prompt_fa.md).

Run: uv run python scripts/demo_holdout.py [mean_revert|trending|random_walk] [out.json]
"""
from __future__ import annotations

import sys
from datetime import date

from sfactory.data.adjust import add_adj_factor
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.evaluation.evidence import build_evidence, dumps
from sfactory.evaluation.holdout import run_holdout
from sfactory.policy.catalog import CATALOG_VERSION, equity_rows
from sfactory.policy.ladder import LadderConfig
from sfactory.registry.repo import Registry
from sfactory.timeline.folds import FoldConfig, FoldManager


def main(kind: str = "mean_revert") -> dict:
    bars, divs, mem = make_market(30, 2600, seed=11, kind=kind)
    bars = add_adj_factor(bars, divs)
    fm = FoldManager(FoldConfig(date(2010, 1, 4), date(2013, 1, 1), date(2018, 1, 1), date(2020, 1, 1),
                                is_years=3))
    dv = f"syn-{kind}-11"
    dev = fm.dev_view(bars)
    dev_cache = TradeCache(prepare_arrays(dev, fm.dev_view(divs, "ex_date")), dv)
    reg = Registry()
    cat = run_catalog(fm, dev_cache, dev, mem, equity_rows(LadderConfig(max_positions=10, max_new_per_day=3)),
                      registry=reg, divs_dev=fm.dev_view(divs, "ex_date"))
    full_cache = TradeCache(prepare_arrays(bars, divs), dv + "-full")   # the only place the lock is lifted
    hold = run_holdout(fm, full_cache, bars, mem, cat, reg, dv)
    return build_evidence(cat, hold, {"data": dv, "catalog": CATALOG_VERSION, "universe": "synthetic 30"})


if __name__ == "__main__":
    pkg = main(sys.argv[1] if len(sys.argv) > 1 else "mean_revert")
    txt = dumps(pkg)
    if len(sys.argv) > 2:
        with open(sys.argv[2], "w", encoding="utf-8") as fh:
            fh.write(txt)
    h = pkg["holdout"]
    summary = {k: h["holdout"][k] for k in ("sharpe", "max_dd", "benchmark_sharpe")} if "holdout" in h else ""
    print("holdout:", h["status"], h.get("checks"), summary)
