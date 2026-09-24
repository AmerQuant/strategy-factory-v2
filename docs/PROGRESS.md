# PROGRESS

## Status
- Phase: P0 done → walking skeleton (P4-lite) done on synthetic data
- Design: v2.1 Persian Word doc (owner); specs in docs/spec/
- Accepted ADRs: 0001 uv + Polars; 0002 custom NumPy/Numba engine, vectorbt only as cross-check; 0003 Parquet + DuckDB
- Deferred: 0004 parallelism (P7)

## Done (skeleton)
- data: contracts, CRSP-style dividend adjustment, synthetic market generator, point-in-time eligibility
- timeline: FoldManager (DPs, rolling/anchored IS, purge, embargo, holdout lock, dev view)
- engine: Numba cell engine (next-open fills, costs, dividends, forced exit) + compute-once trade cache
- signals: causal Wilder RSI
- policy: RSI row at A0/A1; registry (DuckDB) with trials + fold decisions; basic metrics
- tests: 14 passing (see docs/spec/skeleton.md)

## Next
1. Real-data adapters (owner's data: bars, dividends, historical membership) + data audit (P1)
2. Cost model per symbol; vectorbt cross-check test (P2)
3. Benchmarks (all-cells, random-N, frozen first book, random ranking) + rank-IC diagnostics
4. Parquet trade cache on disk; registry file
