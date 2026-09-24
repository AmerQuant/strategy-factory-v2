"""Row MR-RSI-BUY (universe-based) at ladder rungs A0 (fixed params) and A1 (entry threshold chosen in-fold).

Every data-driven decision is taken at a DP from IS trades only (FoldManager.slice_is).
The trade cache must be built from FoldManager.dev_view(...) so nothing can reach the holdout.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import polars as pl

from sfactory.data.universe import eligible_at
from sfactory.engine.cache import TradeCache
from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.registry.repo import Registry
from sfactory.timeline.folds import FoldManager

CODE_VERSION = "0.1.0"


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


@dataclass
class RowResult:
    config: RowConfig
    decisions: list = field(default_factory=list)
    oos_trades: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)


def _pooled(cache: TradeCache, symbols, cfg: RowConfig, thr: float) -> list[pl.DataFrame]:
    return [cache.rsi_mr(s, cfg.period, thr, cfg.direction, cfg.max_hold) for s in symbols]


def select_threshold(is_scores: list[float]) -> int:
    """Plateau-lite: 3-point neighbourhood mean over the ordered grid; ties -> centre of grid."""
    s = np.array(is_scores, dtype=float)
    sm = np.array([np.nanmean(s[max(0, i - 1): i + 2]) if not np.all(np.isnan(s[max(0, i - 1): i + 2]))
                   else np.nan for i in range(len(s))])
    if np.all(np.isnan(sm)):
        return len(s) // 2
    best = np.nanmax(sm)
    cands = [i for i in range(len(s)) if sm[i] == best]
    return min(cands, key=lambda i: abs(i - (len(s) - 1) / 2))


def run_row(fm: FoldManager, cache: TradeCache, bars_dev: pl.DataFrame, membership: pl.DataFrame,
            cfg: RowConfig, registry: Registry | None = None) -> RowResult:
    res = RowResult(cfg)
    oos = []
    for fold in fm.dev_folds():
        elig = eligible_at(fold.dp, bars_dev, membership, cfg.min_price, cfg.min_dollar_vol,
                           min_history=cfg.min_history)
        decision = {"fold": fold.index, "dp": str(fold.dp), "n_eligible": len(elig)}
        if cfg.select:
            scores = []
            for thr in cfg.thresholds:
                frames = [fm.slice_is(t, fold) for t in _pooled(cache, elig, cfg, thr)]
                st = trade_stats(pl.concat(frames) if frames else pl.DataFrame())
                scores.append(st["t_stat"] if st["n"] >= cfg.min_is_trades else np.nan)
            thr = cfg.thresholds[select_threshold(scores)]
            decision["is_scores"] = [None if np.isnan(x) else round(float(x), 4) for x in scores]
        else:
            thr = cfg.fixed_threshold
        decision["threshold"] = thr
        frames = [fm.slice_oos(t, fold) for t in _pooled(cache, elig, cfg, thr)]
        if frames:
            oos.append(pl.concat(frames).with_columns(pl.lit(fold.index).alias("fold")))
        res.decisions.append(decision)
    res.oos_trades = pl.concat(oos) if oos else pl.DataFrame()
    res.stats = {**trade_stats(res.oos_trades), **equity_stats(res.oos_trades)}
    if registry is not None:
        tid = registry.record_trial(cfg.row_id, asdict(cfg), cache.data_version, CODE_VERSION, res.stats)
        for d in res.decisions:
            registry.record_fold(tid, d["fold"], d["dp"], d)
    return res
