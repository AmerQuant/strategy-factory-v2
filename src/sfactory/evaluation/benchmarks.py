"""Stage E benchmarks and selection-skill diagnostics for a row (see design ch. 10).

Benchmarks are diagnostics, not candidate policies: they are not registered as trials.
- grid_ensemble: trade every grid point with 1/len(grid) notional (no selection at all)
- frozen_first: the policy's first-fold choice kept for all folds (no re-optimisation)
- random_choice: a random grid point at every DP, many draws -> distribution
- rank_ic: per-fold Spearman correlation between IS scores and OOS scores across the grid
- random_ranking: (capacity mode only) same book, simultaneous signals ranked randomly, many draws
- stats: PSR / DSR / MinTRL on the policy's zero-filled daily pnl
"""
from __future__ import annotations

import numpy as np
import polars as pl

from sfactory.metrics.core import daily_pnl, equity_stats, trade_stats
from sfactory.policy.grid import FoldCell
from sfactory.policy.rsi_row import RowConfig, apply_capacity, assemble, is_scores, policy_chooser
from sfactory.stats.core import sharpe_report

NOTIONAL = 100_000.0


def _stats(trades: pl.DataFrame) -> dict:
    return {**trade_stats(trades), **equity_stats(trades)}


def grid_ensemble(grid: list[FoldCell], cfg: RowConfig, notional: float = NOTIONAL) -> dict:
    """Cell mode: every grid point at 1/len(grid) notional. Capacity mode: all grid points compete for slots."""
    if cfg.max_positions > 0:
        frames = [cell.oos[t] for cell in grid for t in cfg.thresholds if len(cell.oos[t])]
        allc = pl.concat(frames).unique(subset=["symbol", "signal_date"], keep="first") if frames else pl.DataFrame()
        return _stats(apply_capacity(allc, cfg, notional))
    w = 1.0 / len(cfg.thresholds)
    frames = [cell.oos[t].with_columns((pl.col("net_pnl") * w).alias("net_pnl"))
              for cell in grid for t in cfg.thresholds if len(cell.oos[t])]
    return _stats(pl.concat(frames) if frames else pl.DataFrame())


def frozen_first(grid: list[FoldCell], cfg: RowConfig, notional: float = NOTIONAL) -> dict:
    first = policy_chooser(cfg)(0, grid[0])
    return assemble(grid, cfg, lambda k, cell: first, notional).stats | {"threshold": first}


def random_choice(grid: list[FoldCell], cfg: RowConfig, n_draws: int = 200, seed: int = 0,
                  metric: str = "sharpe", notional: float = NOTIONAL) -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.empty(n_draws)
    for i in range(n_draws):
        picks = rng.integers(0, len(cfg.thresholds), len(grid))
        out[i] = assemble(grid, cfg, lambda k, cell, p=picks: cfg.thresholds[p[k]], notional).stats[metric]
    return out


def random_ranking(grid: list[FoldCell], cfg: RowConfig, n_draws: int = 100, seed: int = 0,
                   metric: str = "sharpe", notional: float = NOTIONAL) -> np.ndarray:
    """Same thresholds as the policy; only the daily ranking among simultaneous signals is random."""
    if cfg.max_positions <= 0:
        raise ValueError("random_ranking needs capacity mode (max_positions > 0)")
    choose = policy_chooser(cfg)
    return np.array([assemble(grid, cfg, choose, notional, ranker="random", seed=seed + i).stats[metric]
                     for i in range(n_draws)])


def spearman(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < 3:
        return np.nan
    ra = np.argsort(np.argsort(a[m])).astype(float)
    rb = np.argsort(np.argsort(b[m])).astype(float)
    if ra.std() == 0 or rb.std() == 0:
        return np.nan
    return float(np.corrcoef(ra, rb)[0, 1])


def rank_ic(grid: list[FoldCell], cfg: RowConfig, min_oos_trades: int = 10) -> dict:
    ics = []
    for cell in grid:
        oos_scores = []
        for t in cfg.thresholds:
            st = trade_stats(cell.oos[t])
            oos_scores.append(st["expectancy"] if st["n"] >= min_oos_trades else np.nan)
        ics.append(spearman(is_scores(cell, cfg), oos_scores))
    v = np.array([x for x in ics if not np.isnan(x)])
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v)))) if len(v) > 1 and v.std() > 0 else 0.0
    return {"per_fold": ics, "mean": float(v.mean()) if len(v) else np.nan, "t_stat": t,
            "frac_positive": float((v > 0).mean()) if len(v) else np.nan, "n_folds": len(v)}


def _dist(values: np.ndarray, x: float) -> dict:
    return {"median": float(np.median(values)), "p95": float(np.percentile(values, 95)),
            "policy_percentile": float((values < x).mean() * 100)}


def evaluate_row(grid: list[FoldCell], cfg: RowConfig, n_draws: int = 200, seed: int = 0,
                 notional: float = NOTIONAL, n_trials: int = 1, sr_variance: float | None = None) -> dict:
    policy = assemble(grid, cfg, policy_chooser(cfg), notional)
    sh = policy.stats["sharpe"]
    rep = {
        "policy": policy.stats,
        "grid_ensemble": grid_ensemble(grid, cfg, notional),
        "frozen_first": frozen_first(grid, cfg, notional),
        "random_choice": _dist(random_choice(grid, cfg, n_draws, seed, notional=notional), sh),
        "rank_ic": rank_ic(grid, cfg),
        "stats": sharpe_report(daily_pnl(policy.oos_trades), n_trials, sr_variance),
    }
    if cfg.max_positions > 0:
        rep["random_ranking"] = _dist(random_ranking(grid, cfg, max(20, n_draws // 4), seed, notional=notional), sh)
    return rep
