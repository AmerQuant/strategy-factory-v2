"""Causal market regime proxy for structural filters and regime labels (design ch. 12)."""
from __future__ import annotations

import numpy as np
import polars as pl

from sfactory.signals.indicators import sma


def market_up_series(bars_adj: pl.DataFrame, ma: int = 200) -> tuple[np.ndarray, np.ndarray]:
    """Equal-weight index of adjusted daily returns of all listed symbols; flag = index above its SMA(ma).

    Only returns up to each date are used, so the flag at t is known at the close of t.
    """
    r = (bars_adj.sort(["symbol", "date"])
         .with_columns((pl.col("close") * pl.col("adj_factor")).alias("px"))
         .with_columns((pl.col("px") / pl.col("px").shift(1).over("symbol") - 1).alias("ret"))
         .drop_nulls("ret").group_by("date").agg(pl.col("ret").mean()).sort("date"))
    idx = np.cumprod(1 + r["ret"].to_numpy())
    m = sma(idx, ma)
    return r["date"].to_numpy(), np.where(np.isnan(m), False, idx > m)
