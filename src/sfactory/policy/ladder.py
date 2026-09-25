"""Row with the full in-fold ablation ladder (design 9.2): A0 fixed -> A1 entry -> A3 exit -> A4 filter.

Any entry method (signals.methods: MR rsi/ibs/consec/lowest_close/donchian_low, TF ma_cross/donchian_break/
supertrend/ichimoku, and the diverse families VOL/XS/CAL/EV), buy or sell. Grid, default value, exit library and
neutral exit follow the method (signals.specs.neutral_exit_for / exit_library_for).

Per decision point, using IS trades only:
  A1  entry threshold by plateau-lite over the grid (neutral exit, no optional filter)
  A3  exit from the library; accepted only if it beats the neutral exit by `exit_margin` (t-stat units)
      AND survives the joint-plateau check (neighbouring thresholds keep >= plateau_ratio of its IS t-stat)
  A4  at most one optional filter; accepted only if it improves the IS t-stat, keeps >= filter_min_keep of the
      trades, improves expectancy in most IS years, and beats random removal of the same number of trades
Structural filters (design ch. 12) are fixed, always applied and never selected in-fold.
Capacity/ranking (S8) is applied to the stitched OOS stream when max_positions > 0; trade-level sizing
(vol targeting, gross exposure cap - portfolio/sizing.py) after it, when the row asks for it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
import polars as pl

from sfactory.data.universe import universe_at
from sfactory.engine.cache import TradeCache
from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.policy.rsi_row import select_threshold
from sfactory.portfolio.capacity import simulate_capacity
from sfactory.portfolio.sizing import apply_row_sizing
from sfactory.registry.repo import Registry
from sfactory.signals.methods import DEFAULT, FAMILY, GRID, EntrySpec
from sfactory.signals.specs import MR_FILTER_LIBRARY, ExitSpec, FilterSpec, exit_library_for, neutral_exit_for
from sfactory.timeline.folds import FoldManager

CODE_VERSION = "0.6.0"
RUNG_LEVEL = {"A0": 0, "A1": 1, "A3": 3, "A4": 4}


@dataclass(frozen=True)
class LadderConfig:
    rung: str = "A0"
    method: str = "rsi"
    direction: int = 1
    row_id: str | None = None          # default: <family>-<METHOD>-<BUY|SELL>-EQ
    thresholds: tuple | None = None    # entry grid; default signals.methods.GRID[method]
    fixed_threshold: float | None = None
    exits: tuple | None = None         # default: the method's exit library
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
    entry_delay: int = 0               # robustness only: execution delay in bars (0 in every real policy)
    # S7 symbol selection (symbol-based rows): keep the top-N symbols by IS t-stat with t >= symbol_min_t
    symbol_select: int = 0             # 0 = pooled universe row (no per-symbol selection)
    symbol_min_t: float = 1.0
    symbol_min_trades: int = 10
    symbol_ranker: str = "is_tstat"    # is_tstat | random (benchmark only)
    symbol_seed: int = 0
    asset_class: str = "equities"
    universe_mode: str = "membership"  # membership | top_liquidity (no index file available)
    universe_top_n: int = 500
    # sizing (package 4): fixed = equal notional per trade; vol = per-trade weight target_vol / trailing vol
    sizing: str = "fixed"
    target_vol: float = 0.015          # per-bar volatility a position is scaled to
    vol_window: int = 20
    vol_fast: int = 5                  # sigma = max(vol over vol_window, vol over vol_fast); 0 = slow only
    w_max: float = 3.0
    max_gross: float = 0.0             # > 0: gross open notional <= max_gross x capital

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
        return neutral_exit_for(self.method)

    @property
    def exit_lib(self) -> tuple:
        return tuple(self.exits) if self.exits is not None else exit_library_for(self.method)

    @property
    def rid(self) -> str:
        side = "BUY" if self.direction == 1 else "SELL"
        suffix = {"equities": "EQ", "fx": "FX", "indices": "IX", "metals": "MT"}.get(self.asset_class, "X")
        sel = f"-TOP{self.symbol_select}" if self.symbol_select else ""
        sz = ("-VOL" if self.sizing == "vol" else "") + (f"-CAP{self.max_gross:g}" if self.max_gross > 0 else "")
        return self.row_id or f"{self.family}-{self.method.upper()}-{side}-{suffix}{sel}{sz}"


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
        return [self.cache.trades(s, spec, ex, c.structural + fl, c.entry_delay) for s in self.elig]

    def stacked(self, thr: float, ex: ExitSpec, fl: tuple = ()) -> pl.DataFrame:
        """All eligible symbols' trades for one setting, stacked once (symbols in eligibility order), so every
        time slice is one filter instead of one per symbol."""
        key = ("stack", thr, ex.id, tuple(f.id for f in fl))
        if key not in self.memo:
            self.memo[key] = _concat(self.trades(thr, ex, fl))
        return self.memo[key]

    def is_df(self, thr: float, ex: ExitSpec, fl: tuple = ()) -> pl.DataFrame:
        key = (thr, ex.id, tuple(f.id for f in fl))
        if key not in self.memo:
            st = self.stacked(thr, ex, fl)
            self.memo[key] = self.fm.slice_is(st, self.fold) if len(st) else st
        return self.memo[key]

    def oos_df(self, thr: float, ex: ExitSpec, fl: tuple, symbols: list | None = None) -> pl.DataFrame:
        st = self.stacked(thr, ex, fl)
        if len(st) == 0:
            return st
        out = self.fm.slice_oos(st, self.fold)
        return out.filter(pl.col("symbol").is_in(list(symbols))) if symbols is not None else out

    def per_symbol_is(self, thr: float, ex: ExitSpec, fl: tuple) -> dict:
        df = self.is_df(thr, ex, fl)
        parts = df.partition_by("symbol", as_dict=True) if len(df) else {}
        empty = df.clear() if len(df) else pl.DataFrame()
        return {s: parts.get((s,), empty) for s in self.elig}


def select_symbols(view: _FoldView, cfg: LadderConfig, thr: float, ex: ExitSpec, fl: tuple) -> tuple[list, dict]:
    """S7: per-symbol IS t-stat with the row's chosen settings; top-N with t >= min_t (random = benchmark)."""
    stats = {}
    for s, df in view.per_symbol_is(thr, ex, fl).items():
        st = trade_stats(df)
        stats[s] = st["t_stat"] if st["n"] >= cfg.symbol_min_trades else np.nan
    if cfg.symbol_ranker == "random":
        rng = np.random.default_rng(cfg.symbol_seed * 100_003 + view.fold.dp.toordinal())
        pool = sorted(stats)
        chosen = sorted(rng.choice(pool, min(cfg.symbol_select, len(pool)), replace=False).tolist()) if pool else []
    else:
        ok = sorted((s for s, t in stats.items() if not np.isnan(t) and t >= cfg.symbol_min_t),
                    key=lambda s: (-stats[s], s))
        chosen = sorted(ok[: cfg.symbol_select])
    return chosen, {s: (None if np.isnan(t) else round(float(t), 4)) for s, t in stats.items()}


def cached_universe(cache, cfg, dp, bars, membership) -> list:
    """universe_at memoised on the cache: rows sharing universe settings re-use the eligible set of a DP.
    The key includes the bar frame's identity and shape, so a different (e.g. perturbed) frame never hits."""
    memo = cache.__dict__.setdefault("_universe_memo", {})
    key = (str(dp), getattr(cfg, "universe_mode", "membership"), cfg.universe_top_n, cfg.min_price,
           cfg.min_dollar_vol, cfg.min_history, id(bars), bars.height, id(membership))
    if key not in memo:
        memo[key] = universe_at(cfg, dp, bars, membership)
    return list(memo[key])


def decide_fold(view: _FoldView, cfg: LadderConfig) -> tuple[float, ExitSpec, tuple, dict]:
    """The in-fold selector for one decision point (IS only). Shared by research, holdout and live DPs."""
    fold = view.fold
    is_df = view.is_df
    lvl = cfg.level
    d = {"fold": fold.index, "dp": str(fold.dp), "n_eligible": len(view.elig)}
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
            rr = random_removal_pct(base["net_pnl"].to_numpy(), len(df_f), tf, cfg.n_rand,
                                    seed=fold.dp.toordinal())  # same seed in research, holdout and live
            if tf > tb and keep >= cfg.filter_min_keep and yrs > 0.5 and rr >= cfg.filter_rand_pct:
                passing.append((f, tf))
        if passing:
            chosen = (max(passing, key=lambda c: c[1])[0],)
    d["filters"] = [f.id for f in cfg.structural + chosen]
    if cfg.symbol_select > 0:
        syms, sym_t = select_symbols(view, cfg, thr, ex, chosen)
        d["symbols"], d["symbol_is_t"] = syms, sym_t
    return thr, ex, chosen, d


def run_ladder(fm: FoldManager, cache: TradeCache, bars_dev: pl.DataFrame, membership: pl.DataFrame,
               cfg: LadderConfig, registry: Registry | None = None, folds: list | None = None) -> LadderResult:
    """`folds` defaults to the dev folds; only the holdout stage passes dev + unlocked holdout folds."""
    res = LadderResult(cfg)
    oos = []
    for fold in (folds if folds is not None else fm.dev_folds()):
        elig = cached_universe(cache, cfg, fold.dp, bars_dev, membership)
        view = _FoldView(fm, cache, cfg, fold, elig)
        thr, ex, chosen, d = decide_fold(view, cfg)
        res.decisions.append(d)
        f_oos = view.oos_df(thr, ex, chosen, d.get("symbols"))
        if len(f_oos):
            oos.append(f_oos.with_columns(pl.lit(fold.index).alias("fold")))
    stitched = pl.concat(oos) if oos else pl.DataFrame()
    if cfg.max_positions > 0 and len(stitched):
        stitched = simulate_capacity(stitched, cfg.max_positions, cfg.max_new_per_day, cfg.capital,
                                     cache.notional, cfg.ranker)
    stitched = apply_row_sizing(stitched, cache, cfg)
    res.oos_trades = stitched
    res.stats = {**trade_stats(stitched), **equity_stats(stitched)}
    if registry is not None:
        tid = registry.record_trial(cfg.rid, asdict(cfg), cache.data_version, CODE_VERSION, res.stats)
        for dd in res.decisions:
            registry.record_fold(tid, dd["fold"], dd["dp"], dd)
    return res
