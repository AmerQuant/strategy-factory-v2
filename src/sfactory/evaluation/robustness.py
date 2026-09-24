"""Robustness tests on stitched OOS (design 10.3) for one row configuration.

Mandatory:  cost stress 1.5x stays profitable; Monte Carlo (block bootstrap) p95 drawdown within limit.
Supplementary (warnings for the analyst): cost 2x, one-bar execution delay keeps >= 50% of expectancy,
price noise keeps >= 50% of the Sharpe, no catastrophic regime (causal market-up / market-down labels).
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import polars as pl

from sfactory.data.adjust import add_adj_factor
from sfactory.data.regime import market_up_series
from sfactory.engine.cache import TradeCache, prepare_arrays
from sfactory.metrics.core import daily_pnl
from sfactory.policy.ladder import LadderConfig, run_ladder


def _sr(x: np.ndarray) -> float:
    sd = x.std(ddof=1) if len(x) > 1 else 0.0
    return float(x.mean() / sd * np.sqrt(252)) if sd > 0 else 0.0


def mc_drawdown_p95(daily: np.ndarray, capital: float, n_boot: int = 1000, block: int = 20, seed: int = 0) -> float:
    rng = np.random.default_rng(seed)
    n = len(daily)
    nb = int(np.ceil(n / block))
    dds = np.empty(n_boot)
    for i in range(n_boot):
        idx = (rng.integers(0, n, nb)[:, None] + np.arange(block)[None, :]).ravel()[:n] % n
        eq = capital + np.cumsum(daily[idx])
        dds[i] = ((np.maximum.accumulate(np.r_[capital, eq])[1:] - eq) / capital).max()
    return float(np.percentile(dds, 95))


def regime_breakdown(trades: pl.DataFrame, dates: np.ndarray, flags: np.ndarray) -> dict:
    """Trade expectancy by causal regime label at the signal date."""
    if len(trades) == 0:
        return {}
    lab = pl.DataFrame({"signal_date": dates, "up": flags}).with_columns(pl.col("signal_date").cast(pl.Date))
    j = trades.join(lab, on="signal_date", how="left").with_columns(pl.col("up").fill_null(False))
    out = {}
    for name, flag in (("market_up", True), ("market_down", False)):
        g = j.filter(pl.col("up") == flag)["net_pnl"]
        out[name] = {"n": len(g), "expectancy": float(g.mean()) if len(g) else 0.0}
    return out


def run_robustness(fm, cache: TradeCache, bars_dev: pl.DataFrame, divs_dev: pl.DataFrame, membership,
                   cfg: LadderConfig, dd_limit: float = 0.35, noise_sd: float = 0.002, seed: int = 0) -> dict:
    base = run_ladder(fm, cache, bars_dev, membership, cfg)
    b_daily = daily_pnl(base.oos_trades)
    exp0 = base.stats["expectancy"]
    out = {"base": {"sharpe": _sr(b_daily), "expectancy": exp0, "n": base.stats["n"]}}
    for f in (1.5, 2.0):
        c2 = TradeCache(cache.arrays, cache.data_version, cache.notional, cost_model=cache.cost_model.stressed(f))
        c2._regime_dates, c2._regime_flags, c2._regime_id = cache._regime_dates, cache._regime_flags, cache._regime_id
        r = run_ladder(fm, c2, bars_dev, membership, cfg)
        out[f"cost_x{f:g}"] = {"sharpe": _sr(daily_pnl(r.oos_trades)), "expectancy": r.stats["expectancy"]}
    rd = run_ladder(fm, cache, bars_dev, membership, replace(cfg, entry_delay=1))
    out["delay_1bar"] = {"expectancy": rd.stats["expectancy"], "keep": rd.stats["expectancy"] / exp0 if exp0 > 0 else 0}
    rng = np.random.default_rng(seed)
    noisy = bars_dev.drop("adj_factor").with_columns(
        [(pl.col(c) * pl.Series(np.exp(rng.normal(0, noise_sd, len(bars_dev))))) for c in ("open", "high", "low",
                                                                                          "close")])
    noisy = noisy.with_columns(pl.max_horizontal("open", "high", "close").alias("high"),
                               pl.min_horizontal("open", "low", "close").alias("low"))
    noisy = add_adj_factor(noisy, divs_dev)
    cn = TradeCache(prepare_arrays(noisy, divs_dev), cache.data_version + "-noise", cache.notional,
                    cost_model=cache.cost_model)
    if cache._regime_dates is not None:
        cn.set_market_regime(*market_up_series(noisy), cache._regime_id + "-noise")
    rn = run_ladder(fm, cn, noisy, membership, cfg)
    out["noise"] = {"sharpe": _sr(daily_pnl(rn.oos_trades))}
    out["mc_dd_p95"] = mc_drawdown_p95(b_daily, cfg.capital, seed=seed) if len(b_daily) > 40 else float("nan")
    d, fl = market_up_series(bars_dev)
    out["regime"] = regime_breakdown(base.oos_trades, d, fl)
    base_sr = out["base"]["sharpe"]
    mandatory = {"cost_x1.5_profitable": out["cost_x1.5"]["expectancy"] > 0,
                 "mc_dd_p95_within_limit": out["mc_dd_p95"] <= dd_limit}
    warnings = {"cost_x2_profitable": out["cost_x2"]["expectancy"] > 0,
                "delay_keeps_half": out["delay_1bar"]["keep"] >= 0.5,
                "noise_keeps_half": base_sr > 0 and out["noise"]["sharpe"] >= 0.5 * base_sr,
                "no_catastrophic_regime": all(v["expectancy"] > -abs(exp0) or v["n"] < 10
                                              for v in out["regime"].values())}
    out["mandatory"], out["warnings"] = mandatory, warnings
    out["passed"] = all(mandatory.values())
    return out
