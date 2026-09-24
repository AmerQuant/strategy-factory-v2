"""Row with the full in-fold ablation ladder (design 9.2): A0 fixed -> A1 entry -> A3 exit -> A4 filter.

Any entry method (signals.methods: MR rsi/ibs/consec/lowest_close/donchian_low, TF ma_cross/donchian_break/
supertrend/ichimoku), buy or sell. Grid, default value, exit library and neutral exit follow the method family.

Per decision point, using IS trades only:
  A1  entry threshold by plateau-lite over the grid (neutral exit, no optional filter)
  A3  exit from the library; accepted only if it beats the neutral exit by `exit_margin` (t-stat units)
      AND survives the joint-plateau check (neighbouring thresholds keep >= plateau_ratio of its IS t-stat)
  A4  at most one optional filter; accepted only if it improves the IS t-stat, keeps >= filter_min_keep of the
      trades, improves expectancy in most IS years, and beats random removal of the same number of trades
Structural filters (design ch. 12) are fixed, always applied and never selected in-fold.
Capacity/ranking (S8) is applied to the stitched OOS stream when max_positions > 0.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import polars as pl

from sfactory.data.universe import eligible_at
from sfactory.engine.cache import TradeCache
from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.policy.rsi_row import select_threshold
from sfactory.portfolio.capacity import simulate_capacity
from sfactory.registry.repo import Registry
from sfactory.signals.methods import DEFAULT, FAMILY, GRID, EntrySpec
from sfactory.signals.specs import MR_FILTER_LIBRARY, ExitSpec, FilterSpec, exit_library, neutral_exit
from sfactory.timeline.folds import FoldManager

CODE_VERSION = "0.5.0"
RUNG_LEVEL = {"A0": 0, "A1": 1, "A3": 3, "A4": 4}


@dataclass(frozen=True)
class LadderConfig:
    rung: str = "A0"
    method: str = "rsi"
    direction: int = 1
    row_id: str | None = None          # default: <family>-<METHOD>-<BUY|SELL>-EQ
    thresholds: tuple | None = None    # entry grid; default signals.methods.GRID[method]
    fixed_threshold: float | None = None
    exits: tuple | None = None         # default: the family's exit library
    filters: tuple = MR_FILTER_LIBRARY
    structural: tuple = ()
    min_is_trades: int = 30
    exit_margin: float = 0.25
    plateau_ratio: float = 0.7
    filter_min_keep: float = 0.6
    filter_rand_pct: float = 0.9
    n_rand: int = 200
    min_price: float = 5.0
    min_dollar_vol: float = 1e6
    min_history: int = 250
    max_positions: int = 0
    max_new_per_day: int = 5
    ranker: str = "score_asc"
    capital: float = 100_000.0

    @property
    def level(self) -> int:
        return RUNG_LEVEL[self.rung]

    @property
    def family(self) -> str:
        return FAMILY[self.method]

    @property
    def grid(self) -> tuple:
        return tuple(self.thresholds) if self.thresholds is not None else GRID[self.method]

    @property
    def fixed(self) -> float:
        return self.fixed_threshold if self.fixed_threshold is not None else DEFAULT[self.method]

    @property
    def neutral(self) -> ExitSpec:
        return neutral_exit(self.family)

    @property
    def exit_lib(self) -> tuple:
        return tuple(self.exits) if self.exits is not None else exit_library(self.family)

    @property
    def rid(self) -> str:
        side = "BUY" if self.direction == 1 else "SELL"
        return self.row_id or f"{self.family}-{self.method.upper()}-{side}-EQ"


@dataclass
class LadderResult:
    config: LadderConfig
    decisions: list = field(default_factory=list)
    oos_trades: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)


def _concat(frames):
    frames = [f for f in frames if len(f)]
    return pl.concat(frames) if frames else pl.DataFrame()


def _t(df: pl.DataFrame, min_n: int) -> float:
    st = trade_stats(df)
    return st["t_stat"] if st["n"] >= min_n else np.nan


def years_improved(base: pl.DataFrame, filt: pl.DataFrame) -> float:
    """Fraction of IS years (with trades in both) where the filtered expectancy beats the base."""
    if len(base) == 0 or len(filt) == 0:
        return 0.0
    yb = base.group_by(pl.col("entry_date").dt.year().alias("y")).agg(pl.col("net_pnl").mean().alias("b"))
    yf = filt.group_by(pl.col("entry_date").dt.year().alias("y")).agg(pl.col("net_pnl").mean().alias("f"))
    j = yb.join(yf, on="y")
    return float((j["f"] > j["b"]).mean()) if len(j) else 0.0


def random_removal_pct(base_pnl: np.ndarray, k: int, t_obs: float, n_rand: int, seed: int) -> float:
    """Percentile of t_obs among t-stats of random subsets of size k of the base trades."""
    if k < 3 or k >= len(base_pnl):
        return 0.0
    rng = np.random.default_rng(seed)
    ts = np.empty(n_rand)
    for i in range(n_rand):
        x = rng.choice(base_pnl, k, replace=False)
        sd = x.std(ddof=1)
        ts[i] = x.mean() / (sd / np.sqrt(k)) if sd > 0 else 0.0
    return float((ts < t_obs).mean())


class _FoldView:
    """IS/OOS access for one fold with memoised IS frames (every slice goes through FoldManager)."""

    def __init__(self, fm: FoldManager, cache: TradeCache, cfg: LadderConfig, fold, elig: list):
        self.fm, self.cache, self.cfg, self.fold, self.elig = fm, cache, cfg, fold, elig
        self.memo: dict = {}

    def trades(self, thr: float, ex: ExitSpec, fl: tuple[FilterSpec, ...]):
        c = self.cfg
        spec = EntrySpec(c.method, thr, c.direction)
        return [self.cache.trades(s, spec, ex, c.structural + fl) for s in self.elig]

    def is_df(self, thr: float, ex: ExitSpec, fl: tuple = ()) -> pl.DataFrame:
        key = (thr, ex.id, tuple(f.id for f in fl))
        if key not in self.memo:
            self.memo[key] = _concat([self.fm.slice_is(t, self.fold) for t in self.trades(thr, ex, fl)])
        return self.memo[key]

    def oos_df(self, thr: float, ex: ExitSpec, fl: tuple) -> pl.DataFrame:
        return _concat([self.fm.slice_oos(t, self.fold) for t in self.trades(thr, ex, fl)])


def run_ladder(fm: FoldManager, cache: TradeCache, bars_dev: pl.DataFrame, membership: pl.DataFrame,
               cfg: LadderConfig, registry: Registry | None = None, folds: list | None = None) -> LadderResult:
    """`folds` defaults to the dev folds; only the holdout stage passes dev + unlocked holdout folds."""
    res = LadderResult(cfg)
    oos = []
    lvl = cfg.level
    for fold in (folds if folds is not None else fm.dev_folds()):
        elig = eligible_at(fold.dp, bars_dev, membership, cfg.min_price, cfg.min_dollar_vol,
                           min_history=cfg.min_history)
        view = _FoldView(fm, cache, cfg, fold, elig)
        is_df = view.is_df
        d = {"fold": fold.index, "dp": str(fold.dp), "n_eligible": len(elig)}
        # A1: entry threshold
        grid, neutral = cfg.grid, cfg.neutral
        thr = cfg.fixed
        if lvl >= 1:
            scores = [_t(is_df(x, neutral), cfg.min_is_trades) for x in grid]
            thr = grid[select_threshold(scores)]
            d["entry_scores"] = [None if np.isnan(v) else round(float(v), 4) for v in scores]
        d["threshold"] = thr
        # A3: exit
        ex = neutral
        if lvl >= 3:
            t0 = _t(is_df(thr, neutral), cfg.min_is_trades)
            cand = [(e, _t(is_df(thr, e), cfg.min_is_trades)) for e in cfg.exit_lib if e != neutral]
            cand = [(e, v) for e, v in cand if not np.isnan(v)]
            if cand and not np.isnan(t0):
                best, tb = max(cand, key=lambda c: c[1])
                i = grid.index(thr)
                nbs = [grid[j] for j in (i - 1, i + 1) if 0 <= j < len(grid)]
                plateau_ok = tb > 0 and all(
                    _t(is_df(nb, best), cfg.min_is_trades) >= cfg.plateau_ratio * tb for nb in nbs)
                d["exit_candidate"] = best.id
                d["exit_plateau_ok"] = bool(plateau_ok)
                if tb > t0 + cfg.exit_margin and plateau_ok:
                    ex = best
        d["exit"] = ex.id
        # A4: at most one optional filter
        chosen: tuple = ()
        if lvl >= 4:
            base = is_df(thr, ex)
            tb = _t(base, cfg.min_is_trades)
            passing = []
            for f in cfg.filters:
                df_f = is_df(thr, ex, (f,))
                tf = _t(df_f, cfg.min_is_trades)
                if np.isnan(tf) or np.isnan(tb) or len(base) == 0:
                    continue
                keep = len(df_f) / len(base)
                yrs = years_improved(base, df_f)
                rr = random_removal_pct(base["net_pnl"].to_numpy(), len(df_f), tf, cfg.n_rand, seed=fold.index)
                if tf > tb and keep >= cfg.filter_min_keep and yrs > 0.5 and rr >= cfg.filter_rand_pct:
                    passing.append((f, tf))
            if passing:
                chosen = (max(passing, key=lambda c: c[1])[0],)
        d["filters"] = [f.id for f in cfg.structural + chosen]
        res.decisions.append(d)
        f_oos = view.oos_df(thr, ex, chosen)
        if len(f_oos):
            oos.append(f_oos.with_columns(pl.lit(fold.index).alias("fold")))
    stitched = pl.concat(oos) if oos else pl.DataFrame()
    if cfg.max_positions > 0 and len(stitched):
        stitched = simulate_capacity(stitched, cfg.max_positions, cfg.max_new_per_day, cfg.capital,
                                     cache.notional, cfg.ranker)
    res.oos_trades = stitched
    res.stats = {**trade_stats(stitched), **equity_stats(stitched)}
    if registry is not None:
        tid = registry.record_trial(cfg.rid, asdict(cfg), cache.data_version, CODE_VERSION, res.stats)
        for dd in res.decisions:
            registry.record_fold(tid, dd["fold"], dd["dp"], dd)
    return res
