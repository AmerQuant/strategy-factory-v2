# Scheduling the jobs

Chosen: **option C, a separate scheduler service**, built on the standard library (own market-time triggers; the
only new package is `tzdata`, on Windows). Code: `src/sfactory/scheduler/` (`triggers.py`, `service.py`,
`python -m sfactory.scheduler`). Tests: `tests/test_scheduler.py`. Options A (Task Scheduler) and B (inside the web
server) were rejected: A keeps schedules outside the dashboard and needs manual DST handling, B makes trading depend
on the web server staying up.

## What it does
- Schedules are admin documents (Admin, Schedules): a chain of job presets + a trigger + a missed-run rule.
- **Triggers** (wall-clock time in `America/New_York` by default, so US daylight saving is automatic):
  - `daily` - at `time` on `weekdays` (0 = Monday), e.g. 16:30 after the close, 09:31 for the MT5 open phase;
  - `bars` - after every bar of the session closes plus `delay_minutes` (time for the data refresh); bars start
    at `session_start` and are `every_minutes` long, the last one closes at `session_end`.
  Exchange holidays are not modelled: the jobs treat a day without new data as a no-op (run_daily and
  run_intraday both do).
- **A fire** runs the chain's presets one after the other through the dashboard's job manager (each step appears in
  Jobs with its log) and stops at the first failure.
- **Rules**:
  - a fire missed by more than `grace_minutes` (service down, machine asleep) is recorded as `missed`, or with
    `misfire: run_once` run once late - use that only for paper jobs, never for live;
  - a schedule whose previous run is still going is not started again (`skipped`);
  - saving a new or edited schedule plans it from now; it never fires on save.
- Preset arguments may contain `{today}` (the fire's local date in the schedule's time zone) and `{now}`.
- The Jobs page shows the service (running / not running, last tick), each schedule's next and last run, and the
  logs of the last run's steps. The dashboard never marks a job the scheduler is running as "unknown".
- Files in the config directory: `scheduler/state.json` (next fire per schedule), `scheduler/runs/*.json`,
  `scheduler/heartbeat.json`. A second service on the same directory is refused while the heartbeat is fresh.

## Run it
```
uv sync --group web                      # the service reads the admin documents (pydantic)
uv run python -m sfactory.scheduler --config D:/sf2_admin [--tick 15]
```

### Keeping it running on Windows
- **Paper only**: a Windows service is simplest, e.g. with NSSM:
  ```
  nssm install sfactory-scheduler D:\...\strategy-factory-v2\.venv\Scripts\python.exe "-m sfactory.scheduler --config D:\sf2_admin"
  nssm set sfactory-scheduler AppDirectory D:\...\strategy-factory-v2
  nssm start sfactory-scheduler
  ```
- **Live MT5**: not verified yet. The MetaTrader5 Python package talks to a terminal running in the user's desktop
  session, and Windows services run in a separate session, so a service may not reach the terminal. Safer: start
  the scheduler in your own session at log on (Task Scheduler, trigger "At log on", action the same command,
  "restart on failure" on), with the MT5 terminal set to start at log on too. Test with `--dry-run` presets first.
- `SF_MT5_PASSWORD` must be in the environment of whatever starts the scheduler (user environment variable).

## Suggested schedules
| schedule | trigger | steps (presets) | missed run |
|---|---|---|---|
| paper, daily | daily 16:30, Mon-Fri | `run_daily --state paper.json --store <store> --broker sim` | run once late |
| live MT5, open | daily 09:31, Mon-Fri | `run_daily ... --broker mt5 --phase open --day {today}` | skip |
| live MT5, close | daily 16:30, Mon-Fri | `run_daily ... --broker mt5 --phase close` | skip |
| paper, 1H | bars 60 min, 09:30-16:00, delay 5 | `run_intraday --state s1h.json --store <store> --timeframe 1H` | run once late |
| live, 1H | bars 60 min, 09:30-16:00, delay 5 | `run_intraday ... --broker mt5` | skip |
| research, weekly | daily 18:00, Sat | `check_survivorship ...`, then `run_real ...` | run once late |
The MT5 open phase needs `--day {today}`: at the open the store's last day is still yesterday.

## Open
- A job type for the v1 data refresh (its command is on the owner's machine): once it exists, a chain can be
  "refresh, then job" inside one schedule. Until then the refresh must run before the schedule's time (the
  `delay_minutes` of a bars trigger leaves room for it).
- Alerts on failed / missed runs (the next package: alerts + kill switch).
