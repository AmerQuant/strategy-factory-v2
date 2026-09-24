"""Generic cell kernel: arbitrary causal exit signal + optional ATR target/stop (close-based) + max hold.

Same execution contract as engine.cell: signal on close t -> fill at open t+1; any exit condition on close j ->
fill at open j+1; missing next bar -> forced exit at last close. ATR is taken at the signal bar and expressed in
execution-price units. Target/stop are checked on the close (intrabar fills are a later extension).
"""
from __future__ import annotations

import numpy as np
from numba import njit


@njit(cache=True)
def run_cell_generic(entry, exit_sig, ex_open, ex_close, div, atr, direction, max_hold,
                     target_atr, stop_atr, notional, cost_bps):
    n = entry.shape[0]
    rec = np.full((n // 2 + 1, 11), np.nan)
    k = 0
    t = 0
    while t < n - 1:
        if not entry[t] or np.isnan(ex_open[t + 1]):
            t += 1
            continue
        ent = t + 1
        ent_px = ex_open[ent]
        shares = notional / ent_px
        a = atr[t]
        j = ent
        forced = 0.0
        while True:
            held = j - ent + 1
            move = direction * (ex_close[j] - ent_px)
            hit_t = target_atr > 0 and not np.isnan(a) and move >= target_atr * a
            hit_s = stop_atr > 0 and not np.isnan(a) and move <= -stop_atr * a
            if exit_sig[j] or held >= max_hold or hit_t or hit_s:
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
        t = ex_i
    return rec[:k]
