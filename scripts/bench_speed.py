"""Speed benchmark for ADR-0004 (run on the owner's machine; the CI container has one core).

    uv run python scripts/bench_speed.py --symbols 1000 --workers 1,4,8
    uv run python scripts/bench_speed.py --store <StrategyFactory_data/store> --workers 1,8

Times, per worker count: the parallel precompute of the 18-row catalogue settings (A1, capacity 10) and the
research run that follows (all cache hits), on a fresh in-memory cache each time. Prints JSON.
"""
from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import replace
from datetime import date

import polars as pl

from sfactory.data.adjust import add_adj_factor
from sfactory.data.contracts import DIVIDENDS_SCHEMA
from sfactory.data.folds_from_data import fold_config_for
from sfactory.data.synthetic import make_market
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.engine.parallel import precompute
from sfactory.policy.catalog import equity_rows
from sfactory.policy.ladder import LadderConfig, run_ladder
from sfactory.timeline.folds import FoldConfig, FoldManager


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", type=int, default=500)
    ap.add_argument("--store", help="v1 store root: benchmark on real daily bars instead of synthetic ones")
    ap.add_argument("--workers", default=f"1,{os.cpu_count()}")
    ap.add_argument("--rows", type=int, default=18)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    if a.store:
        from sfactory.data.sfac_store import load_store
        bars = load_store(a.store).bars
        divs, mem = pl.DataFrame(schema=DIVIDENDS_SCHEMA), None      # top-liquidity universe, as run_real
        fm = FoldManager(fold_config_for(bars["date"].min(), bars["date"].max()))
    else:
        bars, divs, mem = make_market(a.symbols, 2600, seed=1, kind="mean_revert")
        fm = FoldManager(FoldConfig(date(2010, 1, 4), date(2013, 1, 1), date(2018, 1, 1), date(2020, 1, 1),
                                    is_years=3))
    bars = add_adj_factor(bars, divs)
    dev = fm.dev_view(bars)
    arrays = prepare_arrays(dev, fm.dev_view(divs, "ex_date"))
    mode = "top_liquidity" if mem is None else "membership"
    rows = equity_rows(LadderConfig(rung="A1", max_positions=10, universe_mode=mode))[: a.rows]
    out = {"symbols": len(arrays), "rows": len(rows), "load_seconds": round(time.perf_counter() - t0, 2),
           "cpu_count": os.cpu_count(), "runs": []}
    for w in [int(x) for x in a.workers.split(",")]:
        cache = TradeCache(arrays, f"bench-{w}")
        pre = precompute(cache, rows, n_workers=w)
        t = time.perf_counter()
        for r in rows:
            run_ladder(fm, cache, dev, mem, replace(r))
        out["runs"].append({"workers": w, "precompute_seconds": pre["seconds"], "frames": pre["frames_added"],
                            "research_seconds": round(time.perf_counter() - t, 2),
                            "cache_misses_during_research": cache.computed})
    print(json.dumps(out, indent=1))
    return out


if __name__ == "__main__":
    main()
