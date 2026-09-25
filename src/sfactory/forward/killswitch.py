"""Kill switch and risk limits for the paper / live jobs (docs/spec/killswitch.md).

One JSON file (by default `<admin config>/killswitch.json`; the job manager passes its path to every job it starts
as SF_KILL_FILE) holds the switch and the limits:

    {"active": false, "mode": "halt_new", "reason": "", "at": null, "by": "",
     "limits": {"capital": 100000, "max_drawdown": 0.15, "max_daily_loss": 0.03, "max_recon_failures": 2}}

- mode "halt_new": no new entries; open positions keep their own exits.
- mode "flatten":  close every open position at the next open, open nothing.
- The jobs read it before planning and trip it themselves (mode halt_new) when a limit is breached on the book they
  just updated: drawdown of realised equity from its peak, the day's realised loss (both as fractions of `capital`),
  or `max_recon_failures` consecutive reconciliation mismatches with the broker. A limit left empty is off.
- Only a person resumes trading (dashboard or `resume`); the jobs never switch it off.
Every trip / resume is appended to the event log (`alerts.py`), from which notifications are sent.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MODES = ("halt_new", "flatten")


@dataclass
class Limits:
    capital: float = 100_000.0
    max_drawdown: float | None = 0.15
    max_daily_loss: float | None = None
    max_recon_failures: int | None = 2


@dataclass
class KillSwitch:
    active: bool = False
    mode: str = "halt_new"
    reason: str = ""
    at: str | None = None
    by: str = ""
    limits: Limits = field(default_factory=Limits)

    @property
    def halt(self) -> str | None:
        return self.mode if self.active else None

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict) -> KillSwitch:
        lim = Limits(**(d.get("limits") or {}))
        k = cls(**{**{x: d[x] for x in ("active", "mode", "reason", "at", "by") if x in d}, "limits": lim})
        if k.mode not in MODES:
            raise ValueError(f"kill switch mode must be one of {MODES}")
        return k


def kill_file(explicit: str | None = None) -> Path | None:
    p = explicit or os.environ.get("SF_KILL_FILE")
    return Path(p) if p else None


def load(path: str | Path | None) -> KillSwitch:
    """A missing file is an inactive switch with default limits."""
    if path is None or not Path(path).exists():
        return KillSwitch()
    return KillSwitch.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def save(path: str | Path, k: KillSwitch) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(k.to_json(), indent=1), encoding="utf-8")
    tmp.replace(p)


def trip(path: str | Path, reason: str, by: str, mode: str = "halt_new") -> KillSwitch:
    k = load(path)
    if k.active and (k.mode == "flatten" or mode == k.mode):   # never weaken an active switch
        return k
    k.active, k.mode, k.reason, k.by = True, mode, reason, by
    k.at = datetime.now(UTC).isoformat(timespec="seconds")
    save(path, k)
    return k


def resume(path: str | Path, by: str) -> KillSwitch:
    k = load(path)
    k.active, k.reason, k.by, k.at = False, "", by, datetime.now(UTC).isoformat(timespec="seconds")
    save(path, k)
    return k


def breached(state, limits: Limits, day, reconciled_now: bool | None = None) -> str | None:
    """The first limit the book breaches at `day`'s close, as a readable reason; None if all are fine.
    reconciled_now: the result of today's fill reconciliation when it is not in the log yet."""
    closed = sorted((t for b in state.books.values() for t in b.closed), key=lambda t: str(t["exit_date"]))
    eq = peak = 0.0
    for t in closed:
        eq += float(t["net_pnl"])
        peak = max(peak, eq)
    cap = float(limits.capital) or 1.0
    if limits.max_drawdown and (peak - eq) / cap > limits.max_drawdown:
        return f"drawdown {(peak - eq) / cap:.1%} of capital > limit {limits.max_drawdown:.1%}"
    d = str(day)[:10]
    today_pnl = sum(float(t["net_pnl"]) for t in closed if str(t["exit_date"])[:10] == d)
    if limits.max_daily_loss and -today_pnl / cap > limits.max_daily_loss:
        return f"loss today {-today_pnl / cap:.1%} of capital > limit {limits.max_daily_loss:.1%}"
    if limits.max_recon_failures:
        n = 0
        results = [x.get("reconciled") for x in state.log] + ([reconciled_now] if reconciled_now is not None else [])
        for r in reversed(results):                    # None (no fills that day) neither counts nor resets
            if r is False:
                n += 1
            elif r is True:
                break
        if n >= limits.max_recon_failures:
            return f"{n} consecutive reconciliation mismatches with the broker"
    return None
