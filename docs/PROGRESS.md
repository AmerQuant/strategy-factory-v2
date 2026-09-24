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
- tests: 85 passing (vectorbt cross-check runs when the `crosscheck` group is installed) (see docs/spec/skeleton.md, docs/spec/evaluation.md)
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

## Next
1. Real-data adapters (owner's data: bars, dividends, historical membership) + data audit (P1) — needs a sample of the owner's files
2. Add `uv sync --group crosscheck` to CI (needs a workflow edit by the owner)
3. Broker bridge (P11): MT5 order/fill adapter, netting across rows, daily reconciliation, paper mode — needs the owner's machine
4. FX swap/rollover costs in CostModel (needs the broker's swap table)
5. Finding to revisit on real data: every MR row fails the 1-bar delay warning on synthetic data (short-horizon MR is delay-sensitive) — check on real bars before going live
