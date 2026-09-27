"""Progress lines for long runs (stages, counters, elapsed time, ETA), written to stderr so they appear in the job
log of the dashboard without mixing into the scripts' JSON on stdout.

    [progress] stage: trade cache (elapsed 2m05s)
    [progress] trade cache 1200/6708 (17.9%) elapsed 1m40s eta 7m38s
    [progress] catalogue rows 3/22 (13.6%) MR-IBS-BUY-EQ elapsed 12m10s eta 1h17m

A counter prints at most every `min_interval` seconds, plus its first and last step, so a long loop stays readable.
The ETA assumes the remaining steps take as long as the finished ones on average. `SF_PROGRESS=0` silences it.
"""
from __future__ import annotations

import os
import sys
import time


def fmt_seconds(s: float) -> str:
    s = int(max(0, s))
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}h{m:02d}m" if h else f"{m}m{sec:02d}s"


class Counter:
    def __init__(self, reporter: Progress, name: str, total: int):
        self.r, self.name, self.total, self.done = reporter, name, max(0, int(total)), 0
        self.t0 = reporter.clock()
        self._last = None

    def tick(self, n: int = 1, label: str = "") -> None:
        self.done += n
        now = self.r.clock()
        last = self.done >= self.total
        if not (self._last is None or last or now - self._last >= self.r.min_interval):
            return
        self._last = now
        el = now - self.t0
        pct = f" ({100 * self.done / self.total:.1f}%)" if self.total else ""
        eta = (f" eta {fmt_seconds(el / self.done * (self.total - self.done))}"
               if 0 < self.done < self.total else "")
        lab = f" {label}" if label else ""
        self.r.emit(f"{self.name} {self.done}/{self.total}{pct}{lab} elapsed {fmt_seconds(el)}{eta}")


class Progress:
    def __init__(self, stream=None, min_interval: float = 10.0, clock=time.monotonic, enabled: bool | None = None):
        self.stream, self.min_interval, self.clock = stream, min_interval, clock
        self.enabled = os.environ.get("SF_PROGRESS", "1") != "0" if enabled is None else enabled
        self.t0 = clock()

    def emit(self, msg: str) -> None:
        if self.enabled:
            print(f"[progress] {msg}", file=self.stream or sys.stderr, flush=True)

    def stage(self, name: str) -> None:
        self.emit(f"stage: {name} (elapsed {fmt_seconds(self.clock() - self.t0)})")

    def counter(self, name: str, total: int) -> Counter:
        return Counter(self, name, total)


_default: Progress | None = None


def progress() -> Progress:
    """The process-wide reporter (created on first use)."""
    global _default
    if _default is None:
        _default = Progress()
    return _default
