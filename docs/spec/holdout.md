# Stage H: pre-registered one-shot holdout + evidence package

Code: `evaluation/holdout.py`, `registry/repo.py` (holdout_log), `evaluation/evidence.py`,
prompt `docs/templates/report_prompt_fa.md`. Demo: `scripts/demo_holdout.py`.

1. **Freeze** the accepted row configurations + combination parameters → policy hash.
2. **Criteria from dev only**: circular block bootstrap (block 20, 2000 draws) of the combined dev-OOS daily pnl, resampled to the holdout length → Sharpe ≥ p5, max DD ≤ p95, and Sharpe > equal weight of all catalogue rows.
3. **Register → open**: `register_holdout(data_version, hash, criteria)` must precede `open_holdout(...)`; opening marks the data version as burned. A second opening, a re-registration after opening, or a changed policy hash raises `HoldoutError` — only forward testing can evaluate a modified policy.
4. **Run**: all catalogue rows are re-run over dev + unlocked holdout folds on the full data (the only place the lock is lifted); combination weights stay WF-native, so holdout DPs see the dev history exactly as they would live. Per-row holdout Sharpes are descriptive only.
5. **Evidence package**: a single JSON with registry trial count, every row (taxonomy, p, BH, DSR, path, fold decisions of accepted rows), combined dev stats, holdout criteria/checks/result and policy spec. The Persian report is written from this JSON with the standard prompt; no number may come from anywhere else.

## Synthetic results (30 symbols)
| data | status | holdout Sharpe | max DD | all-rows benchmark |
|---|---|---|---|---|
| mean-reverting | pass | 9.31 | 0.8% | 5.92 |
| trending | pass | 3.44 | 2.6% | 1.12 |
| random walk | nothing to test (no row accepted) | — | — | — |
