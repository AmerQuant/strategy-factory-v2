# Stage E (partial): benchmarks and selection-skill diagnostics

Code: `src/sfactory/evaluation/benchmarks.py`, precomputation in `src/sfactory/policy/grid.py`.

`build_grid` computes, per dev fold, the point-in-time eligible set, IS stats per grid point and OOS trades per grid point **once**; the policy and every benchmark are assembled from it (no recomputation).

| Benchmark | Meaning if the policy does not beat it |
|---|---|
| `grid_ensemble` — every grid point, 1/len(grid) notional | selection adds nothing over trading all variants |
| `frozen_first` — first-fold choice kept for all folds | re-optimisation adds nothing |
| `random_choice` — random grid point per DP, N draws | the selector is not better than chance (reported as percentile) |
| `rank_ic` — per-fold Spearman(IS score, OOS expectancy) across the grid | IS ranking does not predict OOS ranking |

Benchmarks are diagnostics, not candidate policies, so they are not registered as trials.
Random ranking among simultaneous daily signals needs the capacity simulator (P5).

Synthetic sanity run (`scripts/demo_synthetic.py`, 30 symbols):
- mean-reverting data: A1 Sharpe 5.99 vs random-choice median 5.14 (99.5th pct), but the grid ensemble is 6.33 and rank IC is not significant (t 0.58) → the edge is real, the *threshold selection* adds no skill. This is exactly the kind of conclusion stage E must be able to reach.
- random walk: every variant ≈ 0 or negative after costs; policy at the 55th percentile of random choice.

Note: `max_dd` is relative to 100k capital while every trade uses 100k notional and trades overlap, so drawdowns > 100% are expected until capacity and sizing exist (P5/P8).
