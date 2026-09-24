"""Single metric library (subset for the skeleton)."""
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


def equity_stats(trades: pl.DataFrame, capital: float = 100_000.0) -> dict:
    if len(trades) == 0:
        return {"sharpe": 0.0, "max_dd": 0.0}
    daily = trades.group_by("exit_date").agg(pl.col("net_pnl").sum()).sort("exit_date")
    pnl = daily["net_pnl"].to_numpy()
    eq = capital + np.cumsum(pnl)
    dd = float(((np.maximum.accumulate(eq) - eq) / capital).max())
    sh = float(pnl.mean() / pnl.std(ddof=1) * math.sqrt(252)) if len(pnl) > 1 and pnl.std() > 0 else 0.0
    return {"sharpe": sh, "max_dd": dd}
