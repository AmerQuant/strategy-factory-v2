# Walking skeleton (P4-lite)

Row `MR-RSI2-BUY-EQ` (universe-based, pooled parameter), rungs:
- **A0**: fixed RSI(2) < 10, neutral exit (close > previous high, max 5 bars), all eligible symbols.
- **A1**: threshold chosen at each DP from grid {5,10,15,20,25} by pooled IS t-stat with 3-point neighbourhood smoothing (plateau-lite, min 30 IS trades).

Flow per fold: S1 point-in-time eligibility (membership, min split-only price, 20-day median dollar volume, min history) → IS selection (A1) → OOS trades by signal date in [oos_start, oos_end) → stitched OOS stats → registry.

Execution: signal on close t, fill at open t+1; exit signal on close j, fill at open j+1; forced exit at last close on delisting/data end. Costs 5 bps per side. Fixed notional 100k per trade (no capacity yet — P5).

Tests (all must stay green):
- future-perturbation: perturb prices/volume on/after DP_k ⇒ decisions at DPs ≤ k unchanged
- random walk ⇒ no OOS edge (3 seeds); mean-reverting data ⇒ edge found
- holdout never touched; folds contiguous; purge at DP; holdout locked by default
- engine exact fills/costs/dividends; RSI causal (property test); CRSP adjustment identity
- registry counts every evaluated config; compute-once cache
