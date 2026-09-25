"""End-to-end research run on real data from the v1 store (StrategyFactory_data/store = SFAC_DATA_ROOT).

    uv run python scripts/run_real.py --store D:/AmerAndish/Projects/Trade/StrategyFactory_data/store \
        --out D:/AmerAndish/Projects/Trade/sf2_runs/run1 --registry D:/AmerAndish/Projects/Trade/sf2_runs/registry.duckdb

load -> audit (critical symbols excluded) -> optional dividends / membership -> folds from the data span
(holdout = last 20 %, >= 18 months) -> 18-row catalogue (+ `--diverse` family rows) + family ensembles ->
screening + robustness -> combined policy -> [--open-holdout: the one-shot holdout] -> evidence.json + report.html.

The registry file must be the same across runs: every run adds its trials there and DSR counts them all.
Without --membership the universe is the top-N by trailing dollar volume at each DP (point-in-time).

Intraday: `--timeframe 1H` reads the hourly store; `--resample 4h` builds 4H bars from it and `--clock-shift 7h`
moves the clock to a broker day (17:00 New York = 00:00). Resampled / shifted data is a new data version.

`--analyze-accepted` adds the edge on/off and sizing ablations of every accepted row to the evidence (candidates
for the next frozen policy; nothing is switched on automatically).

Speed: `--workers 0` precomputes the trade cache on all cores first (ADR-0004); results do not depend on it.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import replace
from pathlib import Path

import polars as pl

from sfactory.costs.model import CostModel, load_cost_overrides
from sfactory.data.adjust import add_adj_factor
from sfactory.data.audit import audit_bars, audit_summary
from sfactory.data.contracts import DIVIDENDS_SCHEMA
from sfactory.data.folds_from_data import fold_config_for
from sfactory.data.regime import market_up_series
from sfactory.data.resample import check_no_straddle, resample_bars, shift_clock
from sfactory.data.sfac_store import load_store
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.engine.parallel import precompute
from sfactory.evaluation.catalog_runner import run_catalog
from sfactory.evaluation.evidence import build_evidence, dumps
from sfactory.evaluation.holdout import run_holdout
from sfactory.evaluation.row_analysis import analyze_rows, summarize
from sfactory.policy.catalog import (
    CATALOG_VERSION,
    DIVERSE_CATALOG_VERSION,
    DIVERSE_METHODS,
    MR_METHODS,
    TF_METHODS,
    diverse_rows,
    ensemble_rows,
    equity_rows,
)
from sfactory.policy.ladder import LadderConfig
from sfactory.registry.repo import Registry
from sfactory.report.html import render_html
from sfactory.timeline.folds import FoldManager


def parse(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--store", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--registry", required=True, help="persistent DuckDB file shared by all runs")
    ap.add_argument("--timeframe", default="1D")
    ap.add_argument("--resample", help="intraday only: build bars of this length from --timeframe, e.g. 4h")
    ap.add_argument("--clock-shift", default="0h", help="intraday only: e.g. 7h puts 17:00 New York at 00:00")
    ap.add_argument("--symbols", help="text file, one symbol per line (e.g. the broker's tradable list)")
    ap.add_argument("--top-n", type=int, default=500)
    ap.add_argument("--membership", help="parquet: symbol, start, end (point-in-time index membership)")
    ap.add_argument("--dividends", help="parquet: symbol, ex_date, amount (split-only basis)")
    ap.add_argument("--costs", default="moneta", help="'moneta' (share-CFD proxy), 'flat5', or a CSV path")
    ap.add_argument("--methods", help="comma list to restrict the catalogue (default: all 9)")
    ap.add_argument("--diverse", action="store_true",
                    help="add the diverse-family rows (VOL, XS, CAL; EV rows with --dividends / --membership)")
    ap.add_argument("--rung", default="A1")
    ap.add_argument("--max-positions", type=int, default=10)
    ap.add_argument("--max-new", type=int, default=3)
    ap.add_argument("--no-ensembles", action="store_true")
    ap.add_argument("--no-robustness", action="store_true")
    ap.add_argument("--open-holdout", action="store_true", help="burns the holdout for this data version")
    ap.add_argument("--analyze-accepted", action="store_true",
                    help="edge on/off and sizing ablations for every accepted row (registry trials; evidence)")
    ap.add_argument("--workers", type=int, default=1,
                    help="processes for the trade-cache precompute (0 = all cores); results do not depend on it")
    return ap.parse_args(argv)


def main(argv=None) -> dict:
    a = parse(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    symbols = [s.strip() for s in Path(a.symbols).read_text(encoding="utf-8").split() if s.strip()] if a.symbols else None
    load = load_store(a.store, a.timeframe, symbols=symbols)
    raw, version = load.bars, load.version
    if a.timeframe != "1D":
        if a.resample:
            raw = resample_bars(raw, a.resample, a.clock_shift)
        else:
            raw = shift_clock(raw, a.clock_shift)
        check_no_straddle(raw, a.resample or a.timeframe.lower())
        if a.resample or a.clock_shift != "0h":
            version = f"{version}-{a.resample or a.timeframe}-shift{a.clock_shift}"
    issues = audit_bars(raw)
    audit = audit_summary(issues, len(load.symbols))
    bars = raw.filter(~pl.col("symbol").is_in(audit["excluded_critical"]))
    divs = (pl.read_parquet(a.dividends).select(list(DIVIDENDS_SCHEMA)) if a.dividends
            else pl.DataFrame(schema=DIVIDENDS_SCHEMA))
    mem = pl.read_parquet(a.membership) if a.membership else None
    caveats = list(load.caveats) if not a.dividends else []
    if mem is None:
        caveats.append(f"no index membership file: universe = top {a.top_n} by trailing dollar volume at each DP")
    bars = add_adj_factor(bars, divs)
    fcfg = fold_config_for(bars["date"].min(), bars["date"].max())
    fm = FoldManager(fcfg)
    costs = (CostModel.moneta_share_cfd_proxy() if a.costs == "moneta" else CostModel.flat(5.0) if a.costs == "flat5"
             else load_cost_overrides(a.costs, CostModel.moneta_share_cfd_proxy()))
    dev, ddev = fm.dev_view(bars), fm.dev_view(divs, "ex_date")
    cache = TradeCache(prepare_arrays(dev, ddev), version, cost_model=costs, cache_dir=out / "cache")
    cache.set_market_regime(*market_up_series(dev), "eqw-ma200")
    if mem is not None:
        cache.set_index_events(mem)
    base = LadderConfig(rung=a.rung, max_positions=a.max_positions, max_new_per_day=a.max_new,
                        universe_mode="membership" if mem is not None else "top_liquidity", universe_top_n=a.top_n)
    rows = equity_rows(base)
    if a.diverse:
        rows += diverse_rows(base, dividends=bool(a.dividends), index_events=mem is not None)
    if a.methods:
        keep = set(a.methods.split(","))
        assert keep <= set(MR_METHODS + TF_METHODS + DIVERSE_METHODS), keep
        rows = [r for r in rows if r.method in keep]
    ens = [] if a.no_ensembles else [replace(e, rung=a.rung) for e in ensemble_rows(rows) if len(e.members) > 1]
    rows = rows + ens
    reg = Registry(a.registry)
    if a.workers != 1:
        precompute(cache, rows, n_workers=a.workers or None)
    cat = run_catalog(fm, cache, dev, mem, rows, registry=reg, divs_dev=None if a.no_robustness else ddev)
    analysis = {}
    if a.analyze_accepted:
        analysis = analyze_rows(fm, cache, dev, mem, [cat["results"][i].config for i in cat["accepted"]], reg)
    hold = None
    if a.open_holdout:
        full = TradeCache(prepare_arrays(bars, divs), version + "-full", cost_model=costs)
        full.set_market_regime(*market_up_series(bars), "eqw-ma200")
        if mem is not None:
            full.set_index_events(mem)
        hold = run_holdout(fm, full, bars, mem, cat, reg, version)
    catalog = CATALOG_VERSION + (f"+{DIVERSE_CATALOG_VERSION}" if a.diverse else "")
    meta = {"data": version, "catalog": catalog, "universe": base.universe_mode, "top_n": a.top_n,
            "timeframe": a.resample or a.timeframe, "clock_shift": a.clock_shift,
            "symbols_loaded": len(load.symbols), "symbols_skipped": load.skipped, "audit": audit,
            "caveats": caveats, "costs": a.costs, "folds": {k: str(v) for k, v in fcfg.__dict__.items()}}
    pkg = build_evidence(cat, hold, meta)
    if analysis:
        pkg["row_analysis"], pkg["row_analysis_summary"] = analysis, summarize(analysis)
    (out / "evidence.json").write_text(dumps(pkg), encoding="utf-8")
    (out / "report.html").write_text(render_html(pkg), encoding="utf-8")
    issues.write_csv(out / "audit_issues.csv")
    summary = {"accepted": [cat["table"][i]["row"] for i in cat["accepted"]], "trials": cat["n_trials"],
               "combined_sharpe": cat["combined"]["sharpe"], "holdout": hold and hold["status"], "out": str(out),
               "row_analysis": summarize(analysis) if analysis else None}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return summary


if __name__ == "__main__":
    main()
