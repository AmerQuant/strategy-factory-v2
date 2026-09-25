# PROGRESS

## Status
- Phase: P0 done → skeleton + benchmarks + capacity + statistics + ablation ladder + 18-row catalogue + screening + combined policy + holdout + forward monitoring done on synthetic data
- Design: v2.1 Persian Word doc (owner); specs in docs/spec/
- Accepted ADRs: 0001 uv + Polars; 0002 custom NumPy/Numba engine, vectorbt only as cross-check; 0003 Parquet + DuckDB
- Accepted: 0004 parallelism (algorithmic slicing fix + stdlib process pool for the trade cache)
- Full-repository check (2026-09-25): every file downloaded from main into a clean folder; ruff (src, tests, scripts) clean; pytest 193 passed + 1 skipped (vectorbt cross-check) before the additions below; last full run on the clean copy: 229 tests, 228 passed + 1 skipped

## Done (skeleton)
- data: contracts, CRSP-style dividend adjustment, synthetic market generator, point-in-time eligibility
- timeline: FoldManager (DPs, rolling/anchored IS, purge, embargo, holdout lock, dev view)
- engine: Numba cell engine (next-open fills, costs, dividends, forced exit) + compute-once trade cache
- signals: causal Wilder RSI
- policy: RSI row at A0/A1; registry (DuckDB) with trials + fold decisions; basic metrics
- tests: 229 (228 run without the `crosscheck` group; web API and scheduler service tests need the `web` group) (see docs/spec/skeleton.md, docs/spec/evaluation.md)
- evaluation: FoldGrid precomputation; benchmarks grid-ensemble, frozen-first, random-choice; rank IC
- engine: optional Parquet-backed trade cache (keyed by rule, params, data version, cost model)
- costs: per-symbol CostModel (spread/commission/slippage, stress factor) in the cache key
- portfolio: capacity simulator (max positions, max new per day, one per symbol, score or random ranker, sizing)
- stats: PSR, expected max Sharpe, DSR, MinTRL, iid/block bootstrap (calibration-tested)
- benchmarks: + random ranking (capacity mode) + Sharpe report
- scripts/demo_synthetic.py: end-to-end A0/A1/A1-capacity + benchmarks + stats on synthetic data
- ladder: A0→A1→A3→A4 in-fold (entry plateau, exit library + joint plateau, filter with year/random-removal tests), structural market filter, generic exit kernel with ATR target/stop
- multiple testing: Benjamini-Hochberg, PBO/CSCV; ablation report with bootstrap acceptance (docs/spec/ladder.md)
- rows: 9 entry methods (5 MR, 4 TF) × buy/sell = 18-row catalogue; TF exit library with trailing stop; mirrored filters; trending synthetic data (docs/spec/rows.md)
- catalogue runner: registry-counted trials → PSR/DSR (within-family variance) → BH → two-path gate → WF-native combined policy (corr cap, inverse vol), effective N, family ensemble helper (docs/spec/catalog.md)
- holdout: frozen-policy hash, dev-only bootstrap criteria, register→open burn rule in the registry, locked-fold run, all-rows benchmark; JSON evidence package + Persian report prompt (docs/spec/holdout.md)
- forward: shared per-DP selector (decide_fold); live book, book diff, daily orders; MC bands, CUSUM, implementation shortfall, pre-registered promote/continue/stop rules (docs/spec/forward.md)
- robustness suite (cost 1.5x/2x, delay, noise, MC drawdown, causal regimes), Hansen SPA, vectorbt engine cross-check (docs/spec/robustness.md)
- catalogue runner now applies the robustness gate, reports SPA and family ensembles; meta-grid runner; all in the evidence package (docs/spec/catalog.md)
- symbol-based rows (S7 top-N by IS t-stat, random-N benchmark, FX row catalogue, static membership) and tradable ensemble rows with a row dispatcher (docs/spec/symbol_rows.md)
- Persian RTL HTML report renderer, zero dependencies, inline SVG charts (docs/spec/report.md)
- real-data path: v1 store adapter, data audit, top-liquidity point-in-time universe, swap/financing in CostModel + Moneta share-CFD proxy, folds from data span, `scripts/run_real.py`, Alpaca dividends fetcher (docs/spec/real_data.md)
- package 1 (edge on/off, design 12.4-12.5): state-persistence test, two clocks (FoldManager.sub_folds), shadow equity-curve rule, causal trendiness features with in-fold calibration, soft weights; run_edge_state ablation vs `always` with persistence + paired bootstrap acceptance (docs/spec/edge_state.md)
- package 2 (intraday 1H/4H): Datetime bar convention (bar start, no midnight straddle), 4H resampling with broker clock shift + alignment variants, intraday dividends/swap/universe/capacity/metrics, hourly synthetic market, run_real --timeframe/--resample/--clock-shift (docs/spec/intraday.md)
- package 3 (diverse families, design 13.2): VOL (vol_spike, squeeze), XS (cross-sectional momentum via the capacity ranker), CAL (turn of month), EV (post ex-dividend, index addition); SeriesCtx, BRK/HOLD exit libraries, separate catalogue version, run_real --diverse (docs/spec/families.md)
- package 4 (sizing): per-trade vol targeting with a fast shock leg, gross exposure cap, daily vol-target and drawdown-brake overlays, sizing ablation with the paired-bootstrap Sharpe gate (docs/spec/sizing.md)
- package 5 (speed, ADR-0004): stacked per-setting time slicing and memoised universe (about 3-5x on 200 symbols, single process), parallel trade-cache precompute with a spawn process pool, run_real --workers, scripts/bench_speed.py
- package 6 (broker bridge): order/fill contract, netting across rows with internal crossing, row books, reconciliation, simulated broker, research-parity paper planner (placeholder-bar method, exact trade parity tested), MT5 adapter for netting and hedging accounts with dry run (docs/spec/broker.md)
- roadmap 7 (v1 cost profiles -> v2): v1-faithful profile resolution, unit conversion (spread / commission / slippage / swap), nothing silently defaulted, broker symbol map for MT5 (docs/spec/costs_v1.md)
- daily paper / live job: persistent JSON state, open / close phases (MT5 at the open, plan after the close), DP book refresh, positions keep the setting they were opened with, `scripts/run_daily.py`; a day without new data is a no-op (docs/spec/daily.md)
- run_real --analyze-accepted: edge on/off + sizing ablations per accepted row in the evidence (evaluation/row_analysis.py)
- fast clock in the live book: policy entries with an accepted activation, sub-DP weights from the research decide_sub (live == research tested)
- cross-row risk budget in the combined policy: joint row / family caps (water filling) + ex-ante portfolio vol target, used in the path-2 test too; run_real --max-row-weight / --max-family-weight / --target-vol (docs/spec/risk_budget.md)
- paper cash flows: dividends on ex-dates and modelled swap in the row books (research parity per share tested); MT5 real commission / fee from the deal history, deal_costs and open_swap reports
- robustness variant caches (cost stress, delay, noise) precomputed in parallel with --workers
- intraday: session-close exit (`flat_eod`), DST-aware broker clock (`clock_shift="ny"`), intraday paper planning with research parity (docs/spec/intraday.md)
- web admin + dashboard: FastAPI backend (validated config documents, runs / registry / live readers, job runner) + React/TS dashboard with light and dark mode; optional token authentication with sign-in screen, refused network bind without a token (docs/spec/web.md)
- survivorship check of the store: delisting profile, heuristic verdict, direct membership leaks; in every run_real evidence + caveat; `scripts/check_survivorship.py` (docs/spec/survivorship.md)
- daily sizing overlays in the live book: policy entries with `overlay` (vol target, drawdown brake) scale new entries by the research overlay factor one day ahead; editor form in the admin (docs/spec/daily.md)
- intraday paper / live job: `bar_step` + `scripts/run_intraday.py` (catch-up, restartable per bar, live gaps refused); paper == research trades over 217 hourly bars with a restart before every bar (docs/spec/daily.md)
- scheduler service (option C, stdlib + tzdata): New York market-time triggers (daily, after every bar), preset chains that stop at the first failure, missed / late / still-running rules, heartbeat, `python -m sfactory.scheduler`; Schedules in the admin, scheduler panel on the Jobs page (docs/spec/scheduling.md)
- kill switch: halt_new / flatten read by every job before every plan, automatic limits (drawdown, daily loss, reconciliation mismatches), event log (jobs, scheduler, dashboard), banner + control panel in the dashboard (docs/spec/killswitch.md)
- Telegram notifications for the events (`forward/notify.py`): the scheduler sends every new event once to one chat (level filter, HTTP proxy, retry from the same place after an outage, flood cap, token only in `SF_TELEGRAM_TOKEN`), settings and "Send test message" on the Platform page; tested against a local fake Telegram server (docs/spec/killswitch.md)

## Code roadmap (no owner machine needed; one or two packages per chat)
1. ~~Edge on/off mechanisms~~ (done; integration into catalogue / evidence / live book still open, see spec)
2. ~~Hourly timeframe in engine, folds and exits~~ (done; session exits, DST-aware clock, live intraday orders open)
3. ~~New edge families~~ (done; gap rows and announcement-date events open, event rows wait for dividends / membership data)
4. ~~Advanced sizing~~ (done; cross-row risk budget in the combined policy and run_real integration open)
5. ~~Parallelism and speed~~ (done; measure with scripts/bench_speed.py on the owner's machine)
6. ~~MT5 bridge~~ (done as far as possible without a terminal; daily paper job script, MT5 deal costs, paper dividends open)
7. ~~Moneta cost converter v1 → v2 CSV~~ (done; run it on the owner's machine)

## Remaining code (no owner machine; in this order)
1. ~~Daily paper / live job with state file~~ (done)
2. ~~run_edge_state and run_sizing per accepted row inside run_real + evidence~~ (done; shown in the web dashboard)
3. ~~Fast clock (edge on/off) in the live book~~ (done)
4. ~~Cross-row risk budget in the combined policy~~ (done)
5. ~~Paper dividends; MT5 commission / swap from deal history~~ (done)
6. ~~Precompute for the robustness caches~~ (done)
7. ~~Intraday: session exits, DST-aware clock, intraday planner~~ (done)
8. ~~Web admin + dashboard~~ (done)
9. ~~Survivorship check of the store~~ (done)
10. ~~Web authentication~~ (done: token + session cookie)
11. ~~Daily sizing overlays in the live book~~ (done, with the admin form)
12. ~~Intraday per-bar job~~ (done)
13. ~~Scheduling~~ (done: option C)
14. ~~Kill switch + event log~~ (done)
15. ~~A notification channel for the events~~ (done: Telegram, owner's choice)
16. Next: Moneta FX / index / metals path (MT5 symbol specification export, cost model from it, `--asset-class`, 24x5 calendar with the 17:00 New York rollover, FX / IX / MT catalogue version); a job type for the v1 data refresh (needs its command)
17. Optional: CPCV and HRP; ML meta-labeling rows; market impact model; gap rows and announcement-date events; mark-to-market kill-switch limits

## Next on the owner's machine
1. Survivorship check first: `scripts/check_survivorship.py --store <store> [--membership ...]` (docs/spec/survivorship.md)
2. First real run: `scripts/run_real.py` on the v1 store (bars ready); then membership + dividends files when located (or `fetch_alpaca_dividends.py`)
3. CI: copy `docs/ci_proposed.yml` over `.github/workflows/ci.yml` (web tests, frontend build, non-blocking vectorbt cross-check); the assistant's token cannot write workflow files
4. Scheduler: `uv sync --group web`, then `python -m sfactory.scheduler --config <dir>` - as a Windows service for paper; for live MT5 start it in your own session at log on (docs/spec/scheduling.md)
5. Kill switch limits: set capital and limits on the Paper & live page before the first live order
6. Telegram: bot token in `SF_TELEGRAM_TOKEN` on the scheduler machine, chat id (+ HTTP proxy if needed) on the Platform page, then "Send test message" (docs/spec/killswitch.md)
7. Broker bridge live test: `MT5Broker(dry_run=True).connect(...)` on the Moneta demo account, check `positions()` and the dry-run requests, then one small real order
8. Finding to revisit on real data: every MR row fails the 1-bar delay warning on synthetic data (short-horizon MR is delay-sensitive) — check on real bars before going live
9. Run `run_edge_state` per accepted row on real data: the persistence verdict decides whether any mechanism is used
10. Hourly run: `run_real.py --timeframe 1H` (and `--resample 4h --clock-shift ny` for the broker-aligned 4H set); paper-trade it with `run_intraday.py`
11. Diverse families on real data: `run_real.py --diverse` (event rows switch on with `--dividends` / `--membership`)
12. `run_sizing` per accepted row on real data: vol sizing / overlays are kept only if they pass the Sharpe gate
13. Speed: `scripts/bench_speed.py --symbols 1000 --workers 1,4,8` (and `--store`), then `run_real.py --workers 0`
14. Costs: `scripts/convert_moneta_costs.py` (see docs/spec/costs_v1.md), then `run_real.py --costs costs_moneta.csv`
15. Paper trading: `scripts/run_daily.py --init ...` with the holdout policy, then one run per trading day (docs/spec/daily.md)
16. Dashboard: `uv sync --group web`, `cd web && npm install && npm run build`, `uv run python -m sfactory.web --config <dir>` (add `SF_WEB_TOKEN` + `--host 0.0.0.0` for network access); commit web/package-lock.json
