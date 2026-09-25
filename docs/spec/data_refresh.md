# v1 data refresh job and job presets for the MT5 scripts

Code: `scripts/refresh_v1_data.py`; job kinds in `web/jobs.py` / `web/schemas.py`; the admin form
(`web/src/pages/admin/Collection.tsx`). Tests: `tests/test_data_refresh.py`.

## Refresh
v2 never writes into the store (CLAUDE.md); the refresh job starts v1's own CLI in v1's repository:
```
uv run python scripts/refresh_v1_data.py --v1-repo <strategy-factory> \
    --steps "data download dukascopy --series h1; data ingest dukascopy --series h1 --set-reference"
```
- `--step` (repeatable) or `--steps "a; b"` (one argument, for dashboard presets): run in order, the job stops at the
  first step with a non-zero exit code (a failed download never reaches the ingest) and exits 1, so a scheduler
  chain stops too.
- `--runner` (default `uv run sfac`: v1's own uv project), `--data-root` (SFAC_DATA_ROOT for v1), `--timeout`.
- Verified in the v1 code: `data download dukascopy --series h1|m1 [--from YYYY-MM --to YYYY-MM]`,
  `data ingest dukascopy --series h1 [--set-reference]`. Alpaca / Yahoo commands are passed through unchecked.
- A new reference snapshot changes the v1 store version, i.e. v2 sees a new data version.

Typical schedule: preset `refresh` (this job) -> preset `paper-fx` (run_daily --asset-class fx), daily after
17:00 New York; the chain stops if the refresh fails.

## Job presets
New kinds: `export_mt5_specs`, `convert_mt5_costs`, `refresh_v1_data`; the admin form lists them with their flags,
and `--asset-class` / `--symbol-top-n` / `--sessions` were added to the flag lists of run_real, run_daily and
run_intraday. The frontend change was not built or type-checked here (no Node build in this environment).
