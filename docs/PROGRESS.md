# PROGRESS

## Status
- Phase: P0 done → walking skeleton (P4-lite) + stage-E benchmarks done on synthetic data
- Design: v2.1 Persian Word doc (owner); specs in docs/spec/
- Accepted ADRs: 0001 uv + Polars; 0002 custom NumPy/Numba engine, vectorbt only as cross-check; 0003 Parquet + DuckDB
- Deferred: 0004 parallelism (P7)

## Done (skeleton)
- data: contracts, CRSP-style dividend adjustment, synthetic market generator, point-in-time eligibility
- timeline: FoldManager (DPs, rolling/anchored IS, purge, embargo, holdout lock, dev view)
- engine: Numba cell engine (next-open fills, costs, dividends, forced exit) + compute-once trade cache
- signals: causal Wilder RSI
- policy: RSI row at A0/A1; registry (DuckDB) with trials + fold decisions; basic metrics
- tests: 18 passing (see docs/spec/skeleton.md, docs/spec/evaluation.md)
- evaluation: FoldGrid precomputation; benchmarks grid-ensemble, frozen-first, random-choice; rank IC
- engine: optional Parquet-backed trade cache (keyed by rule, params, data version, cost model)
- scripts/demo_synthetic.py: end-to-end A0/A1 + benchmarks on synthetic data

## Next
1. Real-data adapters (owner's data: bars, dividends, historical membership) + data audit (P1) — needs a sample of the owner's files
2. Cost model per symbol; vectorbt cross-check test (P2)
3. Capacity-constrained portfolio simulator + daily signal ranking + random-ranking benchmark (P5)
4. Exit library and in-fold exit selection with joint-plateau check (S4)
5. Statistics: DSR, MinTRL, bootstrap; PBO/CSCV over the meta-grid (P6)
