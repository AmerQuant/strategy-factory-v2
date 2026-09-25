# PROGRESS

## Status
- Phase: P0 done → skeleton + benchmarks + capacity + statistics + ablation ladder + 18-row catalogue + screening + combined policy + holdout + forward monitoring done on synthetic data
- Design: v2.1 Persian Word doc (owner); specs in docs/spec/
- Accepted ADRs: 0001 uv + Polars; 0002 custom NumPy/Numba engine, vectorbt only as cross-check; 0003 Parquet + DuckDB
- Deferred: 0004 parallelism (P7)

## Done (skeleton)
- data: contracts, CRSP-style dividend adjustment, synthetic market generator, point-in-time eligibility
- timeline: FoldManager (DPs, rolling/anchored IS, purge, embargo, holdout lock, dev view)
- engine: Numba cell engine (next-open fills, costs, dividends, forced exit) + compute-once trade cache
- signals: causal Wilder RSI
- policy: RSI row at A0/A1; registry (DuckDB) with trials + fold decisions; basic metrics
- tests: 142 passing (vectorbt cross-check runs when the `crosscheck` group is installed) (see docs/spec/skeleton.md, docs/spec/evaluation.md)
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

## Code roadmap (no owner machine needed; one or two packages per chat)
1. ~~Edge on/off mechanisms~~ (done; integration into catalogue / evidence / live book still open, see spec)
2. ~~Hourly timeframe in engine, folds and exits~~ (done; session exits, DST-aware clock, live intraday orders open)
3. ~~New edge families~~ (done; gap rows and announcement-date events open, event rows wait for dividends / membership data)
4. Advanced sizing: vol targeting, per-row and portfolio risk caps
5. Parallelism and speed for ~5,000 symbols (ADR-0004)
6. MT5 bridge as far as possible: order/fill contract, netting across rows, daily reconciliation, paper mode with a simulated broker
7. Moneta cost converter v1 → v2 CSV

## Next on the owner's machine
1. First real run: `scripts/run_real.py` on the v1 store (bars ready); then membership + dividends files when located (or `fetch_alpaca_dividends.py`)
2. Add `uv sync --group crosscheck` to CI (needs a workflow edit by the owner)
3. Broker bridge (P11) live connection test — needs the owner's machine
4. Finding to revisit on real data: every MR row fails the 1-bar delay warning on synthetic data (short-horizon MR is delay-sensitive) — check on real bars before going live
5. Run `run_edge_state` per accepted row on real data: the persistence verdict decides whether any mechanism is used
6. Hourly run: `run_real.py --timeframe 1H` (and `--resample 4h --clock-shift 7h` for the broker-aligned 4H set)
7. Diverse families on real data: `run_real.py --diverse` (event rows switch on with `--dividends` / `--membership`)
