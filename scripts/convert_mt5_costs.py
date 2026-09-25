"""MT5 specification export + manual commission / slippage file -> the v2 cost CSV (costs/mt5_costs.py).

    uv run python scripts/convert_mt5_costs.py --specs mt5_specs.csv --manual manual_costs.csv \
        --spread-stat median --out costs_fx.csv --map-out broker_symbols_fx.csv

manual_costs.csv: symbol,commission_per_lot_side,commission_currency,slippage_bps (every column required per
symbol; commission 0 needs no currency). --spread-stat has no default: mean or median must be chosen. Prints a JSON
report; a symbol that cannot be converted is left out of the CSV and listed with the reason.
"""
from __future__ import annotations

import argparse
import json

from sfactory.broker.mt5_specs import read_specs_csv
from sfactory.costs.convert import write_broker_map, write_csv
from sfactory.costs.mt5_costs import convert_all_mt5, read_manual


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--specs", required=True, help="CSV from scripts/export_mt5_specs.py")
    ap.add_argument("--manual", required=True, help="CSV symbol,commission_per_lot_side,commission_currency,"
                                                    "slippage_bps")
    ap.add_argument("--spread-stat", required=True, choices=("mean", "median"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--map-out")
    a = ap.parse_args(argv)
    rows, skipped = convert_all_mt5(read_specs_csv(a.specs), read_manual(a.manual), a.spread_stat)
    write_csv(rows, a.out)
    mapped = write_broker_map(rows, a.map_out) if a.map_out else {}
    rep = {"converted": len(rows), "skipped": skipped, "spread_stat": a.spread_stat,
           "to_verify": {r.symbol: list(r.to_verify) for r in rows if r.to_verify}, "broker_mapped": len(mapped),
           "out": a.out}
    print(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    main()
