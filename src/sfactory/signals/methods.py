"""Entry methods for rows (design 7.3, 13.2): mean reversion and trend following, buy and sell (mirrored), plus
the diverse families of the edge taxonomy: volatility (VOL), cross-sectional momentum (XS), calendar (CAL) and
events (EV).

Every method returns (entry, score) arrays computed only from data up to t: the adjusted signal series and, for
the families that need it, a `SeriesCtx` (bar dates, adjusted open, dividend and index-addition events at t).
All price rules are scale-invariant (ratios, relative highs/lows, homogeneous recursions), as required by the
backward dividend adjustment. `score` feeds the daily ranker (lower = stronger signal); for XS it IS the edge:
with a capacity limit the ranker keeps the strongest cross-section of the day.
TF methods also provide a `reverse_exit` state used as the neutral TF exit.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from sfactory.signals.indicators import (
    atr_wilder,
    rolling_max_prev,
    rolling_mid,
    rolling_min_prev,
    rolling_pct_rank,
    rsi_wilder,
    sma,
    supertrend_dir,
)

FAMILY = {"rsi": "MR", "ibs": "MR", "consec": "MR", "lowest_close": "MR", "donchian_low": "MR",
          "ma_cross": "TF", "donchian_break": "TF", "supertrend": "TF", "ichimoku": "TF",
          "vol_spike": "VOL", "squeeze": "VOL", "xs_mom": "XS", "tom": "CAL", "post_exdiv": "EV",
          "index_add": "EV"}

GRID = {
    "rsi": (5.0, 10.0, 15.0, 20.0, 25.0),
    "ibs": (0.1, 0.15, 0.2, 0.25, 0.3),
    "consec": (2.0, 3.0, 4.0, 5.0),
    "lowest_close": (3.0, 5.0, 7.0, 10.0, 15.0),
    "donchian_low": (5.0, 10.0, 15.0, 20.0, 30.0),
    "ma_cross": (20.0, 50.0, 100.0, 150.0, 200.0),
    "donchian_break": (20.0, 40.0, 55.0, 80.0, 100.0),
    "supertrend": (1.5, 2.0, 2.5, 3.0, 4.0),
    "ichimoku": (0.5, 0.75, 1.0, 1.5, 2.0),   # scale of (9, 26, 52)
    "vol_spike": (1.5, 2.0, 2.5, 3.0),        # true range / previous ATR(20)
    "squeeze": (0.1, 0.15, 0.2, 0.25, 0.3),   # ATR% percentile (252 bars) of the previous bar
    "xs_mom": (3.0, 6.0, 9.0, 12.0),          # formation months (skip the last month)
    "tom": (1.0, 2.0, 3.0, 4.0),              # weekdays left in the month at the signal
    "post_exdiv": (1.0,),                     # no parameter
    "index_add": (1.0,),                      # no parameter
}
DEFAULT = {"rsi": 10.0, "ibs": 0.2, "consec": 3.0, "lowest_close": 5.0, "donchian_low": 10.0,
           "ma_cross": 100.0, "donchian_break": 55.0, "supertrend": 3.0, "ichimoku": 1.0,
           "vol_spike": 2.0, "squeeze": 0.2, "xs_mom": 12.0, "tom": 2.0, "post_exdiv": 1.0, "index_add": 1.0}
NEEDS_CTX = {"xs_mom": "dates", "tom": "dates", "post_exdiv": "div", "index_add": "index_add"}


@dataclass(frozen=True)
class EntrySpec:
    method: str
    param: float
    direction: int = 1

    @property
    def id(self) -> str:
        return f"{self.method}:{self.param:g}:{self.direction:+d}"

    @property
    def family(self) -> str:
        return FAMILY[self.method]


@dataclass(frozen=True)
class SeriesCtx:
    """Per-bar context for the families that need more than prices; every field is known at bar t."""
    dates: np.ndarray | None = None       # bar dates (datetime64[D] or [us])
    sig_open: np.ndarray | None = None    # adjusted open
    div: np.ndarray | None = None         # dividend amount on the first bar of the ex-date
    index_add: np.ndarray | None = None   # True on the first bar of an index membership spell

    def upto(self, i: int) -> SeriesCtx:
        """The context as known at bar i (for live decisions on a truncated history)."""
        def cut(x):
            return None if x is None else x[: i + 1]
        return SeriesCtx(cut(self.dates), cut(self.sig_open), cut(self.div), cut(self.index_add))


def _ret(c: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(c), np.nan)
    out[n:] = c[n:] / c[:-n] - 1
    return out


def _events(state: np.ndarray) -> np.ndarray:
    ev = np.zeros(len(state), dtype=np.bool_)
    ev[1:] = state[1:] & ~state[:-1]
    return ev


def _nan_false(x):
    return np.where(np.isnan(x), False, x).astype(np.bool_)


def _prev(x: np.ndarray) -> np.ndarray:
    out = np.full(len(x), np.nan)
    out[1:] = x[:-1]
    return out


def _ichimoku_state(h, lo, c, scale: float):
    t, k, b = (max(2, round(9 * scale)), max(3, round(26 * scale)), max(4, round(52 * scale)))
    tenkan, kijun = rolling_mid(h, lo, t), rolling_mid(h, lo, k)
    span_a = (tenkan + kijun) / 2
    span_b = rolling_mid(h, lo, b)
    shift = k
    a_s = np.full(len(c), np.nan)
    b_s = np.full(len(c), np.nan)
    a_s[shift:], b_s[shift:] = span_a[:-shift], span_b[:-shift]
    top = np.fmax(a_s, b_s)
    bot = np.fmin(a_s, b_s)
    return tenkan, kijun, top, bot


def weekdays_left_in_month(dates: np.ndarray) -> np.ndarray:
    """Weekdays after the bar's day up to the month end (a fixed calendar, known in advance)."""
    days = dates.astype("datetime64[D]")
    nxt = (days.astype("datetime64[M]") + np.timedelta64(1, "M")).astype("datetime64[D]")
    return np.busday_count(days + np.timedelta64(1, "D"), nxt)


def _need(ctx: SeriesCtx | None, method: str):
    field = NEEDS_CTX[method]
    if ctx is None or getattr(ctx, field) is None:
        raise ValueError(f"method {method} needs SeriesCtx.{field}")
    return getattr(ctx, field)


def entry_and_score(spec: EntrySpec, h, lo, c, ctx: SeriesCtx | None = None) -> tuple[np.ndarray, np.ndarray]:
    m, p, d = spec.method, spec.param, spec.direction
    buy = d == 1
    if m == "rsi":
        r = rsi_wilder(c, 2)
        return _nan_false(np.where(np.isnan(r), np.nan, (r < p) if buy else (r > 100 - p))), (r if buy else 100 - r)
    if m == "ibs":
        rng = h - lo
        ibs = np.where(rng > 0, (c - lo) / np.where(rng > 0, rng, 1), 0.5)
        return (ibs < p) if buy else (ibs > 1 - p), (ibs if buy else 1 - ibs)
    if m == "consec":
        n = int(p)
        down = np.zeros(len(c), dtype=np.bool_)
        down[1:] = (c[1:] < c[:-1]) if buy else (c[1:] > c[:-1])
        run = np.zeros(len(c))
        for i in range(1, len(c)):
            run[i] = run[i - 1] + 1 if down[i] else 0
        return run >= n, d * _ret(c, n)
    if m == "lowest_close":
        n = int(p)
        ref = rolling_min_prev(c, n - 1) if buy else rolling_max_prev(c, n - 1)
        return _nan_false(np.where(np.isnan(ref), np.nan, (c < ref) if buy else (c > ref))), d * _ret(c, n)
    if m == "donchian_low":
        n = int(p)
        ref = rolling_min_prev(lo, n) if buy else rolling_max_prev(h, n)
        return _nan_false(np.where(np.isnan(ref), np.nan, (lo <= ref) if buy else (h >= ref))), d * _ret(c, n)
    # --- trend following: entries are events (state switches on) ---
    if m == "ma_cross":
        s = sma(c, int(p))
        state = _nan_false(np.where(np.isnan(s), np.nan, (c > s) if buy else (c < s)))
        return _events(state), -d * _ret(c, 20)
    if m == "donchian_break":
        n = int(p)
        ref = rolling_max_prev(h, n) if buy else rolling_min_prev(lo, n)
        state = _nan_false(np.where(np.isnan(ref), np.nan, (c > ref) if buy else (c < ref)))
        return state, -d * _ret(c, 20)
    if m == "supertrend":
        st = supertrend_dir(h, lo, c, atr_wilder(h, lo, c, 10), p)
        return _events(st == (1.0 if buy else -1.0)), -d * _ret(c, 20)
    if m == "ichimoku":
        tenkan, kijun, top, bot = _ichimoku_state(h, lo, c, p)
        cond = (c > top) & (tenkan > kijun) if buy else (c < bot) & (tenkan < kijun)
        return _events(_nan_false(np.where(np.isnan(top), np.nan, cond))), -d * _ret(c, 20)
    # --- volatility (VOL) ---
    if m == "vol_spike":            # fade a range-expansion bar that closes at its extreme against the move
        pc = _prev(c)
        tr = np.fmax(h - lo, np.fmax(np.abs(h - pc), np.abs(lo - pc)))
        ratio = tr / _prev(atr_wilder(h, lo, c, 20))
        rng = h - lo
        ibs = np.where(rng > 0, (c - lo) / np.where(rng > 0, rng, 1), 0.5)
        side = (ibs < 0.3) & (c < pc) if buy else (ibs > 0.7) & (c > pc)
        return _nan_false(np.where(np.isnan(ratio), np.nan, (ratio >= p) & side)), -np.nan_to_num(ratio)
    if m == "squeeze":              # breakout out of a volatility contraction
        pr = _prev(rolling_pct_rank(atr_wilder(h, lo, c, 14) / c, 252))
        ref = rolling_max_prev(h, 20) if buy else rolling_min_prev(lo, 20)
        brk = (c > ref) if buy else (c < ref)
        state = _nan_false(np.where(np.isnan(pr) | np.isnan(ref), np.nan, (pr <= p) & brk))
        return _events(state), -d * _ret(c, 20)
    # --- cross-sectional momentum (XS): rebalance on the first bar of each month ---
    if m == "xs_mom":
        dates = _need(ctx, m)
        mon = dates.astype("datetime64[M]")
        first = np.zeros(len(c), dtype=np.bool_)
        first[1:] = mon[1:] != mon[:-1]
        look, skip = int(p) * 21, 21
        mom = np.full(len(c), np.nan)
        if len(c) > look:
            mom[look:] = c[look - skip:len(c) - skip] / c[:len(c) - look] - 1
        return first & ~np.isnan(mom), -d * np.nan_to_num(mom)
    # --- calendar (CAL): turn of the month ---
    if m == "tom":
        left = weekdays_left_in_month(_need(ctx, m))
        return _events(left == int(p)), d * np.nan_to_num(_ret(c, 5))
    # --- events (EV) ---
    if m == "post_exdiv":           # the ex-date drop is mechanical, the reaction around it is not
        div = _need(ctx, m)
        return div > 0, -np.where(c > 0, div / np.where(c > 0, c, 1), 0.0)
    if m == "index_add":
        add = _need(ctx, m).astype(np.bool_)
        return add.copy(), d * np.nan_to_num(_ret(c, 5))
    raise ValueError(m)


def reverse_exit(spec: EntrySpec, h, lo, c) -> np.ndarray:
    """Neutral TF exit: the method's opposite state (design 7.4)."""
    m, p, buy = spec.method, spec.param, spec.direction == 1
    if m == "ma_cross":
        s = sma(c, int(p))
        return _nan_false(np.where(np.isnan(s), np.nan, (c < s) if buy else (c > s)))
    if m == "donchian_break":
        n = max(2, int(p) // 2)
        ref = rolling_min_prev(lo, n) if buy else rolling_max_prev(h, n)
        return _nan_false(np.where(np.isnan(ref), np.nan, (c < ref) if buy else (c > ref)))
    if m == "supertrend":
        st = supertrend_dir(h, lo, c, atr_wilder(h, lo, c, 10), p)
        return st == (-1.0 if buy else 1.0)
    if m == "ichimoku":
        _, kijun, _, _ = _ichimoku_state(h, lo, c, p)
        return _nan_false(np.where(np.isnan(kijun), np.nan, (c < kijun) if buy else (c > kijun)))
    raise ValueError(f"{m} has no reverse exit (MR method)")
