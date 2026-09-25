# ADR-0004: Parallelism and speed for ~5,000 symbols

Status: **Accepted** (package 5). Owner approved the option set below.

## Context
A catalogue run evaluates every row at every DP over the whole universe. Profiling 200 symbols x an A3 row
(2026-09, synthetic daily bars) showed the time split as: per-symbol Polars time slicing 45 %, trade computation
(Numba kernels + frame building) 24 %, point-in-time universe 11 %, the rest selection logic.

## Options weighed
| option | for | against |
|---|---|---|
| `concurrent.futures.ProcessPoolExecutor` | stdlib, no dependency, Windows-safe with spawn | each worker receives its symbol's arrays (pickling) |
| joblib | memmap of NumPy arrays | new dependency for little gain at this data size |
| Numba `prange` in the kernel | no process overhead | the per-symbol kernel is a sequential position state machine: bars cannot be split; parallelising across symbols inside Numba duplicates the process pool |
| Ray / Dask | multi-machine | heavy, awkward on Windows, unnecessary on one machine |

## Decision
1. **Algorithmic first** (single process): a fold stacks all eligible symbols' trades per setting once and slices
   time with one filter (`_FoldView.stacked`); per-symbol IS frames for S7 come from one partition; the eligible
   universe is memoised per DP on the cache (`cached_universe`). Result: 3.4 s -> 0.7 s for a warm A3 row and
   6.4 s -> 2.2 s cold on 200 symbols; decisions and trades unchanged (whole suite passes).
2. **Process pool for the trade cache** (`engine/parallel.py`): enumerate every (entry, exit, filters, delay) the
   rows can request (`row_specs`), compute per symbol in a spawn-context `ProcessPoolExecutor`, merge under the
   exact `TradeCache.key_for` keys, write Parquet when disk-backed. Research code runs unchanged on a warm cache.
3. `prange` is **not** used: the kernel is sequential per symbol by construction (open position, exits, trailing
   stop). It stays single-threaded and the pool parallelises across symbols.

## Guarantees (tested, tests/test_parallel.py)
- 1 worker, N workers (spawn) and no precompute give identical frames, decisions and OOS trades.
- After a precompute the research run has zero cache misses; a second precompute adds nothing.
- A disk-backed precompute makes the next run start warm with zero computation.

## Open
- The speed-up depends on the owner's machine: `scripts/bench_speed.py --symbols 1000 --workers 1,4,8` (or `--store`)
  measures it; the CI container has one core.
- Robustness variants (cost stress, noise) build their own caches and are not precomputed yet.
