"""Edges that switch on and off (design 12.4-12.5): measure first, then four mechanisms as ablation rungs.

Slow clock: every fold (6-month DP) the row's parameters come from the shared selector `decide_fold` on IS
only, exactly as in research, holdout and live. Fast clock: inside the fold's OOS, at every `sub_months`
sub-DP (FoldManager.sub_folds), each eligible symbol is switched on/off or weighted from data strictly before
the sub-DP. Trades of a symbol with the fold's settings are its *shadow* track: they exist whether or not the
symbol is active, and are the only performance evidence the fast clock uses.

Modes
- always       every eligible symbol on (identical to run_ladder; the baseline every mechanism must beat)
- two_clock    on iff the t-stat of its shadow trades in the last `window_months` >= min_t
- shadow       equity-curve rule on the last `shadow_trades` closed shadow trades, with hysteresis
- trendiness   on iff a causal market feature (efficiency / variance ratio / autocorr) is on the side of the IS
               median chosen in-fold; if neither side beats the whole IS by `feature_margin`, no gating
- soft_weight  no switch: weight = short-window t-stat shrunk to the row mean, mapped to [0, w_max], mean 1

The persistence test (12.4) decides whether any of this is worth having: a mechanism is accepted only if the
state is persistent AND its daily OOS Sharpe beats `always` with a paired block-bootstrap CI above zero.
Works on daily and intraday bars (see docs/spec/intraday.md).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from datetime import date
from itertools import pairwise

import numpy as np
import polars as pl

from sfactory.data.universe import universe_at
from sfactory.evaluation.ablation import aligned_daily
from sfactory.metrics.core import equity_stats, trade_stats
from sfactory.policy.ladder import CODE_VERSION, LadderConfig, _FoldView, decide_fold
from sfactory.portfolio.capacity import simulate_capacity
from sfactory.portfolio.sizing import apply_row_sizing
from sfactory.signals.trendiness import feature_series
from sfactory.stats.core import sharpe_diff_ci
from sfactory.timeline.folds import FoldManager

MODES = ("always", "two_clock", "shadow", "trendiness", "soft_weight")
PNL_COLS = ("gross_pnl", "cost", "dividends", "financing", "net_pnl", "shares")


@dataclass(frozen=True)
class ActivationConfig:
    mode: str = "always"
    sub_months: int = 1
    window_months: int = 12          # two_clock / soft_weight short window (design: 12 to 18 months)
    min_trades: int = 8              # fewer shadow trades in the window -> default state
    min_t: float = 0.0
    default_on: bool = True
    shadow_trades: int = 10
    shadow_on: float = 0.0           # off -> on when the last-k sum rises above this (per cache notional)
    shadow_off: float = 0.0          # on -> off when it falls below this
    feature: str = "efficiency"
    feature_window: int = 60
    feature_margin: float = 0.25     # t-stat units a half must beat the whole IS by
    shrink_k: float = 20.0
    w_max: float = 2.0

    @property
    def id(self) -> str:
        m = self.mode
        if m == "two_clock":
            return f"two_clock:w{self.window_months}:t{self.min_t:g}:n{self.min_trades}"
        if m == "shadow":
            return f"shadow:k{self.shadow_trades}:on{self.shadow_on:g}:off{self.shadow_off:g}"
        if m == "trendiness":
            return f"trend:{self.feature}:{self.feature_window}:m{self.feature_margin:g}"
        if m == "soft_weight":
            return f"soft:w{self.window_months}:k{self.shrink_k:g}:x{self.w_max:g}"
        return "always"


@dataclass
class ActivationResult:
    config: LadderConfig
    activation: ActivationConfig
    decisions: list = field(default_factory=list)
    oos_trades: pl.DataFrame | None = None
    stats: dict = field(default_factory=dict)


def _t_n(df: pl.DataFrame) -> tuple[float, int]:
    st = trade_stats(df)
    return st["t_stat"], st["n"]


def _concat(frames):
    frames = [f for f in frames if len(f)]
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def _scale(df: pl.DataFrame, w: float) -> pl.DataFrame:
    if w == 1.0 or len(df) == 0:
        return df
    return df.with_columns([(pl.col(c) * w) for c in PNL_COLS if c in df.columns])


# --- trendiness feature helpers (causal) ----------------------------------------------------------------
def _feature(cache, sym: str, act: ActivationConfig) -> np.ndarray:
    store = cache.__dict__.setdefault("_edge_features", {})
    key = (sym, act.feature, act.feature_window, cache.data_version)
    if key not in store:
        store[key] = feature_series(act.feature, cache.arrays[sym].sig_close, act.feature_window)
    return store[key]


def feature_before(cache, sym: str, dp: date, act: ActivationConfig) -> float:
    """Feature at the last bar strictly before `dp`."""
    a = cache.arrays[sym]
    i = int(np.searchsorted(a.dates, np.datetime64(dp))) - 1
    return float(_feature(cache, sym, act)[i]) if i >= 0 else float("nan")


def feature_at_signals(cache, sym: str, trades: pl.DataFrame, act: ActivationConfig) -> np.ndarray:
    """Feature on each trade's signal bar (known at that close)."""
    if len(trades) == 0:
        return np.zeros(0)
    a = cache.arrays[sym]
    idx = np.searchsorted(a.dates, trades["signal_date"].to_numpy().astype(a.dates.dtype))
    return _feature(cache, sym, act)[np.clip(idx, 0, len(a.dates) - 1)]


def calibrate_trendiness(cache, fm: FoldManager, fold, per_sym_full: dict, act: ActivationConfig) -> dict:
    """In-fold (IS only): split IS trades at the median feature value; keep the better half if it beats all."""
    vals, pnl = [], []
    for s, t in per_sym_full.items():
        is_t = fm.slice_is(t, fold)
        if len(is_t):
            vals.append(feature_at_signals(cache, s, is_t, act))
            pnl.append(is_t["net_pnl"].to_numpy())
    if not vals:
        return {"side": "none", "threshold": None}
    v, x = np.concatenate(vals), np.concatenate(pnl)
    ok = ~np.isnan(v)
    v, x = v[ok], x[ok]
    if len(x) < 2 * act.min_trades:
        return {"side": "none", "threshold": None}
    thr = float(np.median(v))

    def t(z):
        sd = z.std(ddof=1) if len(z) > 1 else 0.0
        return float(z.mean() / (sd / math.sqrt(len(z)))) if sd > 0 else 0.0

    t_all, t_hi, t_lo = t(x), t(x[v >= thr]), t(x[v < thr])
    side = "none"
    if max(t_hi, t_lo) > t_all + act.feature_margin:
        side = "high" if t_hi >= t_lo else "low"
    return {"side": side, "threshold": round(thr, 6), "t_all": round(t_all, 4), "t_high": round(t_hi, 4),
            "t_low": round(t_lo, 4)}


# --- the fast clock -------------------------------------------------------------------------------------
def decide_sub(fm: FoldManager, cache, sub, per_sym_full: dict, act: ActivationConfig, prev_state: dict,
               trend_cal: dict | None) -> dict[str, float]:
    """Weight per eligible symbol at one sub-DP (0 = off, 1 = on); uses only trades closed before sub.dp."""
    dp = sub.dp
    syms = sorted(per_sym_full)
    if act.mode == "always":
        return {s: 1.0 for s in syms}
    if act.mode == "trendiness":
        side = trend_cal["side"] if trend_cal else "none"
        if side == "none":
            return {s: 1.0 for s in syms}
        out = {}
        for s in syms:
            f = feature_before(cache, s, dp, act)
            out[s] = 0.0 if np.isnan(f) else float((f >= trend_cal["threshold"]) == (side == "high"))
        return out
    if act.mode == "shadow":
        out = {}
        for s in syms:
            hist = fm.slice_closed_before(per_sym_full[s], dp).sort("exit_date")
            prev = prev_state.get(s, 1.0 if act.default_on else 0.0) > 0
            if len(hist) == 0:
                out[s] = 1.0 if act.default_on else 0.0
                continue
            last = float(hist["net_pnl"].tail(act.shadow_trades).sum())
            on = (last >= act.shadow_off) if prev else (last > act.shadow_on)
            out[s] = float(on)
        return out
    # two_clock and soft_weight share the short-window statistic
    tn = {s: _t_n(fm.slice_lookback(per_sym_full[s], dp, act.window_months)) for s in syms}
    if act.mode == "two_clock":
        return {s: (float(t >= act.min_t) if n >= act.min_trades else float(act.default_on))
                for s, (t, n) in tn.items()}
    if act.mode == "soft_weight":
        have = [t for t, n in tn.values() if n > 0]
        t_bar = float(np.mean(have)) if have else 0.0
        shr = {s: (n / (n + act.shrink_k)) * t + (act.shrink_k / (n + act.shrink_k)) * t_bar
               for s, (t, n) in tn.items()}
        mu = float(np.mean(list(shr.values()))) if shr else 0.0
        raw = {s: float(np.clip(1.0 + v - mu, 0.0, act.w_max)) for s, v in shr.items()}
        m = float(np.mean(list(raw.values()))) if raw else 1.0
        return {s: (float(np.clip(w / m, 0.0, act.w_max)) if m > 0 else 1.0) for s, w in raw.items()}
    raise ValueError(act.mode)


def run_activation(fm: FoldManager, cache, bars_dev: pl.DataFrame, membership, cfg: LadderConfig,
                   act: ActivationConfig | None = None, registry=None, folds: list | None = None
                   ) -> ActivationResult:
    act = act or ActivationConfig()
    res = ActivationResult(cfg, act)
    oos, state = [], {}
    for fold in (folds if folds is not None else fm.dev_folds()):
        elig = universe_at(cfg, fold.dp, bars_dev, membership)
        view = _FoldView(fm, cache, cfg, fold, elig)
        thr, ex, chosen, d = decide_fold(view, cfg)                      # slow clock: parameters
        syms = d.get("symbols", elig)
        full = dict(zip(elig, view.trades(thr, ex, chosen)))
        per_sym = {s: full[s] for s in syms}
        cal = calibrate_trendiness(cache, fm, fold, per_sym, act) if act.mode == "trendiness" else None
        subs = []
        for sub in fm.sub_folds(fold, act.sub_months):                  # fast clock: activation / weights
            w = decide_sub(fm, cache, sub, per_sym, act, state, cal)
            switches = sum(1 for s, v in w.items() if (v > 0) != (state.get(s, 1.0) > 0))
            state.update(w)
            frames = [_scale(fm.slice_oos(per_sym[s], sub), v) for s, v in w.items() if v > 0]
            f = _concat(frames)
            if len(f):
                oos.append(f.with_columns(pl.lit(fold.index).alias("fold")))
            subs.append({"dp": str(sub.dp), "n_eligible": len(w), "n_active": sum(v > 0 for v in w.values()),
                         "switches": switches,
                         "weights": {s: round(v, 4) for s, v in w.items() if v != 1.0}})
        d = {**d, "activation": act.id, "trend_calibration": cal, "sub_dps": subs}
        res.decisions.append(d)
    stitched = _concat(oos)
    if cfg.max_positions > 0 and len(stitched):
        stitched = simulate_capacity(stitched, cfg.max_positions, cfg.max_new_per_day, cfg.capital,
                                     cache.notional, cfg.ranker)
    stitched = apply_row_sizing(stitched, cache, cfg)
    res.oos_trades = stitched
    subs = [s for d in res.decisions for s in d["sub_dps"]]
    exposure = float(np.mean([s["n_active"] / s["n_eligible"] for s in subs if s["n_eligible"]])) if subs else 0.0
    res.stats = {**trade_stats(stitched), **equity_stats(stitched), "exposure": exposure,
                 "switches": int(sum(s["switches"] for s in subs))}
    if registry is not None:
        tid = registry.record_trial(f"{cfg.rid}|{act.id}", {**asdict(cfg), "activation": asdict(act)},
                                    cache.data_version, CODE_VERSION, res.stats)
        for dd in res.decisions:
            registry.record_fold(tid, dd["fold"], dd["dp"], dd)
    return res


# --- measure first (design 12.4) ------------------------------------------------------------------------
def _spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 3:
        return float("nan")
    ra, rb = np.argsort(np.argsort(a)), np.argsort(np.argsort(b))
    if ra.std() == 0 or rb.std() == 0:
        return float("nan")
    return float(np.corrcoef(ra, rb)[0, 1])


def _summary(rhos: list[float]) -> dict:
    r = np.array([x for x in rhos if not np.isnan(x)])
    if len(r) < 2:
        return {"mean_rho": None, "t": None, "share_positive": None, "n_pairs": len(r)}
    sd = r.std(ddof=1)
    return {"mean_rho": round(float(r.mean()), 4),
            "t": round(float(r.mean() / (sd / math.sqrt(len(r)))), 3) if sd > 0 else None,
            "share_positive": round(float((r > 0).mean()), 3), "n_pairs": len(r)}


def state_persistence(fm: FoldManager, cache, bars_dev, membership, cfg: LadderConfig,
                      act: ActivationConfig | None = None, min_trades: int = 5, t_crit: float = 2.0) -> dict:
    """Two pre-registered questions, both on OOS shadow trades with the row's in-fold settings.

    fold_to_fold: Spearman rho across symbols between per-trade expectancy in fold k and fold k+1, plus
      P(on in k+1 | on in k) - P(on in k+1) with on = expectancy > 0.
    window_to_next: at every sub-DP, rho between the trailing `window_months` t-stat (what two_clock and
      soft_weight act on) and the next sub-period's expectancy. This is the direct test of the fast clock.
    Verdict 'persistent' iff the window_to_next mean rho > 0 with t >= t_crit across sub-DPs.
    """
    act = act or ActivationConfig()
    per_fold, pred = [], []
    for fold in fm.dev_folds():
        elig = universe_at(cfg, fold.dp, bars_dev, membership)
        view = _FoldView(fm, cache, cfg, fold, elig)
        thr, ex, chosen, d = decide_fold(view, cfg)
        syms = d.get("symbols", elig)
        full = dict(zip(elig, view.trades(thr, ex, chosen)))
        exp = {}
        for s in syms:
            o = fm.slice_oos(full[s], fold)
            if len(o) >= min_trades:
                exp[s] = float(o["net_pnl"].mean())
        per_fold.append(exp)
        for sub in fm.sub_folds(fold, act.sub_months):
            xs, ys = [], []
            for s in syms:
                t, n = _t_n(fm.slice_lookback(full[s], sub.dp, act.window_months))
                nxt = fm.slice_oos(full[s], sub)
                if n >= act.min_trades and len(nxt):
                    xs.append(t)
                    ys.append(float(nxt["net_pnl"].mean()))
            pred.append(_spearman(np.array(xs), np.array(ys)))
    rhos, lift = [], []
    for a, b in pairwise(per_fold):
        common = sorted(set(a) & set(b))
        if len(common) >= 5:
            xa, xb = np.array([a[s] for s in common]), np.array([b[s] for s in common])
            rhos.append(_spearman(xa, xb))
            on_a, on_b = xa > 0, xb > 0
            if on_a.any():
                lift.append(float(on_b[on_a].mean() - on_b.mean()))
    f2f, w2n = _summary(rhos), _summary(pred)
    persistent = bool(w2n["mean_rho"] is not None and w2n["t"] is not None and w2n["mean_rho"] > 0
                      and w2n["t"] >= t_crit)
    return {"fold_to_fold": {**f2f, "on_lift": round(float(np.mean(lift)), 4) if lift else None},
            "window_to_next": w2n, "t_crit": t_crit, "verdict": "persistent" if persistent else "not_persistent"}


# --- ablation report ------------------------------------------------------------------------------------
def run_edge_state(fm: FoldManager, cache, bars_dev, membership, cfg: LadderConfig,
                   base: ActivationConfig | None = None, modes=MODES, registry=None,
                   n_boot: int = 1000, block: int = 20) -> dict:
    """Persistence test + every mechanism vs `always` (each mechanism is one registry trial)."""
    base = base or ActivationConfig()
    pers = state_persistence(fm, cache, bars_dev, membership, cfg, base)
    results = {m: run_activation(fm, cache, bars_dev, membership, cfg, replace(base, mode=m), registry)
               for m in modes}
    folds = fm.dev_folds()
    mat = aligned_daily([results[m].oos_trades for m in modes], folds[0].dp, folds[-1].oos_end)
    b = list(modes).index("always")
    table = []
    for i, m in enumerate(modes):
        ci = None if i == b else sharpe_diff_ci(mat[:, i], mat[:, b], n_boot=n_boot, block=block, seed=i)
        accepted = i != b and pers["verdict"] == "persistent" and ci[0] > 0
        st = results[m].stats
        table.append({"mode": m, "id": results[m].activation.id, "n": st["n"], "sharpe": st["sharpe"],
                      "max_dd": st["max_dd"], "exposure": st["exposure"], "switches": st["switches"],
                      "sharpe_diff_ci_daily": ci, "accepted": bool(accepted)})
    acc = [r["mode"] for r in table if r["accepted"]]
    return {"row": cfg.rid, "persistence": pers, "table": table, "accepted": acc,
            "note": "a mechanism needs a persistent state AND a paired bootstrap CI above zero vs 'always'",
            "results": results}
