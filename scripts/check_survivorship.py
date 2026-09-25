"""Survivorship check of the v1 store (docs/spec/survivorship.md).

    uv run python scripts/check_survivorship.py --store D:/AmerAndish/Projects/Trade/StrategyFactory_data/store \
        [--timeframe 1D] [--membership membership.parquet] [--out survivorship.json]

Prints the verdict and writes the full JSON report (counts, examples, per-year delistings, membership gaps).
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import polars as pl

from sfactory.data.sfac_store import load_store
from sfactory.data.survivorship import survivorship_report


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--timeframe", default="1D")
    ap.add_argument("--membership", help="parquet: symbol, start, end")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    load = load_store(a.store, a.timeframe)
    mem = pl.read_parquet(a.membership) if a.membership else None
    rep = survivorship_report(load.bars, mem)
    rep["store_version"] = load.version
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")
    print(json.dumps({k: rep[k] for k in ("verdict", "symbols", "years", "ended_early", "ended_early_per_year",
                                          "started_late", "advice")}, indent=1))
    return rep


if __name__ == "__main__":
    main()
