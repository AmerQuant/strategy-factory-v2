"""Stage E benchmarks and selection-skill diagnostics for a row (see design ch. 10).

Benchmarks are diagnostics, not candidate policies: they are not registered as trials.
- grid_ensemble: trade every grid point with 1/len(grid) notional (no selection at all)
- frozen_first: the policy's first-fold choice kept for all folds (no re-optimisation)
- random_choice: a random grid point at every DP, many draws -> distribution
- rank_ic: per-fold Spearman correlation between IS scores and OOS scores across the grid
(random daily ranking among simultaneous signals needs the capacity simulator -> P5)
"""
from __future__ import annotations

import numpy as np
import polars as pl

from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.policy.grid import FoldCell
from sfactory.policy.rsi_row import RowConfig, assemble, is_scores, policy_chooser


def _stats(trades: pl.DataFrame) -> dict:
    return {**trade_stats(trades), **equity_stats(trades)}


def grid_ensemble(grid: list[FoldCell], cfg: RowConfig) -> dict:
    w = 1.0 / len(cfg.thresholds)
    frames = [cell.oos[t].with_columns((pl.col("net_pnl") * w).alias("net_pnl"))
              for cell in grid for t in cfg.thresholds if len(cell.oos[t])]
    return _stats(pl.concat(frames) if frames else pl.DataFrame())


def frozen_first(grid: list[FoldCell], cfg: RowConfig) -> dict:
    first = policy_chooser(cfg)(0, grid[0])
    return assemble(grid, cfg, lambda k, cell: first).stats | {"threshold": first}


def random_choice(grid: list[FoldCell], cfg: RowConfig, n_draws: int = 200, seed: int = 0,
                  metric: str = "sharpe") -> np.ndarray:
    rng = np.random.default_rng(seed)
    out = np.empty(n_draws)
    for i in range(n_draws):
        picks = rng.integers(0, len(cfg.thresholds), len(grid))
        out[i] = assemble(grid, cfg, lambda k, cell, p=picks: cfg.thresholds[p[k]]).stats[metric]
    return out


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


def evaluate_row(grid: list[FoldCell], cfg: RowConfig, n_draws: int = 200, seed: int = 0) -> dict:
    policy = assemble(grid, cfg, policy_chooser(cfg))
    rnd = random_choice(grid, cfg, n_draws, seed)
    sh = policy.stats["sharpe"]
    return {
        "policy": policy.stats,
        "grid_ensemble": grid_ensemble(grid, cfg),
        "frozen_first": frozen_first(grid, cfg),
        "random_choice": {"median": float(np.median(rnd)), "p95": float(np.percentile(rnd, 95)),
                          "policy_percentile": float((rnd < sh).mean() * 100)},
        "rank_ic": rank_ic(grid, cfg),
    }
