# Running everything in Docker (Windows + Docker Desktop)

One image (`Dockerfile`) holds the Python platform (uv, `--group web --group dev`) and the built dashboard.
`docker-compose.yml` runs two long-lived services from it and a few one-off tools:

| service | what | command |
|---|---|---|
| `web` | API + dashboard on http://127.0.0.1:8765 (token sign-in) | started by `up` |
| `scheduler` | the scheduler service (jobs on New York time, kill switch, Telegram) | started by `up` |
| `tests` | full pytest inside the image | `docker compose run --rm tests` |
| `cli` | a shell / any script | `docker compose run --rm cli python scripts/run_real.py ...` |
| `web-typecheck` | TypeScript type check of the dashboard | `docker compose run --rm web-typecheck` |

## First start
1. Install Docker Desktop (WSL 2 backend); give it enough memory (Settings -> Resources) for research runs.
2. `git clone` the repository, then `copy .env.example .env` and edit `.env`: `SF_STORE_DIR` (the v1 store, mounted
   read-only), `SF_CONFIG_DIR` / `SF_RUNS_DIR` / `SF_LIVE_DIR` (v2's own folders), `SF_WEB_TOKEN` (>= 16 characters).
3. `docker compose build` then `docker compose run --rm tests` (expected: all pass, 1 skipped: vectorbt).
4. `docker compose up -d`, open http://127.0.0.1:8765 and sign in with `SF_WEB_TOKEN`.
5. `docker compose logs -f web scheduler` to follow; `docker compose down` to stop.

On first start the container writes `platform.json` in the config folder with the container paths
(`/store`, `/runs`, `/runs/registry.duckdb`, `/live`, `/app/scripts`). In presets and settings always use these
container paths, not Windows paths.

## Paths inside the container
| host (`.env`) | container |
|---|---|
| `SF_STORE_DIR` | `/store` (read-only) |
| `SF_CONFIG_DIR` | `/config` (settings, presets, schedules, jobs, kill switch, events) |
| `SF_RUNS_DIR` | `/runs` (research runs, registry) |
| `SF_LIVE_DIR` | `/live` (paper / live state files) |

## What stays on Windows
- **MetaTrader 5**: the `MetaTrader5` Python package works only on Windows, next to a running terminal. The MT5
  export (`export_mt5_specs.py`) and live MT5 jobs (`--broker mt5`) run on the host with `uv run ...`; research,
  paper trading (`--broker sim`), the dashboard and the scheduler run in Docker.
- **v1 data refresh**: the store is mounted read-only (v2 never writes it). Run `refresh_v1_data.py` / v1's `sfac`
  on the host until a read-write mount for it is decided.
- Only one scheduler per config folder: the container removes a stale heartbeat on start
  (`SF_SCHEDULER_TAKEOVER=1`), so never run a second scheduler on the host with the same config.

## Notes
- The dashboard is built with `vite build` (no type check) so a type error cannot block the image; run
  `web-typecheck` to see type errors. The first build creates `web/package-lock.json` inside the image only -
  commit a lock file from the host (`cd web && npm install`) for reproducible builds.
- No `uv.lock` is committed yet: the image resolves the newest versions allowed by `pyproject.toml`.
- Port 8765 is bound to 127.0.0.1 only. For access from other machines change the port mapping and put HTTPS in
  front (docs/spec/web.md).
