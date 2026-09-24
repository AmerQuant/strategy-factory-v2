"""Live decision points (design 15.1): the same selector as research, run at a live DP on data before the DP.

- decide_book: runs `decide_fold` for every row of the frozen policy -> the new book
- book_diff: what changes at the DP (rows in / out / kept, parameter changes); open trades of removed rows
  are never force-closed - they exit by their own rules; removed rows only stop taking new entries
- daily_orders: tomorrow-open entries from today's close, honouring the book, one position per symbol,
  capacity and the daily ranker
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from sfactory.data.universe import eligible_at
from sfactory.policy.ladder import LadderConfig, _FoldView, decide_fold
from sfactory.signals.methods import EntrySpec, entry_and_score
from sfactory.signals.specs import atr_exec_units, filter_mask
from sfactory.timeline.folds import Fold, FoldManager, add_months


@dataclass(frozen=True)
class BookEntry:
    row: str
    config: LadderConfig
    threshold: float
    exit_id: str
    filters: tuple
    eligible: tuple
    decision: dict


def live_fold(fm: FoldManager, dp: date, index: int = -1) -> Fold:
    return Fold(index, dp, fm._is_start(dp), dp, add_months(dp, fm.cfg.dp_months), holdout=False)


def decide_book(fm: FoldManager, cache, bars_hist: pl.DataFrame, membership: pl.DataFrame,
                configs: list[LadderConfig], dp: date) -> dict[str, BookEntry]:
    fold = live_fold(fm, dp)
    book = {}
    for cfg in configs:
        elig = eligible_at(dp, bars_hist, membership, cfg.min_price, cfg.min_dollar_vol, min_history=cfg.min_history)
        view = _FoldView(fm, cache, cfg, fold, elig)
        thr, ex, chosen, d = decide_fold(view, cfg)
        book[cfg.rid] = BookEntry(cfg.rid, cfg, thr, ex.id, cfg.structural + chosen, tuple(elig), d)
    return book


def book_diff(old: dict[str, BookEntry], new: dict[str, BookEntry]) -> dict:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = {}
    for r in sorted(set(old) & set(new)):
        o, n = old[r], new[r]
        delta = {k: (getattr(o, k), getattr(n, k)) for k in ("threshold", "exit_id")
                 if getattr(o, k) != getattr(n, k)}
        of, nf = [f.id for f in o.filters], [f.id for f in n.filters]
        if of != nf:
            delta["filters"] = (of, nf)
        sym_in, sym_out = set(n.eligible) - set(o.eligible), set(o.eligible) - set(n.eligible)
        if sym_in or sym_out:
            delta["symbols"] = {"in": sorted(sym_in), "out": sorted(sym_out)}
        if delta:
            changed[r] = delta
    return {"added": added, "removed": removed, "changed": changed,
            "kept": sorted(set(old) & set(new)),
            "note": "removed rows stop new entries; their open trades exit by their own rules"}


def daily_orders(cache, book: dict[str, BookEntry], day: date, open_positions: dict[str, set],
                 regime_flags=None) -> list[dict]:
    """Entry orders for the next open, from signals on the close of `day`.

    open_positions: row -> set of symbols currently held by that row.
    """
    orders = []
    for rid, be in book.items():
        cfg = be.config
        held = open_positions.get(rid, set())
        spec = EntrySpec(cfg.method, be.threshold, cfg.direction)
        cands = []
        for sym in be.eligible:
            a = cache.arrays.get(sym)
            if a is None or sym in held:
                continue
            i = int(np.searchsorted(a.dates, np.datetime64(day)))
            if i >= len(a.dates) or a.dates[i] != np.datetime64(day):
                continue
            entry, score = entry_and_score(spec, a.sig_high[: i + 1], a.sig_low[: i + 1], a.sig_close[: i + 1])
            ok = bool(entry[i])
            if ok and be.filters:
                atr = atr_exec_units(a.sig_high[: i + 1], a.sig_low[: i + 1], a.sig_close[: i + 1], a.factor[: i + 1])
                flags = cache._regime_for(a.dates[: i + 1]) if regime_flags is None else regime_flags
                for f in be.filters:
                    ok = ok and bool(filter_mask(f, a.sig_close[: i + 1], atr, a.ex_close[: i + 1], flags,
                                                 cfg.direction)[i])
            if ok:
                cands.append({"row": rid, "symbol": sym, "side": "buy" if cfg.direction == 1 else "sell",
                              "score": float(score[i]), "signal_date": day})
        cands.sort(key=lambda c: (c["score"], c["symbol"]))
        if cfg.max_positions > 0:
            free = max(0, cfg.max_positions - len(held))
            cands = cands[: min(free, cfg.max_new_per_day)]
        orders.extend(cands)
    return orders
