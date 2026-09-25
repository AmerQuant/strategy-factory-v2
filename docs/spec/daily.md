# Daily paper / live job

Code: `forward/daily.py` (state, phases), `forward/paper.py` (planner), `scripts/run_daily.py` (daily CLI),
`scripts/run_intraday.py` (intraday CLI). Tests: `tests/test_daily.py`, `tests/test_intraday_live.py`.

## State (`DailyState`, one JSON file, replaced atomically)
Frozen policy (row configs as dicts, as the holdout's `freeze_policy` writes them), fold schedule (first DP,
`dp_months`, `is_years`), last DP, current book, `opened_with` (the setting each open position was opened with),
row books (virtual positions, closed trades), pending orders, simulated-broker positions (paper), daily log.
Days are stored as `YYYY-MM-DD`, intraday bars as `YYYY-MM-DD HH:MM:SS`; both round-trip exactly.

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

## Fast clock (edge on/off) in the live book
A policy entry may carry an accepted mechanism: `{"config": {...}, "activation": {...ActivationConfig}}` (from
`run_real --analyze-accepted`, chosen by the analyst). At every sub-DP (the DP, then every `sub_months`) the job
computes per-symbol weights with the research `decide_sub` on the shadow trades closed before the sub-DP
(trendiness calibrated in-fold at the DP; hysteresis state persisted). Weight 0 blocks new entries for the symbol,
other weights scale the quantity; exits are never affected. Tested: the live number of active symbols equals the
research `run_activation` at all 24 sub-DPs of a two-year replay.

## Daily sizing overlays in the live book
A policy entry may also carry `"overlay"` (only after the sizing ablation accepted it for that row; the admin's
policy editor has a form for it):
```
{"config": {...}, "overlay": {"target_vol_daily": 800, "lookback": 63, "lev_max": 2.0, "min_obs": 20,
                              "dd_limit": 0.15, "cut": 0.5, "resume": 0.5}}
```
`target_vol_daily` (pnl units per day) switches the vol-target overlay on, `dd_limit` (fraction of the row's
capital) the drawdown brake; unknown fields are refused. At every close the job builds the row's realised net pnl
per business day (exit dates, zeros elsewhere, from the first DP) and evaluates the research functions
`vol_target_daily` and `drawdown_brake` one step past it: the factor research would apply to tomorrow. Tomorrow's
new entries of the row are multiplied by leverage x brake (0 = no new entries); exits and open positions are never
resized. The factors are in the close report (`overlays`) and the state (`row_scale`).
Tested: the factor equals the research overlay one day ahead at every step of a 400-day series; with the brake
engaged the planned entry quantities are exactly the unbraked ones times `cut`.
Note: research applies the overlay to the realised daily pnl stream, the live book to new entries; they agree on
when and how much the row is scaled, not on the exact pnl of positions already open when the factor changes.

## Intraday (1H / 4H)
The same state and planner, one step per bar (`bar_step`, `scripts/run_intraday.py`). Decision points stay calendar
days (the first bar on or after the DP applies it).
- **sim**: each bar's pending orders fill at that bar's open, then the plan is made at its close - the research
  timing. Tested: over 217 hourly bars (six weeks), with the state saved and reloaded before every bar, the job's
  closed trades are exactly the research trades of the book it chose (symbol, signal, entry and exit bar).
- **mt5**: run right after a bar closes; the plan at that close is executed at once at market (the next bar's
  open, not in the data yet). Tested: the same decisions at the same bars as sim; only the fill prices differ.
- Each run processes every bar after the state's last one and saves the state after each. With `--broker mt5`
  more than one new bar is refused: missed bars cannot be executed at their historical prices.
- `--skip-last` ignores the store's last bar when the data refresh also writes the bar that is still forming.
- Bars, `--resample` and `--clock-shift` must be the ones the policy was researched on.

## Commands
```
uv run python scripts/run_daily.py --init --state state.json --policy holdout.json --first-dp 2026-10-01
uv run python scripts/run_daily.py --state state.json --store <store> --broker sim           # paper, after the close
uv run python scripts/run_daily.py --state state.json --store <store> --broker mt5 --phase open  --mt5-login N --mt5-server S --symbol-map broker_symbols.csv [--dry-run]
uv run python scripts/run_daily.py --state state.json --store <store> --broker mt5 --phase close

uv run python scripts/run_intraday.py --init --state s1h.json --policy holdout_1h.json --first-dp 2026-10-01
uv run python scripts/run_intraday.py --state s1h.json --store <store> --timeframe 1H [--resample 4h --clock-shift ny] [--broker mt5 ...]
```
The MT5 password comes from `SF_MT5_PASSWORD`. Reports: `--report-dir` gets one JSON per day / phase or per bar.

## Not yet
- The store must be updated before each run (v1's data refresh); the jobs do not download.
- Scheduling (after every close, or after every bar intraday) is the owner's setup; options in docs/spec/scheduling.md.
