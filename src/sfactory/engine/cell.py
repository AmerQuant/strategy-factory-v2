"""Cell engine (ADR-0002): one symbol x one rule -> list of trades, computed once over full history.

Execution contract: signal on close of bar t (signal series) -> fill at OPEN of bar t+1 (split-only series).
Exit signal on close of bar j -> fill at open of j+1. If the next bar is missing (delisting / data end),
the position is closed at the last available close (forced_exit=1).
Costs: `cost_bps` per side on traded notional. Dividends: held over an ex-date => direction * shares * amount.
"""
from __future__ import annotations

import numpy as np
from numba import njit

EXIT_PREV_HIGH = 0  # MR neutral exit: signal close > previous signal high, capped by max_hold


@njit(cache=True)
def run_cell(entry: np.ndarray, sig_close: np.ndarray, sig_high: np.ndarray,
             ex_open: np.ndarray, ex_close: np.ndarray, div: np.ndarray,
             direction: int, max_hold: int, notional: float, cost_bps: float):
    n = entry.shape[0]
    cap = n // 2 + 1
    rec = np.full((cap, 11), np.nan)  # sig, ent, ex, ent_px, ex_px, shares, gross, cost, divs, net, forced
    k = 0
    t = 0
    while t < n - 1:
        if not entry[t] or np.isnan(ex_open[t + 1]):
            t += 1
            continue
        ent = t + 1
        ent_px = ex_open[ent]
        shares = notional / ent_px
        j = ent
        forced = 0.0
        while True:
            held = j - ent + 1
            exit_sig = (sig_close[j] > sig_high[j - 1]) if direction == 1 else (sig_close[j] < sig_high[j - 1])
            if exit_sig or held >= max_hold:
                if j + 1 < n and not np.isnan(ex_open[j + 1]):
                    ex_i = j + 1
                    ex_px = ex_open[ex_i]
                else:
                    ex_i = j
                    ex_px = ex_close[j]
                    forced = 1.0
                break
            if j + 1 >= n or np.isnan(ex_open[j + 1]):
                ex_i = j
                ex_px = ex_close[j]
                forced = 1.0
                break
            j += 1
        divs = 0.0
        for d in range(ent + 1, ex_i + 1):
            if div[d] > 0:
                divs += direction * shares * div[d]
        gross = direction * shares * (ex_px - ent_px)
        cost = cost_bps * 1e-4 * shares * (ent_px + ex_px)
        rec[k, 0] = t
        rec[k, 1] = ent
        rec[k, 2] = ex_i
        rec[k, 3] = ent_px
        rec[k, 4] = ex_px
        rec[k, 5] = shares
        rec[k, 6] = gross
        rec[k, 7] = cost
        rec[k, 8] = divs
        rec[k, 9] = gross - cost + divs
        rec[k, 10] = forced
        k += 1
        t = ex_i  # can signal again on the close of the exit bar
    return rec[:k]
