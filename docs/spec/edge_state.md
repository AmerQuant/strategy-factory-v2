# Edges that switch on and off (design 12.4-12.5)

Code: `policy/edge_state.py`, `signals/trendiness.py`, `FoldManager.sub_folds / slice_lookback / slice_closed_before`.
Tests: `tests/test_edge_state.py`.

## Two clocks
- **Slow clock (parameters):** every fold DP (6 months) the row's threshold, exit and filters come from the shared
  selector `decide_fold` on IS only. The fast clock never changes parameters.
- **Fast clock (activation / weight):** `FoldManager.sub_folds(fold, sub_months)` splits the fold's OOS into
  sub-DPs (default monthly). At each sub-DP every eligible symbol gets a weight in [0, w_max] (0 = off) from
  data strictly before the sub-DP. OOS trades of the sub-period are those with signal date in it, scaled by the
  weight. Capacity (S8) is applied afterwards to the stitched stream, as in `run_ladder`.
- **Shadow track:** the cached trades of a symbol with the fold's settings exist whether or not it is active;
  they are the only performance evidence the fast clock uses.

## Measure first (`state_persistence`)
Pre-registered, on OOS shadow trades with the row's in-fold settings:
- `fold_to_fold`: Spearman rho across symbols of per-trade expectancy, fold k vs k+1, and the on-lift
  P(on in k+1 | on in k) - P(on in k+1).
- `window_to_next`: at every sub-DP, rho between the trailing `window_months` t-stat and the next sub-period's
  expectancy - the direct test of the fast clock.
- Verdict `persistent` iff `window_to_next` mean rho > 0 with t >= 2. Otherwise no mechanism can be accepted and
  trading every eligible symbol (`always`) stays.

## Mechanisms (`ActivationConfig.mode`, each one registry trial)
| mode | rule at a sub-DP | knobs |
|---|---|---|
| `always` | all on; identical to `run_ladder` (tested) | - |
| `two_clock` | on iff trailing `window_months` shadow t-stat >= `min_t`; < `min_trades` -> `default_on` | 12 m, 0, 8 |
| `shadow` | equity-curve rule on the last `shadow_trades` closed shadow trades, hysteresis `shadow_on/off` | 10, 0, 0 |
| `trendiness` | on iff the causal feature (efficiency / variance_ratio / autocorr, `feature_window` bars) at the last bar before the sub-DP is on the side of the IS median chosen in-fold; if no half beats all IS trades by `feature_margin` t, no gating | efficiency, 60, 0.25 |
| `soft_weight` | trailing t-stat shrunk to the row mean with k = `shrink_k` trades, w = clip(1 + t_shr - mean, 0, w_max), renormalised to mean 1 | 12 m, 20, 2 |

## Acceptance (`run_edge_state`)
A mechanism is accepted only if the persistence verdict is `persistent` AND the paired circular-block bootstrap
95 % CI of daily Sharpe(mechanism) - Sharpe(always) is entirely above zero. The table also reports exposure
(mean active share) and switches (on/off changes = turnover at sub-DPs).

## Evidence on synthetic data
- random walk: `not_persistent`, nothing accepted.
- a permanent, equal edge on every symbol: `not_persistent` (ranks do not persist) - nothing to switch.
- per-symbol MR edge in ~700-day regimes, off regime = mild momentum: `persistent` (rho ~ 0.18-0.22, t 7-8);
  `two_clock`, `shadow` and `soft_weight` accepted in all three seeds tried, `trendiness` in one of three.
- same market with the off regime a plain random walk (edge off = zero, not negative): `persistent`, but no
  mechanism beat `always` significantly - switching off a zero-edge symbol only removes noise.

## Not yet
- live DP / book integration (`forward.live.decide_book` still uses parameters only), catalogue runner and
  evidence package integration, per-row choice of mechanism in the meta-grid.
- speed: the fast clock slices per symbol with Polars; ~5,000 symbols x monthly sub-DPs needs the
  ADR-0004 work (NumPy searchsorted on per-symbol trade arrays).
