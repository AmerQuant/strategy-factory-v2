"""Trading sessions from a hand-written CSV (the MetaTrader 5 Python package has no session function).

    symbol,weekday,start,end
    EURUSD,mon,00:00,24:00
    USA30IDXUSD,mon,01:00,23:00          # several rows per day = a break between them

Times are on the broker clock of the run: 00:00 = 17:00 New York (MT5 server day, `clock_shift="ny"`), so the file
can be copied from Moneta's contract specification in server time. `end` may be 24:00. Rows of one symbol and
weekday must not overlap. Nothing is inferred: a symbol without rows has no session and is reported.

The research engine does not need the sessions (it trades the bars the data has); they are used to audit the data
(`bars_outside`: bars that start outside the declared session, e.g. a data vendor's quotes in the broker's break).
"""
from __future__ import annotations

import csv
from itertools import pairwise
from pathlib import Path

import polars as pl

WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def _minutes(s: str) -> int:
    h, m = (int(x) for x in s.strip().split(":"))
    if not (0 <= h <= 24 and 0 <= m < 60) or (h == 24 and m != 0):
        raise ValueError(f"bad time {s!r}")
    return h * 60 + m


def read_sessions(path: str | Path) -> dict[str, dict[int, list[tuple[int, int]]]]:
    """symbol -> {weekday 0 = Monday: [(start_min, end_min), ...]} with validation."""
    out: dict[str, dict[int, list[tuple[int, int]]]] = {}
    with open(path, encoding="utf-8", newline="") as fh:
        for n, r in enumerate(csv.DictReader(fh), start=2):
            wd = (r.get("weekday") or "").strip().lower()[:3]
            if wd not in WEEKDAYS:
                raise ValueError(f"line {n}: weekday {r.get('weekday')!r} is not one of {WEEKDAYS}")
            a, b = _minutes(r["start"]), _minutes(r["end"])
            if b <= a:
                raise ValueError(f"line {n}: session end {r['end']} is not after start {r['start']}")
            out.setdefault(r["symbol"].strip(), {}).setdefault(WEEKDAYS.index(wd), []).append((a, b))
    for sym, days in out.items():
        for wd, iv in days.items():
            iv.sort()
            for (_, e1), (s2, _) in pairwise(iv):
                if s2 < e1:
                    raise ValueError(f"{sym} {WEEKDAYS[wd]}: overlapping sessions")
    return out


def bars_outside(bars: pl.DataFrame, sessions: dict) -> dict:
    """Per symbol: bars whose start is outside the declared session (intraday bars on the broker clock), and the
    symbols that have no session rows at all."""
    if not isinstance(bars.schema["date"], pl.Datetime):
        raise TypeError("bars_outside needs intraday bars")
    out, missing = {}, []
    for (sym,), g in bars.partition_by("symbol", as_dict=True).items():
        s = sessions.get(sym)
        if s is None:
            missing.append(sym)
            continue
        wd = g["date"].dt.weekday().to_numpy() - 1
        mins = (g["date"].dt.hour().cast(pl.Int32) * 60 + g["date"].dt.minute().cast(pl.Int32)).to_numpy()
        bad = sum(1 for w, m in zip(wd, mins) if not any(a <= m < b for a, b in s.get(int(w), ())))
        if bad:
            out[sym] = bad
    return {"outside": out, "no_session": sorted(missing)}
