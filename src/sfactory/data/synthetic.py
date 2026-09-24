"""Deterministic synthetic market data for tests (random walk and mean-reverting variants)."""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl


def business_days(start: date, n: int) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def make_market(n_symbols: int = 20, n_days: int = 2600, seed: int = 7, kind: str = "random_walk",
                start: date = date(2010, 1, 4), mr_strength: float = 0.25):
    """Returns (bars, dividends, membership). kind: 'random_walk' | 'mean_revert' | 'trending'.

    Split-only prices drop by the dividend amount on ex-dates, like real data.
    Some symbols join late and some are delisted early (their data stops).
    """
    rng = np.random.default_rng(seed)
    days = business_days(start, n_days)
    bars, divs, mem = [], [], []
    for i in range(n_symbols):
        sym = f"S{i:03d}"
        vol = rng.uniform(0.01, 0.025)
        eps = rng.normal(0, vol, n_days)
        r = eps.copy()
        if kind == "mean_revert":
            for t in range(1, n_days):
                r[t] = eps[t] - mr_strength * r[t - 1]
        elif kind == "trending":  # slow-moving drift regimes -> exploitable trends
            drift = np.zeros(n_days)
            for t in range(1, n_days):
                drift[t] = 0.99 * drift[t - 1] + rng.normal(0, vol * 0.02)
            r = eps + drift
        s_idx = int(rng.integers(1, n_days // 4)) if i % 4 == 0 else 0
        e_idx = int(rng.integers(3 * n_days // 4, n_days)) if i % 5 == 0 else n_days
        div_idx = list(range(s_idx + 60, e_idx, 63))
        close = np.empty(n_days)
        level = 50.0
        div_amt = {}
        for t in range(n_days):
            level *= np.exp(r[t])
            if t in div_idx:
                amt = level * 0.005
                div_amt[t] = amt
                level -= amt
            close[t] = level
        gap = rng.normal(0, vol * 0.3, n_days)
        open_ = np.r_[close[0], close[:-1] * np.exp(gap[1:])]
        for t, amt in div_amt.items():
            open_[t] -= amt
        hi = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol * 0.5, n_days)))
        lo = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol * 0.5, n_days)))
        volu = rng.uniform(5e5, 5e6, n_days)
        sl = slice(s_idx, e_idx)
        bars.append(pl.DataFrame({"symbol": sym, "date": days[sl], "open": open_[sl], "high": hi[sl],
                                  "low": lo[sl], "close": close[sl], "volume": volu[sl]}))
        mem.append({"symbol": sym, "start": days[s_idx], "end": days[e_idx] if e_idx < n_days else None})
        divs += [{"symbol": sym, "ex_date": days[t], "amount": float(a)} for t, a in div_amt.items()]
    return (pl.concat(bars),
            pl.DataFrame(divs, schema={"symbol": pl.Utf8, "ex_date": pl.Date, "amount": pl.Float64}),
            pl.DataFrame(mem, schema={"symbol": pl.Utf8, "start": pl.Date, "end": pl.Date}))
