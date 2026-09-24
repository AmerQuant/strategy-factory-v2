"""Forward monitoring (design 15.2-15.3): Monte Carlo bands, CUSUM, implementation shortfall, pre-registered
incubation / stop rules. All thresholds are fixed before incubation starts.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import polars as pl


def mc_bands(dev_daily: np.ndarray, horizon: int, n_boot: int = 2000, block: int = 20, seed: int = 0,
             capital: float = 100_000.0, q=(1, 5, 50, 95, 99)) -> dict:
    """Percentile bands of cumulative pnl and running drawdown for each day 1..horizon (block bootstrap)."""
    rng = np.random.default_rng(seed)
    n = len(dev_daily)
    nb = math.ceil(horizon / block)
    cum = np.empty((n_boot, horizon))
    dd = np.empty((n_boot, horizon))
    for i in range(n_boot):
        idx = (rng.integers(0, n, nb)[:, None] + np.arange(block)[None, :]).ravel()[:horizon] % n
        c = np.cumsum(dev_daily[idx])
        cum[i] = c
        eq = capital + c
        dd[i] = (np.maximum.accumulate(np.r_[capital, eq])[1:] - eq) / capital
    return {"q": list(q), "cum": {p: np.percentile(cum, p, axis=0) for p in q},
            "dd": {p: np.percentile(dd, p, axis=0) for p in q}}


def cusum_lower(x: np.ndarray, target_mean: float, sd: float, k: float = 0.5, h: float = 8.0) -> int | None:
    """One-sided lower CUSUM on standardised values; index of the first alarm (mean shift down) or None.

    k = 0.5, h = 8 keeps false alarms rare over a few hundred trades while a 1-2 sd drop is caught quickly.
    """
    s = 0.0
    for i, v in enumerate(np.asarray(x, float)):
        z = (v - target_mean) / sd if sd > 0 else 0.0
        s = min(0.0, s + z + k)
        if s < -h:
            return i
    return None


def implementation_shortfall(live: pl.DataFrame, backtest: pl.DataFrame) -> dict:
    """Matched on (symbol, signal_date): shortfall = backtest net - live net per trade (positive = cost)."""
    j = backtest.select("symbol", "signal_date", pl.col("net_pnl").alias("bt")).join(
        live.select("symbol", "signal_date", pl.col("net_pnl").alias("lv")), on=["symbol", "signal_date"],
        how="inner")
    if len(j) == 0:
        return {"matched": 0, "mean_shortfall": 0.0, "shortfall_frac": 0.0, "missed": len(backtest)}
    sf = (j["bt"] - j["lv"]).to_numpy()
    exp_bt = float(j["bt"].mean())
    return {"matched": len(j), "mean_shortfall": float(sf.mean()),
            "shortfall_frac": float(sf.mean() / exp_bt) if exp_bt > 0 else float("inf"),
            "missed": len(backtest) - len(j)}


@dataclass(frozen=True)
class ForwardRules:
    """Pre-registered (design 15.1/15.3)."""
    min_days: int = 126
    min_trades: int = 40
    band_low: int = 5            # live cumulative pnl must stay above this percentile to promote
    dd_stop: int = 99            # drawdown beyond this percentile -> stop
    shortfall_ok: float = 0.25   # share of backtest expectancy
    shortfall_stop: float = 0.50
    cusum_k: float = 0.5
    cusum_h: float = 8.0


def evaluate_forward(live_daily: np.ndarray, live_trades_pnl: np.ndarray, bands: dict, shortfall: dict,
                     dev_trade_mean: float, dev_trade_sd: float, rules: ForwardRules | None = None) -> dict:
    rules = rules or ForwardRules()
    d = len(live_daily)
    if d == 0:
        return {"status": "continue", "reasons": ["no live data yet"]}
    cum = float(np.cumsum(live_daily)[-1])
    eq = 100_000.0 + np.cumsum(live_daily)
    dd = float(((np.maximum.accumulate(np.r_[100_000.0, eq])[1:] - eq) / 100_000.0)[-1])
    h = min(d, len(bands["cum"][rules.band_low])) - 1
    alarm = cusum_lower(live_trades_pnl, dev_trade_mean, dev_trade_sd, rules.cusum_k, rules.cusum_h)
    stop = []
    if dd > bands["dd"][rules.dd_stop][h]:
        stop.append(f"drawdown beyond p{rules.dd_stop} band")
    if alarm is not None:
        stop.append(f"CUSUM alarm at trade {alarm}")
    if shortfall.get("matched", 0) and shortfall["shortfall_frac"] > rules.shortfall_stop:
        stop.append("implementation shortfall above stop level")
    if stop:
        return {"status": "stop", "reasons": stop}
    enough = d >= rules.min_days or len(live_trades_pnl) >= rules.min_trades
    in_band = cum >= bands["cum"][rules.band_low][h]
    sf_ok = (not shortfall.get("matched", 0)) or shortfall["shortfall_frac"] <= rules.shortfall_ok
    if enough and in_band and sf_ok:
        return {"status": "promote", "reasons": ["incubation criteria met"]}
    reasons = [r for r, bad in (("not enough history", not enough), ("below lower band", not in_band),
                                ("shortfall above tolerance", not sf_ok)) if bad]
    return {"status": "continue", "reasons": reasons}
