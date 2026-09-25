"""Paper / live daily cycle with research parity (design 15.1, roadmap package 6).

Parity planner: the day's decisions come from the research engine itself. The arrays known at today's close are
extended by one placeholder bar (next business day, open = close = today's close). On that extended series:
- an entry signal at today's close is a trade whose `signal_date` is today (it would fill at the next open);
- an open position must exit when its research trade (same `signal_date`) exits at the placeholder open and is
  not a forced end-of-data exit - i.e. the exit rule fired at today's close.
So entries, exits (time, prev-high, RSI, ATR target / stop / trailing) and filters are decided by exactly the
code that produced the backtest; the placeholder bar carries no information (only its open slot is used).

Daily loop (`PaperTrader.fill_pending`, then `PaperTrader.plan`; persistent job: forward/daily.py): plan at the
close of day t -> row orders -> netting -> broker fills at the open of t+1 -> row books -> reconciliation.
Quantities: notional per trade = capital / max_positions (capacity rows) or the cache notional (cell rows), times
the vol weight for `sizing="vol"` rows, rounded down to `lot_step`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import polars as pl

from sfactory.broker.orders import Order, RowBook, allocate, net_orders
from sfactory.broker.reconcile import reconcile
from sfactory.engine.cache import SymbolArrays, TradeCache
from sfactory.portfolio.sizing import trailing_vol
from sfactory.signals.methods import EntrySpec


def _next_day(d) -> np.datetime64:
    if np.asarray(d).dtype != np.dtype("datetime64[D]"):
        raise NotImplementedError("the parity planner is daily-only for now (intraday: next bar of the session)")
    return np.busday_offset(d, 1, roll="forward")


def arrays_upto(arrays: dict[str, SymbolArrays], day: date) -> dict[str, SymbolArrays]:
    """Every symbol's arrays through `day` (inclusive): what is known at that close."""
    d = np.datetime64(day)
    out = {}
    for s, a in arrays.items():
        i = int(np.searchsorted(a.dates, d, side="right"))
        if i > 0:
            out[s] = SymbolArrays(s, a.dates[:i], a.sig_close[:i], a.sig_high[:i], a.ex_open[:i], a.ex_close[:i],
                                  a.div[:i], a.sig_low[:i], a.factor[:i])
    return out


def extend_one_bar(a: SymbolArrays) -> SymbolArrays:
    last = a.dates[-1]
    c, f = a.ex_close[-1], a.factor[-1]
    return SymbolArrays(a.symbol, np.r_[a.dates, _next_day(last)], np.r_[a.sig_close, a.sig_close[-1]],
                        np.r_[a.sig_high, a.sig_close[-1]], np.r_[a.ex_open, c], np.r_[a.ex_close, c],
                        np.r_[a.div, 0.0], np.r_[a.sig_low, a.sig_close[-1]], np.r_[a.factor, f])


def _exit_spec(cfg, exit_id: str):
    for e in (cfg.neutral, *cfg.exit_lib):
        if e.id == exit_id:
            return e
    raise ValueError(f"exit {exit_id} not in the row's library")


@dataclass
class DayPlan:
    day: date
    orders: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def plan_day(known: TradeCache, book: dict, day: date, books: dict[str, RowBook], exit_only: set = frozenset(),
             lot_step: float = 1.0, opened_with: dict | None = None, weights: dict | None = None) -> DayPlan:
    """Row orders for the next open from everything known at `day`'s close.

    known: TradeCache over arrays through `day` (with regime / index events as in research).
    book: row -> forward.live.BookEntry (current decisions); exit_only: rows that may only close positions.
    opened_with: (row, symbol) -> the BookEntry a position was opened with. A position always exits by the rules
    of its own setting (as in research, where a trade opened in fold k runs its course after the next DP), even
    when the row's parameters changed or the row left the book. Default: the row's current entry.
    weights: row -> {symbol: weight} from the fast clock (policy.edge_state): 0 = no new entries for the symbol,
    other values scale the quantity; rows or symbols without a weight trade at 1. Exits are never affected.
    """
    opened_with = opened_with or {}
    weights = weights or {}
    ext = TradeCache({s: extend_one_bar(a) for s, a in known.arrays.items() if a.dates[-1] == np.datetime64(day)},
                     f"{known.data_version}|live-{day}", known.notional, cost_model=known.cost_model)
    if known._regime_dates is not None:
        ext.set_market_regime(known._regime_dates, known._regime_flags, known._regime_id)
    ext._index_add = {s: np.r_[v[:len(known.arrays[s].dates)], False] for s, v in known._index_add.items()
                      if s in ext.arrays}
    ext._events_id = known._events_id
    plan = DayPlan(day)
    exiting: dict[str, int] = {}
    for rid in sorted(set(books) | set(book)):
        rb = books.setdefault(rid, RowBook(rid))
        for sym, pos in sorted(rb.positions.items()):
            be = opened_with.get((rid, sym)) or book.get(rid)
            if be is None:
                plan.warnings.append(f"{rid}/{sym}: no setting known for the open position, kept for review")
                continue
            if sym not in ext.arrays:
                plan.warnings.append(f"{rid}/{sym}: no bar today, position kept")
                continue
            cfg = be.config
            t = ext.trades(sym, EntrySpec(cfg.method, be.threshold, cfg.direction), _exit_spec(cfg, be.exit_id),
                           be.filters)
            m = t.filter(pl.col("signal_date") == pos.signal_date)
            if len(m) == 0:
                plan.warnings.append(f"{rid}/{sym}: open position has no research trade, kept for review")
                continue
            r = m.row(0, named=True)
            if np.datetime64(r["exit_date"]) == ext.arrays[sym].dates[-1] and not r["forced_exit"]:
                exiting[rid] = exiting.get(rid, 0) + 1
                plan.orders.append(Order(f"{day}:{rid}:{sym}:x", rid, sym, -pos.qty, "close", pos.signal_date,
                                         float(known.arrays[sym].ex_close[-1])))
    for rid, be in book.items():
        if rid in exit_only:
            continue
        cfg = be.config
        spec = EntrySpec(cfg.method, be.threshold, cfg.direction)
        ex = _exit_spec(cfg, be.exit_id)
        held = books[rid].positions
        cands = []
        rw = weights.get(rid, {})
        for sym in be.eligible:
            if sym in held or sym not in ext.arrays or rw.get(sym, 1.0) <= 0:
                continue
            t = ext.trades(sym, spec, ex, be.filters)
            hit = t.filter(pl.col("signal_date") == day)
            if len(hit):
                cands.append((float(hit["score"][0]), sym))
        cands.sort()
        if cfg.max_positions > 0:
            free = max(0, cfg.max_positions - (len(held) - exiting.get(rid, 0)))
            cands = cands[: min(free, cfg.max_new_per_day)]
            notional = cfg.capital / cfg.max_positions
        else:
            notional = known.notional
        for _, sym in cands:
            a = known.arrays[sym]
            w = float(rw.get(sym, 1.0))
            if getattr(cfg, "sizing", "fixed") == "vol":
                v = float(np.fmax(trailing_vol(a.sig_close, cfg.vol_window)[-1],
                                  trailing_vol(a.sig_close, cfg.vol_fast)[-1] if cfg.vol_fast > 1 else np.nan))
                w *= float(np.clip(cfg.target_vol / v, 0.0, cfg.w_max)) if np.isfinite(v) and v > 0 else 1.0
            px = float(a.ex_close[-1])
            qty = np.floor(notional * w / px / lot_step) * lot_step
            if qty <= 0:
                plan.warnings.append(f"{rid}/{sym}: quantity rounds to zero at lot step {lot_step}")
                continue
            plan.orders.append(Order(f"{day}:{rid}:{sym}:o", rid, sym, cfg.direction * qty, "open", day, px))
    return plan


@dataclass
class PaperTrader:
    """Plan at the close, fill at the next open, book per row, reconcile against the broker."""
    broker: object
    books: dict = field(default_factory=dict)
    pending: list = field(default_factory=list)
    log: list = field(default_factory=list)

    def fill_pending(self, arrays: dict[str, SymbolArrays], day: date) -> dict:
        """Execute yesterday's plan at `day`'s open."""
        if not self.pending:
            return reconcile(self.books.values(), self.broker.positions())
        orders, self.pending = self.pending, []
        opens = {}
        for o in orders:                     # bar open when the bar exists (paper); else the reference price
            a = arrays.get(o.symbol)         # (live at the open: the broker fills at market and ignores it)
            i = -1 if a is None else int(np.searchsorted(a.dates, np.datetime64(day)))
            ok = a is not None and i < len(a.dates) and a.dates[i] == np.datetime64(day)
            opens[o.symbol] = float(a.ex_open[i]) if ok else o.ref_price
        nets = net_orders(orders)
        fills = self.broker.execute(nets, opens, day)
        for rf in allocate(nets, fills, orders, opens, day):
            self.books.setdefault(rf.order.row, RowBook(rf.order.row)).apply(rf)
        rec = reconcile(self.books.values(), self.broker.positions())
        self.log.append({"day": str(day), "orders": len(orders), "net_orders": sum(abs(n.qty) > 0 for n in nets),
                         "reconciled": rec["ok"]})
        return rec

    def plan(self, known: TradeCache, book: dict, day: date, exit_only: set = frozenset(),
             lot_step: float = 1.0, opened_with: dict | None = None, weights: dict | None = None) -> DayPlan:
        p = plan_day(known, book, day, self.books, exit_only, lot_step, opened_with, weights)
        self.pending = list(p.orders)
        return p

    def closed_trades(self) -> pl.DataFrame:
        rows = [t for b in self.books.values() for t in b.closed]
        return pl.DataFrame(rows) if rows else pl.DataFrame()


def run_paper(arrays: dict[str, SymbolArrays], book: dict, days: list[date], broker, data_version: str = "paper",
              cost_model=None, notional: float = 100_000.0, regime=None, lot_step: float = 1e-9) -> PaperTrader:
    """Replay `days` as if live: for each day fill yesterday's orders at the open, then plan at the close.
    Used for paper mode on historical data and for the research-parity test."""
    pt = PaperTrader(broker)
    for day in days:
        pt.fill_pending(arrays, day)
        known = TradeCache(arrays_upto(arrays, day), f"{data_version}|{day}", notional, cost_model=cost_model)
        if regime is not None:
            known.set_market_regime(*regime)
        pt.plan(known, book, day, lot_step=lot_step)
    return pt
