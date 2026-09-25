"""Market-time triggers. All times are wall-clock times in `tz` (default New York), so US daylight saving is
followed automatically; fire times are returned as aware UTC datetimes.

- daily: at `time` on `weekdays` (0 = Monday), e.g. 16:30 after the close, or 09:31 for the MT5 open phase.
- bars:  after every bar of the session closes, plus `delay_minutes` (time for the data refresh). Bars start at
         `session_start` and are `every_minutes` long; the last bar closes at `session_end` even if shorter.
Exchange holidays are not modelled: the jobs treat a day without new data as a no-op.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KINDS = ("daily", "bars")


def _hm(s: str) -> time:
    h, m = (int(x) for x in s.split(":"))
    return time(h, m)


@dataclass(frozen=True)
class Trigger:
    kind: str = "daily"
    time: str = "16:30"
    tz: str = "America/New_York"
    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)
    every_minutes: int = 60
    session_start: str = "09:30"
    session_end: str = "16:00"
    delay_minutes: int = 5

    def __post_init__(self):
        if self.kind not in KINDS:
            raise ValueError(f"trigger kind must be one of {KINDS}")
        try:
            ZoneInfo(self.tz)
        except (KeyError, ValueError) as e:                # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"unknown time zone {self.tz!r}") from e
        for t in (self.time, self.session_start, self.session_end):
            _hm(t)
        if not self.weekdays or any(d not in range(7) for d in self.weekdays):
            raise ValueError("weekdays: numbers 0 (Monday) to 6")
        if self.kind == "bars":
            if not 1 <= self.every_minutes <= 1440:
                raise ValueError("every_minutes must be between 1 and 1440")
            if _hm(self.session_end) <= _hm(self.session_start):
                raise ValueError("session_end must be after session_start")
        if not 0 <= self.delay_minutes <= 720:
            raise ValueError("delay_minutes must be between 0 and 720")

    def fires_on(self, day: date) -> list[datetime]:
        """The day's fire times (aware, in `tz`)."""
        if day.weekday() not in self.weekdays:
            return []
        z = ZoneInfo(self.tz)
        if self.kind == "daily":
            return [datetime.combine(day, _hm(self.time), z)]
        start = datetime.combine(day, _hm(self.session_start), z)
        end = datetime.combine(day, _hm(self.session_end), z)
        step, delay = timedelta(minutes=self.every_minutes), timedelta(minutes=self.delay_minutes)
        closes, t = [], start + step
        while t < end:
            closes.append(t)
            t += step
        closes.append(end)
        return [c + delay for c in closes]

    def next_fire(self, after: datetime) -> datetime:
        """First fire strictly after `after` (aware), as an aware UTC datetime."""
        z = ZoneInfo(self.tz)
        day = after.astimezone(z).date() - timedelta(days=1)       # a delay can push a fire past midnight
        for _ in range(16):
            for f in self.fires_on(day):
                if f > after:
                    return f.astimezone(UTC)
            day += timedelta(days=1)
        raise ValueError("no fire time within two weeks")          # unreachable with valid weekdays

    def bar_label(self, fire: datetime) -> datetime:
        """For a bars trigger: the start of the bar whose close the fire follows (local wall time)."""
        z = ZoneInfo(self.tz)
        close = fire.astimezone(z) - timedelta(minutes=self.delay_minutes)
        start = datetime.combine(close.date(), _hm(self.session_start), z)
        k = max(0, -(-int((close - start).total_seconds()) // (60 * self.every_minutes)) - 1)
        return start + timedelta(minutes=k * self.every_minutes)
