"""Dividend adjustment: fully adjusted signal series from split-only prices + dividends.

Backward (CRSP-style): for every ex-date d, bars before d are multiplied by (1 - div_d / close_{d-1}).
Returns-based indicators on this series have no ex-date artefacts. Level rules use split-only prices.
"""
from __future__ import annotations

import numpy as np
import polars as pl


def add_adj_factor(bars: pl.DataFrame, dividends: pl.DataFrame) -> pl.DataFrame:
    out = []
    for (sym,), g in bars.sort(["symbol", "date"]).partition_by("symbol", as_dict=True).items():
        dates = g["date"].to_numpy()
        close = g["close"].to_numpy()
        factor = np.ones(len(g))
        d = dividends.filter(pl.col("symbol") == sym)
        for ex, amt in zip(d["ex_date"].to_numpy(), d["amount"].to_numpy()):
            idx = int(np.searchsorted(dates, ex))
            if 0 < idx < len(dates) and dates[idx] == ex and close[idx - 1] > 0:
                factor[:idx] *= 1.0 - amt / close[idx - 1]
        out.append(g.with_columns(pl.Series("adj_factor", factor)))
    return pl.concat(out)
