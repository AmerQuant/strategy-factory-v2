# CLAUDE.md — WF-native strategy platform

Source of truth: design doc v2.1 (Persian Word file held by the owner); engineering specs per module in `docs/spec/`. Decisions: `docs/adr/`. Progress/handoff: `docs/PROGRESS.md` (update at the end of every task).

## Non-negotiable invariants
1. No look-ahead anywhere. Every data-driven decision at decision point (DP) t uses only data available at t.
2. All time slicing (IS/OOS, purge, embargo, holdout) goes through `FoldManager`. No module slices time on its own.
3. Holdout data is never read outside the holdout stage. Never touch `data/**/holdout*` or disable the holdout lock.
4. Costs are always on (spread, commission, slippage, swap, dividend cash flows).
5. Signals use the fully adjusted series; execution uses the split-only series; dividends are cash flows.
6. Universe and all metadata are point-in-time.
7. Every evaluated policy configuration is recorded in the registry (no invisible trials).
8. Deterministic runs: fixed seeds, versioned data and config.

## Working rules
- Plan before editing; small diffs; tests first.
- Every selector stage must pass the future-perturbation test (perturb data after t ⇒ decisions at DPs ≤ t unchanged).
- Statistical code (DSR, PBO, SPA, FDR) must be checked against reference numeric examples.
- Signal rules must be scale-invariant on the adjusted series (backward dividend adjustment rescales past levels); level rules use split-only prices.
- Build the trade cache only from `FoldManager.dev_view(...)` data.
- Research runs use ONE persistent registry file across runs (`--registry`); a fresh registry per run hides trials from DSR.
- Real data comes from the v1 store read-only (`data/sfac_store.py`); never write into it.
- The fast clock (edge activation, `policy/edge_state.py`) never changes parameters; parameters come only from `decide_fold`.
- Intraday bars: `date` = naive Datetime bar start in the run clock; no bar may straddle midnight (`check_no_straddle`); DPs are dates; daily metrics aggregate exits per calendar day.
- When a mistake is found, add a line here.
