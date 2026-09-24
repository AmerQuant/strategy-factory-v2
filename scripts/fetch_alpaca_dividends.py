"""Download cash dividends from Alpaca's corporate-actions API into a v2 dividends parquet.

    set ALPACA_API_KEY / ALPACA_API_SECRET (same names as v1), then
    uv run python scripts/fetch_alpaca_dividends.py --symbols symbols.txt --start 2000-01-01 --out dividends.parquet

Standard library only (no new dependency). Output columns: symbol, ex_date, amount (per share, as paid; the
v1 store is split-adjusted, so amounts are rescaled for later splits with --splits-adjust using Alpaca's
forward/reverse split actions). NOT yet run against the live API from this environment (no network access to
alpaca.markets here): check the first page of output before using it in research.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime

import polars as pl

URL = "https://data.alpaca.markets/v1/corporate-actions"


def fetch(symbols: list[str], start: str, end: str, types: str) -> list[dict]:
    key, secret = os.environ["ALPACA_API_KEY"], os.environ["ALPACA_API_SECRET"]
    rows, token = [], None
    for i in range(0, len(symbols), 50):
        batch = symbols[i:i + 50]
        while True:
            q = {"symbols": ",".join(batch), "types": types, "start": start, "end": end, "limit": 1000}
            if token:
                q["page_token"] = token
            req = urllib.request.Request(f"{URL}?{urllib.parse.urlencode(q)}",
                                         headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret})
            with urllib.request.urlopen(req, timeout=60) as r:
                data = json.loads(r.read().decode("utf-8"))
            ca = data.get("corporate_actions", {})
            for kind, items in ca.items():
                rows.extend({"kind": kind, **it} for it in items)
            token = data.get("next_page_token")
            if not token:
                break
            time.sleep(0.3)
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True)
    ap.add_argument("--start", default="2000-01-01")
    ap.add_argument("--end", default=str(datetime.now(UTC).date()))
    ap.add_argument("--out", required=True)
    ap.add_argument("--splits-adjust", action="store_true")
    a = ap.parse_args()
    with open(a.symbols, encoding="utf-8") as fh:
        syms = [s.strip() for s in fh.read().split() if s.strip()]
    types = "cash_dividend,forward_split,reverse_split" if a.splits_adjust else "cash_dividend"
    raw = fetch(syms, a.start, a.end, types)
    div = pl.DataFrame([{"symbol": r["symbol"], "ex_date": r["ex_date"], "amount": float(r["rate"])}
                        for r in raw if r["kind"] == "cash_dividends"],
                       schema={"symbol": pl.Utf8, "ex_date": pl.Utf8, "amount": pl.Float64})
    div = div.with_columns(pl.col("ex_date").str.to_date())
    if a.splits_adjust:
        sp = [{"symbol": r["symbol"], "ex_date": r["ex_date"], "ratio": float(r["new_rate"]) / float(r["old_rate"])}
              for r in raw if r["kind"] in ("forward_splits", "reverse_splits")]
        if sp:
            spl = pl.DataFrame(sp).with_columns(pl.col("ex_date").str.to_date())
            # amount / product of split ratios with ex_date after the dividend
            div = div.with_columns(pl.struct("symbol", "ex_date").map_elements(
                lambda s: float(spl.filter((pl.col("symbol") == s["symbol"]) & (pl.col("ex_date") > s["ex_date"]))
                                ["ratio"].product() or 1.0), return_dtype=pl.Float64).alias("f"))
            div = div.with_columns((pl.col("amount") / pl.col("f")).alias("amount")).drop("f")
    div.sort(["symbol", "ex_date"]).write_parquet(a.out)
    print(f"{div.height} dividends for {div['symbol'].n_unique()} symbols -> {a.out}")


if __name__ == "__main__":
    main()
