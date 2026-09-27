"""Persistent daily job for paper and live trading (docs/spec/daily.md).

Per trading day:
1. open phase - fill the orders planned at the previous close (paper: the simulated broker at the bar open;
   live MT5: the adapter at market, run at the open);
2. close phase - at a new decision point (every `dp_months`, first day of the month) rebuild the book with the
   research selector (`decide_book`, data strictly before the DP), then plan the next open with the
   research-parity planner (`forward.paper.plan_day`). Every open position exits by the setting it was opened
   with (`opened_with`), even when its row changed at the DP or left the book. Rows that carry an accepted
   fast-clock mechanism (policy entry {"config", "activation"}) get per-symbol weights at every sub-DP from the
   same `decide_sub` as research; a weight only gates / scales new entries. Rows with a daily sizing overlay
   (policy entry "overlay": vol target and / or drawdown brake) scale their new entries by the factor the research
   overlay would apply tomorrow, computed with the same functions on the row's realised daily pnl;
3. persist everything in a JSON state file, so the job is restartable and auditable (every day appends a log line).

The state holds the frozen policy (row configs as dicts, as `evaluation.holdout.freeze_policy` writes them), the
current book, the rows' virtual positions and closed trades, the pending orders and the log. Serialisation is
lossless for the fields the planner uses (tested: a run that saves and reloads the state every day gives the same
trades as one that keeps it in memory).
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl

from sfactory.broker.orders import Order, RowBook, VirtualPosition
from sfactory.engine.cache import SymbolArrays, TradeCache
from sfactory.forward.live import BookEntry, book_diff, decide_book, live_fold
from sfactory.forward.paper import PaperTrader, _exit_spec, _next_bar, arrays_upto
from sfactory.policy.edge_state import ActivationConfig, calibrate_trendiness, decide_sub
from sfactory.policy.ladder import LadderConfig
from sfactory.portfolio.sizing import drawdown_brake, vol_target_daily
from sfactory.signals.methods import EntrySpec
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


OVERLAY_DEFAULTS = {"target_vol_daily": None, "lookback": 63, "lev_max": 2.0, "min_obs": 20,
                    "dd_limit": None, "cut": 0.5, "resume": 0.5}


def overlay_of(entry) -> dict | None:
    """The daily sizing overlay of a policy entry ({"config", "overlay": {...}}), defaults filled; None if absent."""
    if not isinstance(entry, dict) or not entry.get("overlay"):
        return None
    ov = {**OVERLAY_DEFAULTS, **entry["overlay"]}
    unknown = set(ov) - set(OVERLAY_DEFAULTS)
    if unknown:
        raise ValueError(f"unknown overlay fields {sorted(unknown)}")
    return ov


def row_daily_pnl(closed: list, start: date, end: date) -> np.ndarray:
    """Realised net pnl per business day (exit date) in [start, end], zeros on days without exits - the series the
    research overlays run on."""
    days = np.arange(np.datetime64(start, "D"), np.datetime64(end, "D") + np.timedelta64(1, "D"))
    days = days[np.is_busday(days)]
    out = np.zeros(len(days))
    for t in closed:
        d = np.datetime64(str(t["exit_date"])[:10], "D")
        i = int(np.searchsorted(days, d))
        if i < len(days) and days[i] == d:
            out[i] += float(t["net_pnl"])
    return out


def overlay_scale(daily: np.ndarray, ov: dict, capital: float) -> dict:
    """Factor for the next day's new entries: research vol_target_daily and drawdown_brake evaluated one step past
    the realised series (both only use days strictly before the one they size)."""
    x = np.r_[daily, 0.0]
    lev, exp = 1.0, 1.0
    if ov["target_vol_daily"]:
        _, lv = vol_target_daily(x, ov["target_vol_daily"], ov["lookback"], ov["lev_max"], ov["min_obs"])
        lev = float(lv[-1])
    if ov["dd_limit"]:
        _, ex = drawdown_brake(x, capital, ov["dd_limit"], ov["cut"], ov["resume"])
        exp = float(ex[-1])
    return {"leverage": lev, "brake": exp, "scale": lev * exp}


def policy_entries(policy: list) -> list[tuple]:
    """(row config, ActivationConfig or None) per policy entry. An entry is a config dict (as `asdict` writes it)
    or {"config": {...}, "activation": {...}} for a row that trades with an accepted fast-clock mechanism."""
    out = []
    for e in policy:
        if isinstance(e, dict) and "config" in e:
            act = e.get("activation")
            out.append((config_from_dict(e["config"]), ActivationConfig(**act) if act else None))
        else:
            out.append((config_from_dict(e), None))
    return out


def _d(s):
    """Parse a stored day or bar timestamp: dates stay dates, intraday timestamps stay datetimes."""
    if s is None:
        return None
    s = str(s)
    return datetime.fromisoformat(s) if len(s) > 10 else date.fromisoformat(s)


def _day(t) -> date:
    """The calendar day of a day or a bar timestamp (decision points are calendar days)."""
    return t.date() if isinstance(t, datetime) else t


def _at(t) -> datetime:
    """Comparable instant of a day (its start) or a bar timestamp."""
    return t if isinstance(t, datetime) else datetime(t.year, t.month, t.day)  # noqa: DTZ001 - naive by convention


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
    weights: dict = field(default_factory=dict)        # fast clock: row -> {symbol: weight}
    act_state: dict = field(default_factory=dict)      # fast clock: row -> previous weights (hysteresis)
    trend_cal: dict = field(default_factory=dict)      # fast clock: row -> in-fold trendiness calibration
    last_sub_dp: str | None = None
    div_day: str | None = None                         # last day whose dividends were accrued in the books
    row_scale: dict = field(default_factory=dict)      # daily sizing overlays: row -> factor for new entries

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
                "filled_day": self.filled_day, "weights": self.weights, "act_state": self.act_state,
                "trend_cal": self.trend_cal, "last_sub_dp": self.last_sub_dp, "div_day": self.div_day,
                "row_scale": self.row_scale}

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
                   d["log"], d["last_day"], d.get("sim_net", {}), d.get("filled_day"), d.get("weights", {}),
                   d.get("act_state", {}), d.get("trend_cal", {}), d.get("last_sub_dp"), d.get("div_day"),
                   d.get("row_scale", {}))

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


def _entry(r):
    if isinstance(r, dict):
        return r
    if isinstance(r, tuple):
        cfg, act, *rest = r
        e = {"config": asdict(cfg), "activation": asdict(act) if act is not None else None}
        if rest and rest[0]:
            e["overlay"] = dict(rest[0])
        return e
    return asdict(r)


def new_state(policy_rows: list, first_dp: date, dp_months: int = 6, is_years: int = 3,
              data_start: date = date(1990, 1, 1)) -> DailyState:
    """policy_rows: configs, config dicts, or (config, ActivationConfig | None[, overlay dict]) tuples."""
    return DailyState([_entry(r) for r in policy_rows],
                      {"data_start": str(data_start), "first_dp": str(first_dp), "dp_months": dp_months,
                       "is_years": is_years})


def _fm(state: DailyState) -> FoldManager:
    f = state.fold
    first = date.fromisoformat(f["first_dp"])
    return FoldManager(FoldConfig(date.fromisoformat(f["data_start"]), first, date(2199, 1, 1),
                                  date(2199, 1, 1), dp_months=int(f["dp_months"]), is_years=int(f["is_years"])))


def due_dp(state: DailyState, today: date) -> date | None:
    """The decision point to apply at `today`'s close, if one has come (first DP, then every dp_months)."""
    today = _day(today)
    step = int(state.fold["dp_months"])
    nxt = (date.fromisoformat(state.fold["first_dp"]) if state.last_dp is None
           else add_months(date.fromisoformat(state.last_dp), step))
    if today < nxt:
        return None
    while add_months(nxt, step) <= today:            # catch up after a long pause
        nxt = add_months(nxt, step)
    return nxt


def due_sub(state: DailyState, today: date, sub_months: int = 1) -> date | None:
    """The fast-clock sub-DP to apply at `today`'s close: the latest of last_dp + k * sub_months that has come
    and was not applied yet (the DP itself is the first sub-DP of its fold)."""
    if state.last_dp is None:
        return None
    today = _day(today)
    dp = date.fromisoformat(state.last_dp)
    last = date.fromisoformat(state.last_sub_dp) if state.last_sub_dp else None
    cands = [add_months(dp, k * sub_months) if k else dp for k in range(int(state.fold["dp_months"]) // sub_months)]
    due = [c for c in cands if c <= today and (last is None or c > last)]
    return max(due) if due else None


def _fast_clock(state: DailyState, known: TradeCache, today: date, new_dp: bool) -> dict | None:
    acts = {cfg.rid: act for cfg, act in policy_entries(state.policy) if act is not None}
    acts = {r: a for r, a in acts.items() if r in state.book}
    if not acts:
        return None
    fm = _fm(state)
    if new_dp:
        state.trend_cal = {}
    sub = due_sub(state, today, min(a.sub_months for a in acts.values()))
    if sub is None:
        return None
    out = {}
    for rid, act in acts.items():
        be = state.book[rid]
        cfg = be.config
        spec, ex = EntrySpec(cfg.method, be.threshold, cfg.direction), _exit_spec(cfg, be.exit_id)
        per_sym = {s: known.trades(s, spec, ex, be.filters) for s in be.eligible if s in known.arrays}
        if act.mode == "trendiness" and rid not in state.trend_cal:
            state.trend_cal[rid] = calibrate_trendiness(known, fm, live_fold(fm, date.fromisoformat(state.last_dp)),
                                                        per_sym, act)
        prev = state.act_state.setdefault(rid, {})
        w = decide_sub(fm, known, SimpleNamespace(dp=sub), per_sym, act, prev, state.trend_cal.get(rid))
        prev.update(w)
        state.weights[rid] = w
        out[rid] = {"n_eligible": len(w), "n_active": sum(v > 0 for v in w.values())}
    state.last_sub_dp = str(sub)
    return {"sub_dp": str(sub), "rows": out}


def _overlays(state: DailyState, today: date) -> dict:
    out = {}
    start = date.fromisoformat(state.fold["first_dp"])
    for entry, (cfg, _) in zip(state.policy, policy_entries(state.policy)):
        ov = overlay_of(entry)
        if ov is None:
            continue
        closed = state.books[cfg.rid].closed if cfg.rid in state.books else []
        out[cfg.rid] = overlay_scale(row_daily_pnl(closed, start, _day(today)), ov, cfg.capital)
    state.row_scale = {r: v["scale"] for r, v in out.items()}
    return out


# --- the day ----------------------------------------------------------------------------------------------
def open_phase(state: DailyState, arrays: dict[str, SymbolArrays], today: date, broker, cost_model=None) -> dict:
    """Execute the orders planned at the previous close. Paper: at today's bar open (arrays through today);
    live MT5: run at the open, the adapter fills at market (today's bar need not exist yet). Dividends of today
    are accrued here when today's bars are known, otherwise in the close phase (positions closed at today's
    open then miss that dividend in the books; the broker's cash is the reference in live)."""
    if state.filled_day is not None and _at(_d(state.filled_day)) >= _at(today):
        raise ValueError(f"open of {today} already executed")
    pt = PaperTrader(broker, books=state.books, pending=list(state.pending), cost_model=cost_model,
                     div_day=_d(state.div_day))
    rec = pt.fill_pending(arrays, today)
    state.div_day = str(pt.div_day) if pt.div_day else state.div_day
    for (r, s) in list(state.opened_with):          # positions closed today no longer need their setting
        if s not in pt.books.get(r, RowBook(r)).positions:
            del state.opened_with[(r, s)]
    state.books, state.pending, state.filled_day = pt.books, [], str(today)
    if hasattr(broker, "net"):
        state.sim_net = dict(broker.net)
    return {"day": str(today), "reconciled": rec["ok"], "diffs": rec["diffs"]}


def close_phase(state: DailyState, arrays: dict[str, SymbolArrays], bars_hist: pl.DataFrame,
                membership: pl.DataFrame | None, today: date, cost_model=None, regime=None,
                data_version: str = "live", lot_step: float = 1e-9, halt: str | None = None) -> dict:
    """After today's close: refresh the book at a DP, then plan the next open.
    halt (kill switch): "halt_new" plans exits only; "flatten" closes every position at the next open."""
    if state.last_day is not None and _at(_d(state.last_day)) >= _at(today):
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
        configs = [cfg for cfg, _ in policy_entries(state.policy)]
        new = decide_book(_fm(state), known, bars_hist.filter(pl.col("date") <= today), membership, configs, dp)
        diff = book_diff(state.book, new)
        state.book, state.last_dp, state.last_sub_dp = new, str(dp), None
        state.weights = {r: w for r, w in state.weights.items() if r in new}
        report["dp"] = {"dp": str(dp), **{k: diff[k] for k in ("added", "removed", "changed")}}
    fc = _fast_clock(state, known, today, dp is not None)
    if fc is not None:
        report["fast_clock"] = fc
    pt = PaperTrader(None, books=state.books, div_day=_d(state.div_day))
    pt.accrue_dividends(arrays, today)             # live: the open ran before today's bar existed
    state.div_day = str(pt.div_day) if pt.div_day else state.div_day
    ov_report = _overlays(state, today)
    if ov_report:
        report["overlays"] = ov_report
    exit_only = set(state.book) if halt else frozenset()
    plan = pt.plan(known, state.book, today, exit_only=exit_only, lot_step=lot_step, opened_with=state.opened_with,
                   weights=state.weights, row_scale=state.row_scale, flatten=halt == "flatten")
    if halt:
        report["halt"] = halt
    for o in plan.orders:
        if o.intent == "open":
            state.opened_with[(o.row, o.symbol)] = state.book[o.row]
    state.books, state.pending, state.last_day = pt.books, list(pt.pending), str(today)
    report.update({"orders": len(plan.orders), "warnings": plan.warnings,
                   "open_positions": sum(len(b.positions) for b in state.books.values())})
    return report


def bar_step(state: DailyState, arrays: dict[str, SymbolArrays], bars_hist: pl.DataFrame,
             membership: pl.DataFrame | None, bar, broker, live: bool = False, cost_model=None, regime=None,
             data_version: str = "live", lot_step: float = 1e-9, halt: str | None = None) -> dict:
    """One intraday bar (or one day). Paper (`live=False`): fill the pending orders at this bar's open, then plan
    at its close - the research timing. Live: the job runs right after `bar` closed; plan at its close, then
    execute at once at market (the next bar's open, which does not exist in the data yet)."""
    if not live:
        return daily_step(state, arrays, bars_hist, membership, bar, broker, cost_model, regime, data_version,
                          lot_step, halt)
    rep = close_phase(state, arrays, bars_hist, membership, bar, cost_model, regime, data_version, lot_step, halt)
    a = next(iter(arrays.values()), None)
    nxt = _next_bar(a.dates[a.dates <= np.datetime64(bar)]).astype(object) if a is not None else bar
    rec = open_phase(state, arrays_upto(arrays, bar), nxt, broker, cost_model)
    rep.update({"reconciled": rec["reconciled"], "diffs": rec["diffs"], "executed_at": str(nxt)})
    state.log.append({k: rep[k] for k in ("day", "reconciled", "orders", "open_positions")})
    return rep


def daily_step(state: DailyState, arrays: dict[str, SymbolArrays], bars_hist: pl.DataFrame,
               membership: pl.DataFrame | None, today: date, broker, cost_model=None, regime=None,
               data_version: str = "live", lot_step: float = 1e-9, halt: str | None = None) -> dict:
    """Paper mode in one call: fill yesterday's plan at today's open, then plan at today's close."""
    rec = open_phase(state, arrays, today, broker, cost_model)
    rep = close_phase(state, arrays, bars_hist, membership, today, cost_model, regime, data_version, lot_step, halt)
    rep.update({"reconciled": rec["reconciled"], "diffs": rec["diffs"]})
    state.log.append({k: rep[k] for k in ("day", "reconciled", "orders", "open_positions")})
    return rep
