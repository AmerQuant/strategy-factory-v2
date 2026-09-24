"""Capacity-constrained portfolio simulation over a stitched stream of candidate trades (design 7.6, S8).

Rules: positions are processed in entry-date order; a slot frees on the exit date (exit and entry both fill
at the open); at most `max_positions` open, at most `max_new_per_day` new entries per day, one position per
symbol. Candidates of the same day are ranked by `score` (ascending: lower = more oversold) or randomly.
Each taken trade is sized at capital / max_positions (pnl is rescaled from the cache's fixed notional).
"""
from __future__ import annotations

import heapq

import numpy as np
import polars as pl


def simulate_capacity(cands: pl.DataFrame, max_positions: int, max_new_per_day: int, capital: float,
                      cache_notional: float, ranker: str = "score_asc", seed: int = 0) -> pl.DataFrame:
    if len(cands) == 0:
        return cands
    rng = np.random.default_rng(seed)
    df = cands.sort(["entry_date", "symbol"])
    if ranker == "random":
        df = df.with_columns(pl.Series("_r", rng.random(len(df))))
    elif ranker == "score_asc":
        df = df.with_columns(pl.col("score").alias("_r"))
    else:
        raise ValueError(f"unknown ranker {ranker}")
    open_heap: list[tuple] = []  # (exit_date, symbol)
    held: set[str] = set()
    keep = np.zeros(len(df), dtype=bool)
    ent = df["entry_date"].to_list()
    ex = df["exit_date"].to_list()
    sym = df["symbol"].to_list()
    rank = df["_r"].to_numpy()
    i, n = 0, len(df)
    while i < n:
        day = ent[i]
        j = i
        while j < n and ent[j] == day:
            j += 1
        while open_heap and open_heap[0][0] <= day:
            _, s = heapq.heappop(open_heap)
            held.discard(s)
        order = sorted(range(i, j), key=lambda k: (rank[k], sym[k]))
        taken = 0
        for k in order:
            if len(held) >= max_positions or taken >= max_new_per_day:
                break
            if sym[k] in held:
                continue
            keep[k] = True
            held.add(sym[k])
            heapq.heappush(open_heap, (ex[k], sym[k]))
            taken += 1
        i = j
    scale = (capital / max_positions) / cache_notional
    out = df.filter(pl.Series(keep)).drop("_r")
    cols = [c for c in ("gross_pnl", "cost", "dividends", "financing", "net_pnl", "shares") if c in out.columns]
    return out.with_columns([(pl.col(c) * scale) for c in cols])


def max_concurrent(trades: pl.DataFrame) -> int:
    ev = [(d, 1) for d in trades["entry_date"].to_list()] + [(d, -1) for d in trades["exit_date"].to_list()]
    cur = best = 0
    for _, s in sorted(ev, key=lambda x: (x[0], x[1])):  # exits (-1) processed before entries on same day
        cur += s
        best = max(best, cur)
    return best
