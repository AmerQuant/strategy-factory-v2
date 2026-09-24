# PROGRESS

## Status
- Phase: P0 (specs & architecture)
- Design: v2.1 Persian Word doc (owner); module specs go to docs/spec/
- Accepted ADRs: 0001 uv + Polars; 0002 custom NumPy/Numba engine, vectorbt only as cross-check; 0003 Parquet + DuckDB
- Deferred: 0004 parallelism (P7)

## Next
1. Data contracts (OHLCV, universe, dividends, trades cache, registry, fold, book)
2. Walking skeleton: FoldManager + minimal cell engine + RSI2-Buy row at A0 on synthetic data, with future-perturbation and random-walk tests
