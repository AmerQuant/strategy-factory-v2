"""Convert the v1 cost profiles (Moneta build included) into the v2 per-symbol cost CSV (`--costs file.csv`).

    uv run --with pyyaml python scripts/convert_moneta_costs.py \
        --v1-costs D:/AmerAndish/Projects/Trade/strategy-factory/configs/costs \
        --universe D:/AmerAndish/Projects/Trade/strategy-factory/configs/universe/us_equity_daily.csv \
        --store D:/AmerAndish/Projects/Trade/StrategyFactory_data/store \
        --notional 10000 --out costs_moneta.csv --map-out broker_symbols.csv

Reads v1's YAML read-only (pyyaml, only for this script). Reference prices and ATR% for price-unit costs come
from the v1 store (median close / median ATR14% over the last 250 daily bars). Writes the cost CSV, the
research -> broker symbol map for the MT5 adapter, and prints a JSON report (converted, skipped with reasons,
placeholders, to-verify flags). Nothing falls back to a default silently: a symbol that cannot be converted is
left out of the CSV and listed.
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import polars as pl

from sfactory.costs.convert import convert_all, write_broker_map, write_csv


def _yaml(path: Path) -> dict:
    try:
        import yaml
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise SystemExit("pyyaml is needed to read the v1 files: `uv run --with pyyaml python ...`") from exc
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_v1(costs_dir: Path) -> tuple[dict, dict, dict]:
    """(profiles by name, groups, symbol assignments) as v1 loads them (base + generated Moneta files)."""
    profiles = {}
    for p in sorted(costs_dir.glob("*.yaml")):
        if p.name != "assignments.yaml":
            d = _yaml(p)
            profiles[d["name"]] = d
    gen = costs_dir / "moneta" / "moneta_profiles.yaml"
    if gen.is_file():
        for d in _yaml(gen).get("profiles") or []:
            profiles[d["name"]] = d
    base = _yaml(costs_dir / "assignments.yaml")
    groups, symbols = dict(base.get("groups") or {}), dict(base.get("symbols") or {})
    ga = costs_dir / "moneta" / "assignments.yaml"
    if ga.is_file():
        extra = _yaml(ga).get("symbols") or {}
        both = sorted(set(symbols) & set(extra))
        if both:
            raise SystemExit(f"symbols assigned in both v1 assignment files: {both[:10]}")
        symbols.update(extra)
    return profiles, groups, symbols


def reference_values(store: str, symbols: list[str], window: int = 250) -> tuple[dict, dict]:
    from sfactory.data.sfac_store import load_store
    bars = load_store(store, "1D", symbols=symbols).bars.sort(["symbol", "date"])
    pc = pl.col("close").shift(1).over("symbol")
    tr = pl.max_horizontal(pl.col("high") - pl.col("low"), (pl.col("high") - pc).abs(), (pl.col("low") - pc).abs())
    b = bars.with_columns((tr.rolling_mean(14).over("symbol") / pl.col("close")).alias("atr_pct"))
    last = b.group_by("symbol").tail(window)
    agg = last.group_by("symbol").agg(pl.col("close").median().alias("px"), pl.col("atr_pct").median().alias("atr"))
    return ({r["symbol"]: r["px"] for r in agg.iter_rows(named=True)},
            {r["symbol"]: r["atr"] for r in agg.iter_rows(named=True) if r["atr"] is not None})


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-costs", required=True, help="v1 configs/costs directory")
    ap.add_argument("--universe", action="append", default=[],
                    help="CSV with symbol[,asset_class] (repeatable); default: the v1 symbol assignments")
    ap.add_argument("--store", help="v1 data store root, for reference prices and ATR%%")
    ap.add_argument("--prices", help="CSV symbol,price instead of / in addition to --store")
    ap.add_argument("--notional", type=float, default=10_000.0, help="trade notional for per-order commissions")
    ap.add_argument("--out", required=True)
    ap.add_argument("--map-out")
    a = ap.parse_args(argv)
    profiles, groups, assigned = load_v1(Path(a.v1_costs))
    universe: dict[str, str] = {}
    for f in a.universe:
        with open(f, encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                universe[r["symbol"]] = r.get("asset_class") or "us_equity"
    if not universe:
        universe = {s: "us_equity" for s in assigned}
    prices, atr = {}, {}
    if a.store:
        prices, atr = reference_values(a.store, sorted(universe))
    if a.prices:
        with open(a.prices, encoding="utf-8", newline="") as fh:
            prices.update({r["symbol"]: float(r["price"]) for r in csv.DictReader(fh)})
    rows, skipped = convert_all(universe, profiles, groups, assigned, prices, a.notional, atr)
    write_csv(rows, a.out)
    mapped = write_broker_map(rows, a.map_out) if a.map_out else {}
    reasons: dict[str, int] = {}
    for why in skipped.values():
        reasons[why] = reasons.get(why, 0) + 1
    rep = {"converted": len(rows), "skipped": len(skipped), "skip_reasons": reasons,
           "placeholders": sum(r.status == "placeholder" for r in rows),
           "to_verify": {r.symbol: list(r.to_verify) for r in rows if r.to_verify},
           "broker_mapped": len(mapped), "out": a.out}
    print(json.dumps(rep, indent=1, ensure_ascii=False))
    return rep


if __name__ == "__main__":
    main()
