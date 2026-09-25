# Web admin and dashboard

Stack (owner's choice, from the options in the chat): FastAPI backend (`src/sfactory/web`) + React / TypeScript
frontend (`web/`, Vite, Tailwind 4, shadcn-style components on Radix, ECharts). English UI, light and dark mode,
local fonts (works offline). Tests: `tests/test_web.py` (skipped when the `web` dependency group is absent).

## Run it
```
uv sync --group web
cd web && npm install && npm run build && cd ..          # once, and after frontend changes
uv run python -m sfactory.web --config D:/sf2_admin      # http://127.0.0.1:8765
```
Development: `npm run dev` in `web/` (port 5173) proxies `/api` to the backend on 8765.

## Access
- Local only (default): `--host 127.0.0.1`, no token needed.
- On a network: set a token of at least 16 characters and bind to the interface:
  ```
  set SF_WEB_TOKEN=<long random string>        (or --token-file D:/sf2_admin/token.txt)
  uv run python -m sfactory.web --config D:/sf2_admin --host 0.0.0.0
  ```
  The server refuses a non-local bind without a token. Every `/api` route except health / login / logout then needs
  `Authorization: Bearer <token>` (scripts) or the session cookie the sign-in screen sets. The cookie holds an HMAC of
  the token (never the token), is HttpOnly and SameSite=Strict, lasts 14 days, and is marked Secure behind HTTPS.
  Beyond a trusted LAN put the server behind an HTTPS reverse proxy.
- One token = one operator role; there are no per-user accounts. No other secrets are stored (the MT5 password stays
  in `SF_MT5_PASSWORD`).

## What it shows (workspace)
| page | source | content |
|---|---|---|
| Overview | latest `evidence.json`, live folder, jobs | combined OOS equity vs all-rows benchmark, accepted rows, effective bets, trials, SPA, holdout, accepted rows by family, live books, recent jobs |
| Research runs | every `<runs_root>/*/evidence.json` | runs table; per run: rows (sortable, family colours, gate path), Sharpe bars, DSR vs Sharpe with the 0.95 gate, combined weights per fold (heatmap, risk-budget leverage), family ensembles, robustness checks, edge & sizing analysis, fold decisions per accepted row, run settings, the HTML report, "Create policy" from the frozen holdout rows |
| Trial registry | the DuckDB registry (read-only) | trials per row, recent trials with metrics |
| Paper & live | `run_daily.py` state files | per book: realised equity, current book, open positions, pending orders, per-row results, closed trades, daily reconciliation log |
| Jobs | `<config>/jobs` | start a preset, follow the log, stop a running job |

## What it edits (admin)
Every document is a JSON file under `<config>/<collection>/<id>.json`, written atomically and validated on save.
| collection | validation |
|---|---|
| Platform settings | paths (store, runs, registry, live folder, dividends, membership), interpreter, scripts folder, default costs, workers, capital |
| Catalogues | every row is built with the platform's own `config_from_dict` (a row the engine cannot run is refused); quick-add of the 18 equity rows or the diverse-family rows; all LadderConfig fields in a JSON view |
| Policies | entries = row config + optional edge on/off mechanism (validated as `ActivationConfig`); frozen flag; created from a run in one click |
| Risk budgets | row cap, family cap, portfolio vol target, leverage limit (bounded) |
| Cost profiles | default bps costs and swap; path of the per-symbol CSV from the Moneta converter |
| Symbol maps | research -> broker symbol pairs |
| Broker accounts | simulated or MT5 (login, server, symbol map, dry run) |
| Job presets | script (run_real, run_daily, cost converter, speed benchmark) + arguments, with flag suggestions per script |

Jobs run with the repository's `src` on `PYTHONPATH`, so they work even if the configured interpreter is not the
project's own environment.

## Extending
- A new admin document type: a pydantic model in `web/schemas.py` + one entry in `COLLECTIONS`; the frontend gets
  a page from one entry in `SPECS` (`web/src/pages/admin/Collection.tsx`) and a line in the navigation.
- A new job type: one entry in `jobs.SCRIPTS` and in `KIND_FLAGS`.
- New evidence fields appear in "Run settings" automatically; charts are one `<Chart option={(c) => ...} />`
  away, with `c` the current theme palette (family colours included).

## Not yet
- Per-user accounts and roles (one shared token for now).
- The frontend build is not in CI yet: `docs/ci_proposed.yml` adds it (the owner copies it over the workflow); the
  `package-lock.json` is created by the first `npm install` on the owner's machine and should be committed then.
- Scheduling of presets (the daily job still needs Windows Task Scheduler).
