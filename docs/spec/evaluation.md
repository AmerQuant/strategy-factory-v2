# Stage E (partial): benchmarks, capacity, statistics

Code: `src/sfactory/evaluation/benchmarks.py`, `src/sfactory/portfolio/capacity.py`, `src/sfactory/stats/core.py`,
`src/sfactory/costs/model.py`; precomputation in `src/sfactory/policy/grid.py`.

`build_grid` computes, per dev fold, the point-in-time eligible set, IS stats per grid point and OOS trades per
grid point **once**; the policy and every benchmark are assembled from it (no recomputation).

## Benchmarks (diagnostics, not trials)
| Benchmark | If the policy does not beat it |
|---|---|
| `grid_ensemble` — cell mode: every grid point at 1/len(grid); capacity mode: all grid points compete for slots | selection adds nothing |
| `frozen_first` — first-fold choice kept for all folds | re-optimisation adds nothing |
| `random_choice` — random grid point per DP, N draws (percentile) | the selector is not better than chance |
| `random_ranking` — capacity mode: same book, simultaneous signals ranked randomly | the daily ranker adds nothing |
| `rank_ic` — per-fold Spearman(IS score, OOS expectancy) across the grid | IS ranking does not predict OOS |

## Capacity (S8)
Stitched OOS candidates in entry order; slot frees on exit date; `max_positions`, `max_new_per_day`, one position
per symbol; ranker `score_asc` (signal strength, e.g. lowest RSI) or `random` (seeded). Each position is sized at
capital / max_positions. Cell mode (`max_positions=0`) keeps every signal at the cache notional.

## Costs
`CostModel`: per-symbol spread (half per side), commission, slippage in bps; `stressed(1.5)` for stress tests.
Its fingerprint is part of the trade-cache key, so cost changes never reuse stale trades.

## Statistics (per-period Sharpe; kurtosis normal = 3)
PSR, expected max Sharpe of N trials, DSR, MinTRL, iid / circular-block bootstrap. Validated by calibration tests:
PSR false-positive rate ≈ 5% under the null, E[max] within 4% of Monte Carlo for N = 10/100/1000,
PSR at n = MinTRL ≈ 0.95. Daily pnl is zero-filled on weekdays between first entry and last exit.
DSR's `n_trials` must come from the registry (policy-level trials across all rows).

## Synthetic sanity run (`scripts/demo_synthetic.py`, 30 symbols, 3 configs = 3 trials)
| data | config | OOS Sharpe | max DD | vs random choice | vs random ranking | DSR |
|---|---|---|---|---|---|---|
| mean-revert | A1 cell | 5.89 | — | 100th pct | — | 1.00 |
| mean-revert | A1 capacity 10/3 | 5.42 | 3% | 100th pct | 88th pct | 1.00 |
| random walk | A1 cell | −0.27 | — | 57th pct | — | 0.07 |
| random walk | A1 capacity 10/3 | −0.31 | 19% | 31st pct | 44th pct | 0.06 |

In cell mode the grid ensemble (6.28) still beats A1 and rank IC is insignificant → the threshold selection adds
no skill even though the edge is real. In capacity mode A1 beats the capacity ensemble (5.03), and the RSI ranker
is only at the 88th percentile of random ranking → ranking skill not proven. On random walk nothing passes.
