"""Persistent daily job for paper and live trading (docs/spec/daily.md).

Per trading day:
1. open phase - fill the orders planned at the previous close (paper: the simulated broker at the bar open;
   live MT5: the adapter at market, run at the open);
2. close phase - at a new decision point (every `dp_months`, first day of the month) rebuild the book with the
   research selector (`decide_book`, data strictly before the DP), then plan the next open with the
   research-parity planner (`forward.paper.plan_day`). Every open position exits by the setting it was opened
   with (`opened_with`), even when its row changed at the DP or left the book;
3. persist everything in a JSON state file, so the job is restartable and auditable (every day appends a log line).

The state holds the frozen policy (row configs as dicts, as `evaluation.holdout.freeze_policy` writes them), the
current book, the rows' virtual positions and closed trades, the pending orders and the log. Serialisation is
lossless for the fields the planner uses (tested: a run that saves and reloads the state every day gives the same
trades as one that keeps it in memory).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

import polars as pl

from sfactory.broker.orders import Order, RowBook, VirtualPosition
from sfactory.engine.cache import SymbolArrays, TradeCache
from sfactory.forward.live import BookEntry, book_diff, decide_book
from sfactory.forward.paper import PaperTrader, arrays_upto
from sfactory.policy.ladder import LadderConfig
from sfactory.signals.specs import ExitSpec, FilterSpec
from sfactory.timeline.folds import FoldConfig, FoldManager, add_months

STATE_VERSION = 1


# --- (de)serialisation ------------------------------------------------------------------------------------
def config_from_dict(d: dict):
    """Inverse of `dataclasses.asdict` for LadderConfig / EnsembleConfig (nested specs rebuilt)."""
    if d.get("method") == "ensemble" and "members" in d:
        from sfactory.policy.ensemble import EnsembleConfig
        return EnsembleConfig(**{**d, "members": tuple(config_from_dict(m) for m in d["members"])})
    x = dict(d)
    for k in ("filters", "structural"):
        x[k] = tuple(FilterSpec(**f) for f in x.get(k) or ())
    if x.get("exits") is not None:
        x["exits"] = tuple(ExitSpec(**e) for e in x["exits"])
    if x.get("thresholds") is not None:
        x["thresholds"] = tuple(x["thresholds"])
    return LadderConfig(**x)


def _d(s):
    return None if s is None else date.fromisoformat(str(s)[:10])


def _book_to_json(book: dict) -> dict:
    return {r: {"config": asdict(b.config), "threshold": b.threshold, "exit_id": b.exit_id,
                "filters": [asdict(f) for f in b.filters], "eligible": list(b.eligible), "decision": b.decision}
            for r, b in book.items()}


def _book_from_json(d: dict) -> dict:
    return {r: BookEntry(r, config_from_dict(v["config"]), v["threshold"], v["exit_id"],
                         tuple(FilterSpec(**f) for f in v["filters"]), tuple(v["eligible"]), v["decision"])
            for r, v in d.items()}


@dataclass
class DailyState:
    policy: list                                       # row configs as dicts
    fold: dict                                         # first_dp, dp_months, is_years, data_start
    last_dp: str | None = None
    book: dict = field(default_factory=dict)           # row -> BookEntry (current decisions)
    opened_with: dict = field(default_factory=dict)    # (row, symbol) -> BookEntry the position was opened with
    books: dict = field(default_factory=dict)          # row -> RowBook
    pending: list = field(default_factory=list)        # Order
    log: list = field(default_factory=list)
    last_day: str | None = None
    sim_net: dict = field(default_factory=dict)        # simulated broker net positions (paper mode)
    filled_day: str | None = None                      # last day whose open was executed

    def to_json(self) -> dict:
        ow = _book_to_json({f"{r}|{s}": b for (r, s), b in self.opened_with.items()})
        return {"version": STATE_VERSION, "policy": self.policy, "fold": self.fold, "last_dp": self.last_dp,
                "book": _book_to_json(self.book), "opened_with": ow,
                "books": {r: {"positions": [{**asdict(p), "signal_date": str(p.signal_date),
                                             "entry_date": str(p.entry_date)} for p in b.positions.values()],
                              "closed": [{k: (str(v) if isinstance(v, date) else v) for k, v in t.items()}
                                         for t in b.closed]} for r, b in self.books.items()},
                "pending": [{**asdict(o), "signal_date": str(o.signal_date)} for o in self.pending],
                "log": self.log, "last_day": self.last_day, "sim_net": self.sim_net,
                "filled_day": self.filled_day}

    @classmethod
    def from_json(cls, d: dict) -> DailyState:
        if d.get("version") != STATE_VERSION:
            raise ValueError(f"state version {d.get('version')} != {STATE_VERSION}")
        books = {}
        for r, b in d["books"].items():
            rb = RowBook(r)
            for p in b["positions"]:
                rb.positions[p["symbol"]] = VirtualPosition(**{**p, "signal_date": _d(p["signal_date"]),
                                                               "entry_date": _d(p["entry_date"])})
            rb.closed = [{**t, **{k: _d(t[k]) for k in ("signal_date", "entry_date", "exit_date")}}
                         for t in b["closed"]]
            books[r] = rb
        ow = {tuple(k.split("|", 1)): v for k, v in _book_from_json(d["opened_with"]).items()}
        ow = {k: BookEntry(k[0], v.config, v.threshold, v.exit_id, v.filters, v.eligible, v.decision)
              for k, v in ow.items()}
        return cls(d["policy"], d["fold"], d["last_dp"], _book_from_json(d["book"]), ow, books,
                   [Order(**{**o, "signal_date": _d(o["signal_date"])}) for o in d["pending"]],
                   d["log"], d["last_day"], d.get("sim_net", {}), d.get("filled_day"))

    def save(self, path: str | Path) -> None:
        p = Path(path)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=1, default=str), encoding="utf-8")
        tmp.replace(p)                                 # atomic on the same volume

    @classmethod
    def load(cls, path: str | Path) -> DailyState:
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")))

    def closed_trades(self) -> pl.DataFrame:
        rows = [t for b in self.books.values() for t in b.closed]
        return pl.DataFrame(rows) if rows else pl.DataFrame()


def new_state(policy_rows: list, first_dp: date, dp_months: int = 6, is_years: int = 3,
              data_start: date = date(1990, 1, 1)) -> DailyState:
    return DailyState([asdict(r) if not isinstance(r, dict) else r for r in policy_rows],
                      {"data_start": str(data_start), "first_dp": str(first_dp), "dp_months": dp_months,
                       "is_years": is_years})


def _fm(state: DailyState) -> FoldManager:
    f = state.fold
    first = date.fromisoformat(f["first_dp"])
    return FoldManager(FoldConfig(date.fromisoformat(f["data_start"]), first, date(2199, 1, 1),
                                  date(2199, 1, 1), dp_months=int(f["dp_months"]), is_years=int(f["is_years"])))


def due_dp(state: DailyState, today: date) -> date | None:
    """The decision point to apply at `today`'s close, if one has come (first DP, then every dp_months)."""
    step = int(state.fold["dp_months"])
    nxt = (date.fromisoformat(state.fold["first_dp"]) if state.last_dp is None
           else add_months(date.fromisoformat(state.last_dp), step))
    if today < nxt:
        return None
    while add_months(nxt, step) <= today:            # catch up after a long pause
        nxt = add_months(nxt, step)
    return nxt


# --- the day ----------------------------------------------------------------------------------------------
def open_phase(state: DailyState, arrays: dict[str, SymbolArrays], today: date, broker) -> dict:
    """Execute the orders planned at the previous close. Paper: at today's bar open (arrays through today);
    live MT5: run at the open, the adapter fills at market (today's bar need not exist yet)."""
    if state.filled_day is not None and date.fromisoformat(state.filled_day) >= today:
        raise ValueError(f"open of {today} already executed")
    pt = PaperTrader(broker, books=state.books, pending=list(state.pending))
    rec = pt.fill_pending(arrays, today)
    for (r, s) in list(state.opened_with):          # positions closed today no longer need their setting
        if s not in pt.books.get(r, RowBook(r)).positions:
            del state.opened_with[(r, s)]
    state.books, state.pending, state.filled_day = pt.books, [], str(today)
    if hasattr(broker, "net"):
        state.sim_net = dict(broker.net)
    return {"day": str(today), "reconciled": rec["ok"], "diffs": rec["diffs"]}


def close_phase(state: DailyState, arrays: dict[str, SymbolArrays], bars_hist: pl.DataFrame,
                membership: pl.DataFrame | None, today: date, cost_model=None, regime=None,
                data_version: str = "live", lot_step: float = 1e-9) -> dict:
    """After today's close: refresh the book at a DP, then plan the next open."""
    if state.last_day is not None and date.fromisoformat(state.last_day) >= today:
        raise ValueError(f"close of {today} already processed (last {state.last_day})")
    if state.pending:
        raise ValueError("pending orders were not executed: run the open phase first")
    known = TradeCache(arrays_upto(arrays, today), f"{data_version}|{today}", cost_model=cost_model)
    if regime is not None:
        known.set_market_regime(*regime)
    if membership is not None:
        known.set_index_events(membership)
    report: dict = {"day": str(today)}
    dp = due_dp(state, today)
    if dp is not None:
        configs = [config_from_dict(r) for r in state.policy]
        new = decide_book(_fm(state), known, bars_hist.filter(pl.col("date") <= today), membership, configs, dp)
        diff = book_diff(state.book, new)
        state.book, state.last_dp = new, str(dp)
        report["dp"] = {"dp": str(dp), **{k: diff[k] for k in ("added", "removed", "changed")}}
    pt = PaperTrader(None, books=state.books)
    plan = pt.plan(known, state.book, today, lot_step=lot_step, opened_with=state.opened_with)
    for o in plan.orders:
        if o.intent == "open":
            state.opened_with[(o.row, o.symbol)] = state.book[o.row]
    state.books, state.pending, state.last_day = pt.books, list(pt.pending), str(today)
    report.update({"orders": len(plan.orders), "warnings": plan.warnings,
                   "open_positions": sum(len(b.positions) for b in state.books.values())})
    return report


def daily_step(state: DailyState, arrays: dict[str, SymbolArrays], bars_hist: pl.DataFrame,
               membership: pl.DataFrame | None, today: date, broker, cost_model=None, regime=None,
               data_version: str = "live", lot_step: float = 1e-9) -> dict:
    """Paper mode in one call: fill yesterday's plan at today's open, then plan at today's close."""
    rec = open_phase(state, arrays, today, broker)
    rep = close_phase(state, arrays, bars_hist, membership, today, cost_model, regime, data_version, lot_step)
    rep.update({"reconciled": rec["reconciled"], "diffs": rec["diffs"]})
    state.log.append({k: rep[k] for k in ("day", "reconciled", "orders", "open_positions")})
    return rep
