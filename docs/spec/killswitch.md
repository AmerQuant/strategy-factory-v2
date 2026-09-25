# Kill switch, risk limits and events

Code: `forward/killswitch.py`, `forward/alerts.py`; used by `scripts/run_daily.py`, `scripts/run_intraday.py`, the
scheduler and the dashboard (Paper & live page, banner on every page). Tests: `tests/test_killswitch.py`.

## The switch
One file, `<admin config>/killswitch.json`. Every job the dashboard or the scheduler starts gets its path in
`SF_KILL_FILE` (and the event log in `SF_EVENTS_FILE`); a job started by hand takes `--kill-file` / `--events-file`.
- `halt_new`: the next plan opens nothing; open positions keep exiting by their own rules.
- `flatten`: the next plan sends a close order for every open position and opens nothing.
- Jobs read it before every plan (intraday: before every bar). A job can trip it but never release it, and never
  weakens it (an active `flatten` is not turned into `halt_new`). Only a person resumes (dashboard: Resume).
- Tested: `halt_new` plans no entries and only exits the positions whose own rule fired; `flatten` plans a close for
  every position.

## Automatic limits (in the same file, edited on the Paper & live page)
| limit | meaning | default |
|---|---|---|
| `capital` | base for the percentages | 100000 |
| `max_drawdown` | realised equity below its peak, as a fraction of capital | 0.15 |
| `max_daily_loss` | the day's realised loss, as a fraction of capital | off |
| `max_recon_failures` | consecutive reconciliation mismatches with the broker | 2 |
Checked at every close on the book the job has just updated, before planning; a breach trips `halt_new` with the
reason and emits one critical event (tested: the run_daily script trips once and every later day plans no entries).
Limits are on realised pnl of the book; open-position (mark-to-market) losses do not count yet.

## Events
`<admin config>/events.jsonl`, one JSON line per event (at, level, source, message). Written by the jobs
(critical: switch tripped; warning: book and broker differ), the scheduler (warning: missed run, failed chain) and
the dashboard (critical: switch set; info: resumed). Shown on the Paper & live page, newest first.
Sending events to a phone / mailbox is the next step; the channel is the owner's choice.

## Not yet
- Mark-to-market limits (need live prices of open positions).
- A notification channel for the events.
