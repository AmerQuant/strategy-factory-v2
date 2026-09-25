"""`python -m sfactory.scheduler --config <dir> [--tick 15]` - run the scheduler service (see service.py).

Install it as a Windows service so it starts with the machine and restarts after a crash (docs/spec/scheduling.md).
A second instance is refused while the heartbeat of a running one is fresh.
"""
from __future__ import annotations

import argparse

from sfactory.scheduler.service import HEARTBEAT_STALE, SchedulerService, heartbeat_age


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="the admin configuration directory (same as the dashboard)")
    ap.add_argument("--tick", type=float, default=15, help="seconds between passes")
    a = ap.parse_args(argv)
    age = heartbeat_age(a.config)
    if age is not None and age < HEARTBEAT_STALE:
        raise SystemExit(f"a scheduler is already running on {a.config} (heartbeat {int(age.total_seconds())} s ago)")
    SchedulerService(a.config).run_forever(a.tick)


if __name__ == "__main__":
    main()
