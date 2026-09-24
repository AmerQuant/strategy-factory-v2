"""Single metric library (subset for the skeleton).

Trades may carry Date (daily bars) or Datetime (intraday bars) columns; equity statistics are always computed on
realised pnl per calendar weekday, so daily and intraday rows are directly comparable (Sharpe x sqrt(252)).
"""
from __future__ import annotations

import math

import numpy as np
import polars as pl


def trade_stats(trades: pl.DataFrame) -> dict:
    n = len(trades)
    if n == 0:
        return {"n": 0, "expectancy": 0.0, "t_stat": 0.0, "win_rate": 0.0, "profit_factor": 0.0, "net": 0.0}
    x = trades["net_pnl"].to_numpy()
    sd = x.std(ddof=1) if n > 1 else 0.0
    wins, losses = x[x > 0].sum(), -x[x < 0].sum()
    return {
        "n": n, "net": float(x.sum()), "expectancy": float(x.mean()),
        "t_stat": float(x.mean() / (sd / math.sqrt(n))) if sd > 0 else 0.0,
        "win_rate": float((x > 0).mean()),
        "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
    }


def daily_pnl(trades: pl.DataFrame) -> np.ndarray:
    """Realised pnl per weekday (by exit date) over the trades' span, zero-filled on days without exits."""
    if len(trades) == 0:
        return np.zeros(0)
    start, end = trades["entry_date"].cast(pl.Date).min(), trades["exit_date"].cast(pl.Date).max()
    cal = pl.DataFrame({"exit_date": pl.date_range(start, end, "1d", eager=True)}).filter(
        pl.col("exit_date").dt.weekday() <= 5)
    agg = trades.group_by(pl.col("exit_date").cast(pl.Date)).agg(pl.col("net_pnl").sum())
    return cal.join(agg, on="exit_date", how="left").sort("exit_date")["net_pnl"].fill_null(0.0).to_numpy()


def equity_stats(trades: pl.DataFrame, capital: float = 100_000.0) -> dict:
    if len(trades) == 0:
        return {"sharpe": 0.0, "max_dd": 0.0}
    pnl = daily_pnl(trades)
    eq = capital + np.cumsum(pnl)
    dd = float(((np.maximum.accumulate(eq) - eq) / capital).max())
    sh = float(pnl.mean() / pnl.std(ddof=1) * math.sqrt(252)) if len(pnl) > 1 and pnl.std() > 0 else 0.0
    return {"sharpe": sh, "max_dd": dd}
