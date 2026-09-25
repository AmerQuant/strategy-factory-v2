# Daily paper / live job

Code: `forward/daily.py` (state, phases), `forward/paper.py` (planner), `scripts/run_daily.py` (CLI).
Tests: `tests/test_daily.py`.

## State (`DailyState`, one JSON file, replaced atomically)
Frozen policy (row configs as dicts, as the holdout's `freeze_policy` writes them), fold schedule (first DP,
`dp_months`, `is_years`), last DP, current book, `opened_with` (the setting each open position was opened with),
row books (virtual positions, closed trades), pending orders, simulated-broker positions (paper), daily log.

## Phases
- **open** - execute the orders planned at the previous close. Paper: at the bar open. Live MT5: run at the open;
  the adapter fills at market (the store need not have today's bar yet; reference prices stand in).
- **close** - at a DP (first DP, then every `dp_months`, catching up after a pause) rebuild the book with the
  research selector on data before the DP and log the book diff; then plan the next open with the parity planner.
- **both** - paper mode in one run (open then close). A day cannot be processed twice; the close refuses to run
  while orders are still pending.

## Rule that matters: positions keep their setting
A position exits by the rules of the setting it was opened with (as in research, where a trade opened in fold k
runs its course after the next DP), even if the row's threshold / exit / filter changed at the DP or the row left
the book. New entries use the current book. Tested over six DPs with changing parameters: no position ever
lost its exit rule, and a run that saves and reloads the state every day gives the same trades as one in memory.

## Commands
```
uv run python scripts/run_daily.py --init --state state.json --policy holdout.json --first-dp 2026-10-01
uv run python scripts/run_daily.py --state state.json --store <store> --broker sim           # paper, after the close
uv run python scripts/run_daily.py --state state.json --store <store> --broker mt5 --phase open  --mt5-login N --mt5-server S --symbol-map broker_symbols.csv [--dry-run]
uv run python scripts/run_daily.py --state state.json --store <store> --broker mt5 --phase close
```
The MT5 password comes from `SF_MT5_PASSWORD`. Reports: `--report-dir` gets `<day>-<phase>.json`.

## Not yet
- The store must be updated before each close run (v1's data refresh); the job does not download.
- Scheduling (Windows Task Scheduler at the open and after the close) is the owner's setup.
- The fast clock (edge on/off) and sizing overlays are not applied in the live book yet.
