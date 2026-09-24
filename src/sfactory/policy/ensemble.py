"""Tradable family-ensemble rows (design 13.3): several members of one family x direction traded together,
each with its own in-fold selection, capital split equally. The ensemble is ONE registry trial.

`run_row_any` dispatches LadderConfig / EnsembleConfig so the catalogue, holdout, robustness and live code
treat both the same way.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace

import polars as pl

from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.policy.ladder import CODE_VERSION, LadderConfig, run_ladder
from sfactory.registry.repo import Registry


@dataclass(frozen=True)
class EnsembleConfig:
    members: tuple = ()
    name: str | None = None
    rung: str = "A1"
    entry_delay: int = 0
    asset_class: str = "equities"
    method: str = "ensemble"

    @property
    def family(self) -> str:
        return self.members[0].family

    @property
    def direction(self) -> int:
        return self.members[0].direction

    @property
    def capital(self) -> float:
        return self.members[0].capital

    @property
    def max_positions(self) -> int:
        return self.members[0].max_positions

    @property
    def rid(self) -> str:
        side = "BUY" if self.direction == 1 else "SELL"
        return self.name or f"ENS-{self.family}-{side}-{len(self.members)}"

    def expanded(self) -> list[LadderConfig]:
        k = len(self.members)
        return [replace(m, rung=self.rung, entry_delay=self.entry_delay,
                        capital=m.capital / k if m.max_positions > 0 else m.capital) for m in self.members]


@dataclass
class EnsembleResult:
    config: EnsembleConfig
    decisions: list = field(default_factory=list)
    oos_trades: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)


def family_ensemble_row(rows: list[LadderConfig], family: str, direction: int, **kw) -> EnsembleConfig:
    members = tuple(r for r in rows if r.family == family and r.direction == direction)
    return EnsembleConfig(members=members, **kw)


def run_ensemble(fm, cache, bars_dev, membership, cfg: EnsembleConfig, registry: Registry | None = None,
                 folds: list | None = None) -> EnsembleResult:
    res = EnsembleResult(cfg)
    frames = []
    k = len(cfg.members)
    for m in cfg.expanded():
        r = run_ladder(fm, cache, bars_dev, membership, m, None, folds)
        res.decisions.append({"member": m.rid, "decisions": r.decisions})
        t = r.oos_trades
        if len(t):
            if m.max_positions <= 0:          # cell mode: split notional equally
                t = t.with_columns([(pl.col(c) / k) for c in ("gross_pnl", "cost", "dividends", "net_pnl")])
            frames.append(t.with_columns(pl.lit(m.rid).alias("member")))
    res.oos_trades = pl.concat(frames, how="diagonal") if frames else pl.DataFrame()
    res.stats = {**trade_stats(res.oos_trades), **equity_stats(res.oos_trades)}
    if registry is not None:
        registry.record_trial(cfg.rid, asdict(cfg), cache.data_version, CODE_VERSION, res.stats)
    return res


def run_row_any(fm, cache, bars_dev, membership, cfg, registry: Registry | None = None, folds: list | None = None):
    if isinstance(cfg, EnsembleConfig):
        return run_ensemble(fm, cache, bars_dev, membership, cfg, registry, folds)
    return run_ladder(fm, cache, bars_dev, membership, cfg, registry, folds)
