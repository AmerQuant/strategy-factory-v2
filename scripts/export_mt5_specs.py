"""Export symbol specifications from the MetaTrader 5 terminal (Moneta, ECN) into a CSV (broker/mt5_specs.py).

    set SF_MT5_PASSWORD=...
    uv run python scripts/export_mt5_specs.py --symbol-map broker_symbols.csv --out mt5_specs.csv \
        --mt5-login 123 --mt5-server Moneta-Demo [--timeframe H1 --bars 2000]

--symbol-map: CSV research_symbol,broker_symbol (as written by the cost converters); or --symbols with a text file
of broker symbols used under the same name. Windows only (official MetaTrader5 package). Prints a JSON report of
exported and skipped symbols; nothing is written for a symbol the terminal does not know.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from sfactory.broker.mt5_specs import export_specs, write_specs_csv
from sfactory.costs.convert import read_broker_map


def main(argv=None, mt5=None) -> dict:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--symbol-map", help="CSV research_symbol,broker_symbol")
    g.add_argument("--symbols", help="text file, one broker symbol per line")
    ap.add_argument("--out", required=True)
    ap.add_argument("--timeframe", default="H1", help="bars whose spread field is averaged (MT5 TIMEFRAME_<x>)")
    ap.add_argument("--bars", type=int, default=2000)
    ap.add_argument("--mt5-login", type=int)
    ap.add_argument("--mt5-server")
    ap.add_argument("--mt5-path")
    a = ap.parse_args(argv)
    if a.symbol_map:
        symbols = read_broker_map(a.symbol_map)
    else:
        symbols = {s: s for s in Path(a.symbols).read_text(encoding="utf-8").split() if s.strip()}
    if mt5 is None:
        import MetaTrader5 as mt5
    kw = {k: v for k, v in (("login", a.mt5_login), ("password", os.environ.get("SF_MT5_PASSWORD")),
                            ("server", a.mt5_server), ("path", a.mt5_path)) if v is not None}
    if not mt5.initialize(**kw):
        raise SystemExit(f"MT5 initialize failed: {mt5.last_error()}")
    try:
        rows, skipped = export_specs(mt5, symbols, a.timeframe, a.bars)
    finally:
        mt5.shutdown()
    write_specs_csv(rows, a.out)
    rep = {"exported": len(rows), "skipped": skipped, "no_spread_history": [r["research_symbol"] for r in rows
                                                                           if not r.get("spread_bars")],
           "out": a.out}
    print(json.dumps(rep, indent=1))
    return rep


if __name__ == "__main__":
    main()
