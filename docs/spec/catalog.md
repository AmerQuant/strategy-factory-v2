# Catalogue runner, row screening, combined policy

Code: `evaluation/catalog_runner.py`, `portfolio/combine.py`, `stats/core.sharpe_diff_ci`. Demo: `scripts/demo_catalog.py`.

1. **Run** every pre-registered (row, rung) configuration; each is a registry trial.
2. **Statistics per configuration** on zero-filled daily dev-OOS pnl: PSR vs 0, DSR with N = registry trial count and the cross-trial Sharpe variance *within the row's family* (MR / TF).
3. **BH** (q = 0.10) on p = 1 − PSR across all configurations.
4. **Two-path gate** (design 12.1): standalone = BH pass and DSR ≥ 0.95; portfolio = positive expectancy, max DD ≤ 35%, and a paired block-bootstrap CI of Sharpe(combined + row) − Sharpe(combined) entirely above zero.
5. **Combined policy (WF-native)**: at every DP, using only daily pnl before the DP (lookback 504 days, ≥ 120 days of history, otherwise equal weight): greedy by trailing Sharpe, skip rows with trailing correlation > 0.6 to any selected row, inverse-vol weights summing to 1.
6. Report: effective number of bets (eigenvalue formula), combined Sharpe vs equal weight of all rows.

## Synthetic results (30 symbols, capacity 10 / 3, rung A1)
| data | BH pass | accepted | combined Sharpe | effective N | all 18 rows equal-weight |
|---|---|---|---|---|---|
| mean-reverting | 10 (all MR) | 10 MR, standalone | 8.26 | 6.3 | 5.57 |
| trending | 8 (all TF) | 7 TF, standalone | 2.59 | 6.4 | 1.06 |
| random walk | 0 | 0 | — | — | −1.40 |

## Added: robustness gate, SPA, family ensembles, meta-grid
- **Robustness gate**: with `divs_dev` given, every accepted row runs the robustness suite (docs/spec/robustness.md); a mandatory failure removes it (`path = …-rejected_by_robustness`); warnings go into the evidence package.
- **SPA** (reported): all configurations vs cash, and the combined policy vs the all-rows equal weight.
- **Family ensembles** (design 13.3, reported): equal-weight of every family × direction vs its best member. On synthetic data the ensemble beats the best single member in every profitable group (mean-reverting: MR-BUY 6.85 vs 5.53, MR-SELL 6.72 vs 5.05; trending: TF-BUY 2.72 vs 1.84, TF-SELL 2.09 vs 1.62) — picking one member is only allowed when it beats the ensemble OOS.
- **Meta-grid** (`evaluation/meta_grid.py`): pre-registered IS length × window type × capacity; every setting's rows are registry trials; report = combined Sharpe per setting, share of settings beating the benchmark, PBO across settings. Never used to pick the maximum.
