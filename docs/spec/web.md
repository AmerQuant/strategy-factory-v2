# Web admin and dashboard

Stack (approved): FastAPI backend in `src/sfactory/web/`, React + TypeScript + Vite + Tailwind 4 + shadcn-style
components (Radix) + ECharts frontend in `web/`. English UI, light and dark mode, fonts bundled (works offline).

## Run it
```
uv sync --group web
cd web && npm install && npm run build && cd ..          # once, and after frontend changes
uv run --group web python -m sfactory.web --config D:/sf2_admin --port 8765
```
Open http://127.0.0.1:8765. For frontend development: `npm run dev` in `web/` (port 5173, proxies /api to 8765).
The server binds to 127.0.0.1 and has no login: put it behind an authenticating reverse proxy before exposing it.
No secrets are stored; MT5 reads `SF_MT5_PASSWORD` from the environment.

## What it shows (read-only)
- Overview: the latest run's combined OOS curve against the all-rows benchmark, gates, paper books, recent jobs.
- Research runs: every `<runs_root>/<run>/evidence.json`; per run the row table (path, Sharpe, DSR, BH, drawdown),
  Sharpe per configuration and DSR-vs-Sharpe charts coloured by edge family, weights per fold, family ensembles,
  robustness checks, edge / sizing analysis, fold decisions per row, the Persian HTML report.
- Trial registry: trials per row and the recent trials from the DuckDB registry.
- Paper & live: each `run_daily` state file: realised P&L curve, book, open positions, pending orders, per-row
  results, closed trades, daily log with reconciliation.

## What it edits (admin)
Every document is a JSON file under `<config>/<collection>/<id>.json`, validated on save by the platform itself
(row configurations are built with `config_from_dict`, activation settings with `ActivationConfig`), so nothing the
research code cannot run can be saved.

| section | content |
|---|---|
| Platform | data store, runs folder, registry, live folder, scripts folder, interpreter, default costs, workers, capital |
| Catalogues | versioned row sets (method, side, rung, positions, sizing, universe; every other field in the JSON view); one click adds the 18 equity rows or the diverse-family rows |
| Policies | rows to trade with an optional edge on/off mechanism; "Create policy" on a run copies the frozen holdout rows |
| Risk budgets | row cap, family cap, portfolio vol target, leverage cap |
| Cost profiles | default spread / commission / slippage / swap and the per-symbol CSV |
| Symbol maps | research symbol to broker symbol |
| Broker accounts | simulated or MT5 (login, server, symbol map, dry run) |
| Job presets | saved command lines for run_real, run_daily, the cost converter and the speed benchmark |

Jobs: start a preset, watch its log, stop it. Jobs run the repository's own scripts as subprocesses; metadata and
logs stay in `<config>/jobs/`.

## Extending
- New collection: add a pydantic model to `web/schemas.py` (`COLLECTIONS`), and a spec (fields, columns, blank
  document) to `web/src/pages/admin/Collection.tsx`.
- New page: a component in `web/src/pages/`, a route in `main.tsx`, a navigation item in `components/Layout.tsx`.
- New API view: a reader in `web/readers.py` and a route in `web/app.py` (tests: `tests/test_web.py`).
- Colours: edge families have fixed hues (`--color-fam-*` in `index.css`) used by tables and charts alike.

## Not yet
- Login and multiple users; a job scheduler (the daily job is still started by Windows Task Scheduler).
- `package-lock.json` is not committed yet: run `npm install` once and commit the lock file it creates.
