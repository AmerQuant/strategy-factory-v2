"""FoldGrid: per-fold precomputation shared by the policy and all benchmarks (compute once, slice many).

For every dev fold: point-in-time eligible symbols, IS stats per grid point, OOS trades per grid point.
Every IS number is computed with FoldManager.slice_is, every OOS frame with FoldManager.slice_oos.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from sfactory.data.universe import eligible_at
from sfactory.engine.cache import TradeCache
from sfactory.metrics.core import trade_stats
from sfactory.timeline.folds import Fold, FoldManager


@dataclass
class FoldCell:
    fold: Fold
    eligible: list
    is_stats: dict = field(default_factory=dict)    # thr -> trade_stats
    oos: dict = field(default_factory=dict)         # thr -> DataFrame


def _concat(frames: list[pl.DataFrame]) -> pl.DataFrame:
    frames = [f for f in frames if len(f)]
    return pl.concat(frames) if frames else pl.DataFrame()


def build_grid(fm: FoldManager, cache: TradeCache, bars_dev: pl.DataFrame, membership: pl.DataFrame,
               cfg) -> list[FoldCell]:
    out = []
    for fold in fm.dev_folds():
        elig = eligible_at(fold.dp, bars_dev, membership, cfg.min_price, cfg.min_dollar_vol,
                           min_history=cfg.min_history)
        cell = FoldCell(fold, elig)
        for thr in cfg.thresholds:
            trades = [cache.rsi_mr(s, cfg.period, thr, cfg.direction, cfg.max_hold) for s in elig]
            cell.is_stats[thr] = trade_stats(_concat([fm.slice_is(t, fold) for t in trades]))
            cell.oos[thr] = _concat([fm.slice_oos(t, fold) for t in trades])
        out.append(cell)
    return out
