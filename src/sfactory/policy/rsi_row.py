"""Row MR-RSI-BUY (universe-based) at ladder rungs A0 (fixed params) and A1 (entry threshold chosen in-fold).

Every data-driven decision is taken at a DP from IS statistics only (see policy.grid.build_grid).
The trade cache must be built from FoldManager.dev_view(...) so nothing can reach the holdout.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field

import numpy as np
import polars as pl

from sfactory.engine.cache import TradeCache
from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.policy.grid import FoldCell, build_grid
from sfactory.portfolio.capacity import simulate_capacity
from sfactory.registry.repo import Registry
from sfactory.timeline.folds import FoldManager

CODE_VERSION = "0.3.0"


@dataclass(frozen=True)
class RowConfig:
    row_id: str = "MR-RSI2-BUY-EQ"
    period: int = 2
    thresholds: tuple = (5.0, 10.0, 15.0, 20.0, 25.0)
    fixed_threshold: float = 10.0
    select: bool = False  # False: A0, True: A1
    direction: int = 1
    max_hold: int = 5
    min_is_trades: int = 30
    min_price: float = 5.0
    min_dollar_vol: float = 1e6
    min_history: int = 250
    # capacity (S8); max_positions = 0 -> cell mode (every signal taken at the cache notional)
    max_positions: int = 0
    max_new_per_day: int = 5
    ranker: str = "score_asc"
    capital: float = 100_000.0


@dataclass
class RowResult:
    config: RowConfig
    decisions: list = field(default_factory=list)
    oos_trades: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)


def is_scores(cell: FoldCell, cfg: RowConfig) -> list[float]:
    return [cell.is_stats[t]["t_stat"] if cell.is_stats[t]["n"] >= cfg.min_is_trades else np.nan
            for t in cfg.thresholds]


def select_threshold(scores: list[float]) -> int:
    """Plateau-lite: 3-point neighbourhood mean over the ordered grid; ties -> centre of grid."""
    s = np.array(scores, dtype=float)
    sm = np.array([np.nanmean(s[max(0, i - 1): i + 2]) if not np.all(np.isnan(s[max(0, i - 1): i + 2]))
                   else np.nan for i in range(len(s))])
    if np.all(np.isnan(sm)):
        return len(s) // 2
    best = np.nanmax(sm)
    cands = [i for i in range(len(s)) if sm[i] == best]
    return min(cands, key=lambda i: abs(i - (len(s) - 1) / 2))


Chooser = Callable[[int, FoldCell], float]


def apply_capacity(trades: pl.DataFrame, cfg: RowConfig, cache_notional: float, ranker: str | None = None,
                   seed: int = 0) -> pl.DataFrame:
    if cfg.max_positions <= 0 or len(trades) == 0:
        return trades
    return simulate_capacity(trades, cfg.max_positions, cfg.max_new_per_day, cfg.capital, cache_notional,
                             ranker or cfg.ranker, seed)


def assemble(grid: list[FoldCell], cfg: RowConfig, choose: Chooser, cache_notional: float = 100_000.0,
             ranker: str | None = None, seed: int = 0) -> RowResult:
    """Run a threshold-choosing rule over precomputed folds and stitch the OOS trades."""
    res = RowResult(cfg)
    oos = []
    for k, cell in enumerate(grid):
        thr = choose(k, cell)
        d = {"fold": cell.fold.index, "dp": str(cell.fold.dp), "n_eligible": len(cell.eligible), "threshold": thr}
        if cfg.select:
            d["is_scores"] = [None if np.isnan(x) else round(float(x), 4) for x in is_scores(cell, cfg)]
        res.decisions.append(d)
        f = cell.oos[thr]
        if len(f):
            oos.append(f.with_columns(pl.lit(cell.fold.index).alias("fold")))
    res.oos_trades = apply_capacity(pl.concat(oos) if oos else pl.DataFrame(), cfg, cache_notional, ranker, seed)
    res.stats = {**trade_stats(res.oos_trades), **equity_stats(res.oos_trades)}
    return res


def policy_chooser(cfg: RowConfig) -> Chooser:
    if cfg.select:
        return lambda k, cell: cfg.thresholds[select_threshold(is_scores(cell, cfg))]
    return lambda k, cell: cfg.fixed_threshold


def run_row(fm: FoldManager, cache: TradeCache, bars_dev: pl.DataFrame, membership: pl.DataFrame,
            cfg: RowConfig, registry: Registry | None = None, grid: list[FoldCell] | None = None) -> RowResult:
    grid = grid if grid is not None else build_grid(fm, cache, bars_dev, membership, cfg)
    res = assemble(grid, cfg, policy_chooser(cfg), cache.notional)
    if registry is not None:
        tid = registry.record_trial(cfg.row_id, asdict(cfg), cache.data_version, CODE_VERSION, res.stats)
        for d in res.decisions:
            registry.record_fold(tid, d["fold"], d["dp"], d)
    return res
