# Ablation ladder, exits, filters, multiple testing

Code: `policy/ladder.py`, `engine/generic.py`, `signals/specs.py`, `data/regime.py`, `stats/multiple.py`,
`evaluation/ablation.py`. Demo: `scripts/demo_ablation.py`.

## In-fold stages (IS only, every DP)
| Rung | Stage | Rule |
|---|---|---|
| A0 | fixed | RSI(2) < 10, neutral exit, no optional filter |
| A1 | entry | threshold by plateau-lite over {5..25} |
| A3 | exit | best of the exit library; kept only if IS t-stat > neutral + 0.25 **and** joint plateau (neighbour thresholds keep ≥ 70% of its t-stat) |
| A4 | filter | ≤ 1 optional filter; must raise IS t-stat, keep ≥ 60% of trades, improve expectancy in > 50% of IS years, and beat ≥ 90% of random removals of the same size |
Capacity / daily ranking (S8) applies when `max_positions > 0`. Structural filters are fixed and never selected.

## Libraries
- Exits (MR): prev-high (neutral, max 5), time 3 / 5, RSI > 50 / 70 (max 10), prev-high + 1 ATR target, prev-high + 3 ATR stop (max 10). Target/stop are close-based; ATR is the adjusted-series ATR divided by the adjustment factor at t (causal, execution units).
- Optional filters: close above SMA(200) (adjusted series), ATR% below its 252-day 80th percentile.
- Structural: `market_up` = equal-weight market proxy above its SMA(200) (`data/regime.py`), set on the cache with a regime id that is part of the cache key.

## Ablation acceptance
A rung replaces the active one only if the block-bootstrap (block 20) 95% CI of the daily-pnl difference is entirely above zero. DSR uses the number of rung configurations as trials; PBO/CSCV (10 blocks) runs over all rungs' aligned daily OOS pnl.

## Synthetic results (30 symbols, capacity 10 / 3 per day)
| data | A0 | A1 | A3 | A4 | active | PBO |
|---|---|---|---|---|---|---|
| mean-revert | 3.78 | 5.42 ✓ | 7.80 ✓ | 7.80 ✗ (no filter passed) | A3 | 0.00 |
| random walk | −0.44 | −0.31 ✗ | −0.04 ✗ | −0.04 ✗ | A0 | 0.36 |
Structural filter on synthetic data (no real regimes) lowers A0 from 3.78 to 3.19, as expected; on random walk nothing is accepted.

## Multiple testing
`benjamini_hochberg(pvals, q)` for accepting several rows at once (p-value per row = 1 − PSR); `pbo_cscv(T×N, S)`.
Calibration tests: BH textbook cases; PBO ≈ 0.5 on pure noise, < 0.1 with one genuinely better configuration.
