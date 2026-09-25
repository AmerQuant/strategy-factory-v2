# Scheduling the jobs - options (owner's decision, nothing implemented yet)

What has to run, on the Windows machine that also runs the MT5 terminal:
| job | when | order |
|---|---|---|
| v1 data refresh | after the US close (daily); after every bar (intraday) | first |
| `run_daily.py --phase close` | after the refresh, before the next open | second |
| `run_daily.py --phase open` (MT5) | at the US open | - |
| `run_intraday.py --broker mt5` | right after every 1H / 4H bar, after the refresh | second |
| research runs (`run_real.py`) | on demand or weekly | anytime |

Constraints from the code as it stands:
- Paper jobs catch up missed days / bars by themselves; a missed **live** intraday bar stops the job (it refuses
  to execute old bars), and a missed daily open leaves orders pending until the next open. So reliability of the
  trigger matters more for live than for paper.
- Times are New York market times; the scheduler must follow US daylight saving (the machine's clock may not be in
  New York time).
- The refresh must finish before the job starts (a chain, not two independent timers).
- The dashboard already runs jobs from presets and keeps their logs (`web/jobs.py`); anything that reuses it gets
  the logs in the Jobs page for free.

## A. Windows Task Scheduler
One task per job, each calling a small `.cmd` that runs the refresh and then the job.
- For: part of the OS, starts with Windows, no extra process to keep alive, runs even if the dashboard is down;
  "run task as soon as possible after a scheduled start is missed" covers a sleeping machine.
- Against: schedules live outside the platform (not visible / editable in the dashboard); triggers are in the
  machine's local time, so New York DST shifts need either a New York machine clock or tasks adjusted twice a year;
  hourly triggers inside market hours need a repeat pattern per task; logs only in files unless the job reports to
  the dashboard.
- Implementation: a `scripts/make_tasks.py` that writes the `.cmd` wrappers and `schtasks /create` commands (or task
  XML) from the job presets; nothing changes in the server. Smallest amount of code.

## B. Scheduler inside the web server (APScheduler)
Schedules become a document type in the admin (cron expression + preset + time zone); the server starts the preset
through the existing job manager when a schedule fires.
- For: everything in one place - create, edit, pause schedules and see every run and its log in the dashboard;
  cron triggers in `America/New_York` follow DST automatically; chaining is just "refresh preset, then job preset".
- Against: the web server becomes critical infrastructure - if it is stopped or crashes, nothing trades; it must run
  as a single process (several workers would fire every schedule several times); one new dependency
  (`apscheduler`); a missed fire while the server was down needs an explicit catch-up rule.
- Implementation: `Schedule` schema + collection, a scheduler started in `create_app` (single-instance lock file),
  "next run" / "last run" columns in the Jobs page, tests with a fake clock. Medium amount of code.

## C. Separate scheduler service
The same schedule documents as B, but executed by a small standalone process (`python -m sfactory.scheduler`),
installed as a Windows service (for example with NSSM); the dashboard only edits schedules and shows the runs.
- For: schedules are managed in the dashboard like B, but trading does not depend on the web server; the service
  restarts automatically; one clear owner of "what runs when".
- Against: one more process to install and monitor; the most code of the three; the service and the dashboard must
  share the config directory and job records (they already live in files, so this is straightforward).
- Implementation: B's schedule model + a stdlib loop (or APScheduler) in its own module, a heartbeat file the
  dashboard shows ("scheduler alive, last tick"), the service install notes. Largest amount of code.

## Comparison
| | A. Task Scheduler | B. in the web server | C. separate service |
|---|---|---|---|
| schedules visible / editable in the dashboard | no | yes | yes |
| trading keeps running if the dashboard is down | yes | no | yes |
| New York DST handled | manually | automatically | automatically |
| refresh -> job chaining | in the `.cmd` | in the schedule | in the schedule |
| extra processes to keep alive | none | none (but the server must stay up) | one service |
| new dependencies | none | apscheduler | none or apscheduler |
| code to write | small | medium | largest |

Whatever is chosen, the jobs themselves do not change: they are already restartable, idempotent per day / bar and
refuse unsafe catch-up in live mode.
