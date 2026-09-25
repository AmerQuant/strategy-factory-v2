# Cross-row risk budget in the combined policy

Code: `portfolio/combine.py` (`RiskBudget`, `cap_weights`, `apply_budget`), `evaluation/catalog_runner.py`
(`budget=`), `scripts/run_real.py` (`--max-row-weight`, `--max-family-weight`, `--target-vol`).
Tests: `tests/test_risk_budget.py`.

At every DP, after the greedy correlation-capped selection and inverse-vol weights (unchanged):
1. **caps** - per row (`max_weight`) and per edge family (`family_cap`), solved jointly by water filling: a
   violation is cut to its cap and the excess goes pro rata to rows that still have room under both caps;
   what cannot be placed stays in cash;
2. **vol target** - all weights are scaled so the ex-ante portfolio volatility (covariance of the same trailing
   window, data before the DP) equals `target_vol_ann` x capital per year, leverage clipped to [0, `lev_max`];
   without enough history the leverage is 1.

The same budget is used for the path-2 acceptance test, so rows are accepted for the portfolio that is actually
traded. The evidence package records the budget, the weights per fold (after caps, times leverage) and the
leverage per fold. No budget = the previous behaviour exactly (tested).
Tested: caps hold in every fold, the realised volatility lands near the target, and changing data after a DP
never changes earlier folds' weights.
