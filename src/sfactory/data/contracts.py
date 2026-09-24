"""Data contracts (see docs/spec/data-contracts.md).

Bars are long-format Polars frames. `open/high/low/close` are SPLIT-ONLY prices (execution series).
The fully adjusted (split + dividend) signal series is `price * adj_factor`.
"""
from __future__ import annotations

import polars as pl

BARS_SCHEMA = {
    "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
    "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64,
}
DIVIDENDS_SCHEMA = {"symbol": pl.Utf8, "ex_date": pl.Date, "amount": pl.Float64}
MEMBERSHIP_SCHEMA = {"symbol": pl.Utf8, "start": pl.Date, "end": pl.Date}  # end exclusive; null = still member

TRADE_COLUMNS = [
    "symbol", "signal_date", "entry_date", "exit_date", "entry_px", "exit_px",
    "shares", "gross_pnl", "cost", "dividends", "net_pnl", "forced_exit",
]


def validate(df: pl.DataFrame, schema: dict, name: str) -> pl.DataFrame:
    missing = [c for c in schema if c not in df.columns]
    if missing:
        raise ValueError(f"{name}: missing columns {missing}")
    return df.select([pl.col(c).cast(t) for c, t in schema.items()])
