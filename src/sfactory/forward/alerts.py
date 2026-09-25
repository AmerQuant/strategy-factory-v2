"""Event log for notifications (docs/spec/killswitch.md). Jobs and the scheduler append one JSON line per event to
`<admin config>/events.jsonl` (path passed to jobs as SF_EVENTS_FILE). Sending the events to a channel is a separate,
pluggable step; the log is the source of truth and is shown in the dashboard.

Levels: "critical" (kill switch tripped, live job stopped), "warning" (missed run, failed run, reconciliation
mismatch), "info" (resumed, run succeeded after a failure)."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path


def events_file(explicit: str | None = None) -> Path | None:
    p = explicit or os.environ.get("SF_EVENTS_FILE")
    return Path(p) if p else None


def emit(path: str | Path | None, level: str, source: str, message: str, **data) -> dict:
    ev = {"at": datetime.now(UTC).isoformat(timespec="seconds"), "level": level, "source": source,
          "message": message, **data}
    if path is not None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as fh:            # one line per event: appends never corrupt old ones
            fh.write(json.dumps(ev, default=str) + "\n")
    return ev


def read(path: str | Path | None, limit: int = 200) -> list[dict]:
    if path is None or not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines()[-limit:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out[::-1]
