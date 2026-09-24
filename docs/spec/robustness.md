# Robustness, SPA, engine cross-check

Code: `evaluation/robustness.py`, `stats/multiple.spa_test`, `engine/cache.trades(delay=...)`,
`tests/test_crosscheck_spa_robustness.py`.

## Robustness suite (design 10.3) — `run_robustness(fm, cache, bars_dev, divs_dev, membership, cfg)`
| Test | Rule | Type |
|---|---|---|
| cost × 1.5 | OOS expectancy > 0 | mandatory |
| Monte Carlo drawdown | block-bootstrap p95 of max DD ≤ 35% | mandatory |
| cost × 2 | OOS expectancy > 0 | warning |
| execution delay (+1 bar) | keeps ≥ 50% of expectancy | warning |
| price noise (σ = 0.2% per bar, OHLC re-ordered) | keeps ≥ 50% of OOS Sharpe | warning |
| regime (causal market-up / down labels at signal date) | no regime with expectancy < −|base| | warning |
On synthetic data a mean-reverting IBS row passes; the same row on random walk fails.

## Hansen SPA (consistent version) — `spa_test(candidates T×K, benchmark T)`
Stationary bootstrap (mean block 20), recentring μ̂ = d̄·1{√n·d̄/ω ≥ −√(2 log log n)}. Tests: 15 pure-noise candidates → p > 0.05; one candidate with +0.25/day → p < 0.05 and identified as best.

## Engine cross-check against vectorbt (ADR-0002)
Same RSI(2) rule, next-open fills, 5 bps fees per side, fixed 100k notional: entry bars and per-trade PnL (ex-dividends, since vectorbt has no dividend cash flows) match to 1e-6. vectorbt is an optional dependency group (`uv sync --group crosscheck`, pins `plotly<6`); the test skips when it is not installed. To run it in CI add `uv sync --group crosscheck` to `.github/workflows/ci.yml`.
