"""Position sizing and risk overlays (design ch. 7.6, roadmap package 4). Every rule is causal.

Trade level (inside a row, applied to the stitched OOS trades after capacity):
- `vol_weights`: weight = clip(target_vol / sigma_t, 0, w_max), where sigma_t is the symbol's trailing standard
  deviation of bar log returns on the adjusted series ending AT the signal bar (known at the signal close), the
  larger of a `window`-bar and a 5-bar estimate (entries cluster at volatility shocks, see `signal_vol`).
  Every pnl column and the share count scale with the weight, so costs scale with notional.
- `exposure_cap`: gross open notional never exceeds `max_gross` x capital. Trades are taken in entry order; a
  trade that does not fit is scaled down to the room left (skipped if there is none). Only positions already
  open at the entry bar are used.

Daily level (a row's or the combined policy's daily pnl):
- `vol_target_daily`: leverage for day t = clip(target_daily / sd(pnl[t-lookback .. t-1]), 0, lev_max)
- `drawdown_brake`: exposure `cut` while the drawdown known at the previous close exceeds `dd_limit`, back to 1
  once it recovers below `dd_limit * resume`

None of these are free: each variant is a registry trial and is accepted only if it beats fixed sizing with a
paired block-bootstrap CI (evaluation/sizing_ablation.py).
"""
from __future__ import annotations

import heapq

import numpy as np
import polars as pl

PNL_COLS = ("gross_pnl", "cost", "dividends", "financing", "net_pnl", "shares")


def trailing_vol(close: np.ndarray, window: int) -> np.ndarray:
    """Std of log returns over the `window` returns ending at bar i (NaN until enough history)."""
    r = np.r_[np.nan, np.diff(np.log(close))]
    out = np.full(len(close), np.nan)
    if len(close) <= window:
        return out
    x = np.nan_to_num(r)
    c1 = np.cumsum(np.r_[0.0, x])
    c2 = np.cumsum(np.r_[0.0, x * x])
    n = window
    s1 = c1[n + 1:] - c1[1:-n]           # sums of r[i-n+1 .. i] for i = n .. len-1
    s2 = c2[n + 1:] - c2[1:-n]
    var = np.maximum(s2 / n - (s1 / n) ** 2, 0.0) * n / (n - 1)
    out[n:] = np.sqrt(var)
    return out


def _scale(df: pl.DataFrame, w: np.ndarray) -> pl.DataFrame:
    ws = pl.Series("_w", w)
    return df.with_columns([(pl.col(c) * ws) for c in PNL_COLS if c in df.columns])


def signal_vol(cache, trades: pl.DataFrame, window: int = 20, fast: int = 5) -> np.ndarray:
    """Trailing volatility of each trade's symbol at its signal bar: max(vol over `window`, vol over `fast`).

    The fast leg matters: entries cluster exactly at volatility shocks (an RSI(2) crash is a big move), where a
    20-bar estimate still reports the calm past and would lever the position up into the shock.
    fast=0 uses the slow window only."""
    out = np.full(len(trades), np.nan)
    if len(trades) == 0:
        return out
    store = cache.__dict__.setdefault("_sizing_vol", {})
    syms = trades["symbol"].to_numpy()
    sig = trades["signal_date"].to_numpy()
    for s in np.unique(syms):
        a = cache.arrays[s]
        key = (s, window, fast, cache.data_version)
        if key not in store:
            v = trailing_vol(a.sig_close, window)
            store[key] = np.fmax(v, trailing_vol(a.sig_close, fast)) if fast > 1 else v
        m = syms == s
        idx = np.searchsorted(a.dates, sig[m].astype(a.dates.dtype))
        out[m] = store[key][np.clip(idx, 0, len(a.dates) - 1)]
    return out


def vol_weights(cache, trades: pl.DataFrame, target_vol: float = 0.015, window: int = 20,
                w_max: float = 3.0, fast: int = 5) -> tuple[pl.DataFrame, np.ndarray]:
    """Scale each trade to a common per-bar volatility; symbols without enough history get weight 1."""
    if len(trades) == 0:
        return trades, np.zeros(0)
    sv = signal_vol(cache, trades, window, fast)
    w = np.where(np.isfinite(sv) & (sv > 0), np.clip(target_vol / np.where(sv > 0, sv, 1.0), 0.0, w_max), 1.0)
    return _scale(trades, w), w


def exposure_cap(trades: pl.DataFrame, capital: float, max_gross: float) -> pl.DataFrame:
    """Gross open notional <= max_gross x capital at every entry; scales or skips trades that do not fit."""
    if len(trades) == 0 or max_gross <= 0:
        return trades
    df = trades.sort(["entry_date", "symbol"])
    ent, ex = df["entry_date"].to_list(), df["exit_date"].to_list()
    notion = (df["shares"].abs() * df["entry_px"]).to_numpy()
    cap = max_gross * capital
    heap: list[tuple] = []
    open_n = 0.0
    w = np.zeros(len(df))
    for k in range(len(df)):
        while heap and heap[0][0] <= ent[k]:
            open_n -= heapq.heappop(heap)[2]
        room = cap - open_n
        if room <= 1e-9 or notion[k] <= 0:
            continue
        w[k] = min(1.0, room / notion[k])
        used = w[k] * notion[k]
        open_n += used
        heapq.heappush(heap, (ex[k], k, used))
    keep = w > 0
    return _scale(df.filter(pl.Series(keep)), w[keep])


def max_gross_exposure(trades: pl.DataFrame) -> float:
    """Largest gross open notional over the trade stream (exits before entries on the same bar)."""
    if len(trades) == 0:
        return 0.0
    n = (trades["shares"].abs() * trades["entry_px"]).to_numpy()
    ev = sorted([(e, 1, v) for e, v in zip(trades["entry_date"].to_list(), n)]
                + [(x, 0, -v) for x, v in zip(trades["exit_date"].to_list(), n)], key=lambda t: (t[0], t[1]))
    cur = best = 0.0
    for _, _, v in ev:
        cur += v
        best = max(best, cur)
    return best


def vol_target_daily(daily: np.ndarray, target_daily: float, lookback: int = 63, lev_max: float = 2.0,
                     min_obs: int = 20) -> tuple[np.ndarray, np.ndarray]:
    """Daily-pnl overlay: leverage from the realised volatility of days strictly before t (1 until min_obs)."""
    lev = np.ones(len(daily))
    for t in range(len(daily)):
        past = daily[max(0, t - lookback):t]
        if len(past) >= min_obs:
            sd = past.std(ddof=1)
            lev[t] = float(np.clip(target_daily / sd, 0.0, lev_max)) if sd > 0 else lev_max
    return daily * lev, lev


def drawdown_brake(daily: np.ndarray, capital: float, dd_limit: float = 0.15, cut: float = 0.5,
                   resume: float = 0.5) -> tuple[np.ndarray, np.ndarray]:
    """Scale exposure to `cut` while the drawdown at the previous close exceeds dd_limit (hysteresis: resume
    below dd_limit * resume). The drawdown is measured on the braked equity itself, as it would be live."""
    exp = np.ones(len(daily))
    out = np.zeros(len(daily))
    eq, peak, braked = capital, capital, False
    for t in range(len(daily)):
        dd = (peak - eq) / capital
        if not braked and dd > dd_limit:
            braked = True
        elif braked and dd < dd_limit * resume:
            braked = False
        exp[t] = cut if braked else 1.0
        out[t] = daily[t] * exp[t]
        eq += out[t]
        peak = max(peak, eq)
    return out, exp


def apply_row_sizing(trades: pl.DataFrame, cache, cfg) -> pl.DataFrame:
    """The row's trade-level sizing (LadderConfig.sizing / max_gross), after capacity. 'fixed' is a no-op."""
    if len(trades) == 0:
        return trades
    if getattr(cfg, "sizing", "fixed") == "vol":
        trades, _ = vol_weights(cache, trades, cfg.target_vol, cfg.vol_window, cfg.w_max,
                                getattr(cfg, "vol_fast", 5))
    elif getattr(cfg, "sizing", "fixed") != "fixed":
        raise ValueError(f"unknown sizing {cfg.sizing}")
    if getattr(cfg, "max_gross", 0.0) > 0:
        trades = exposure_cap(trades, cfg.capital, cfg.max_gross)
    return trades
