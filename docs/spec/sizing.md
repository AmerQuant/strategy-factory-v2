# Sizing and risk overlays - package 4

Code: `portfolio/sizing.py`, `evaluation/sizing_ablation.py`; `LadderConfig.sizing / target_vol / vol_window /
vol_fast / w_max / max_gross` applied in `run_ladder` and `run_activation` after capacity. Tests: `tests/test_sizing.py`.

## Rules (all causal)
| level | rule | decision data |
|---|---|---|
| trade | `vol`: weight = clip(target_vol / sigma, 0, w_max); sigma = max(vol over 20 bars, vol over 5 bars) of adjusted log returns ending at the signal bar | bars up to the signal close |
| trade | `max_gross`: gross open notional <= max_gross x capital; a trade that does not fit is scaled to the room left, or skipped | positions open at the entry bar |
| daily | `vol_target_daily`: leverage_t = clip(target / sd(pnl over the 63 days before t), 0, 2) | pnl before t |
| daily | `drawdown_brake`: exposure 0.5 while the drawdown at the previous close > 15 %, back to 1 below 7.5 % | braked equity before t |

Weights scale every pnl column and the share count, so costs and swap scale with notional. `sizing="fixed"` and
`max_gross=0` (the defaults) leave every existing result unchanged; a sized row gets a `-VOL` / `-CAP<g>` suffix.

## Gate
`run_sizing` runs fixed, vol, cap, vol+cap (registry trials through `run_ladder`) and the two daily overlays on the
fixed row (recorded as `<row>|<overlay>` trials). A variant is **accepted only if the paired block-bootstrap CI of
its daily Sharpe minus the fixed Sharpe is entirely above zero**. `risk_better` (lower max drawdown and lower 5 %
CVaR without a significantly worse Sharpe) is reported for the analyst but never replaces the gate.

## Findings on synthetic data (Sharpe / max drawdown; accepted: none)
| market / row | fixed | vol | cap 1.0 | vol+cap | +vol target | +dd brake |
|---|---|---|---|---|---|---|
| MR, 20 symbols, cell mode | 4.75 / 0.37 | 4.48 / 0.36 | 1.58 / 0.36 | 2.39 / 0.21 | 4.00 / 0.09 | 4.68 / 0.29 |
| MR, capacity 10 | 4.66 / 0.042 | 4.39 / 0.037 | = fixed | 4.37 / 0.037 | 4.61 / 0.056 | = fixed |
| TF donchian, capacity 10 | 1.35 / 0.081 | 1.35 / 0.061 | = fixed | 1.35 / 0.061 | 0.99 / 0.091 | = fixed |
| MR with volatility regimes, 8 symbols | 3.13 / 0.37 | 3.33 / 0.24 | 1.75 / 0.29 | 1.51 / 0.13 | 2.12 / 0.20 | 2.87 / 0.32 |

What this says:
1. **Entries cluster at volatility shocks.** An RSI(2) crash is a large move; a 20-bar estimate still reports the
   calm past and levers the position up into the shock. The 5-bar leg (`vol_fast`) exists for this reason: it
   lowered drawdown in most regime-switching seeds.
2. Inverse-vol sizing mainly **lowers drawdown** (TF: 8.1 % -> 6.1 %; regime MR: 37 % -> 24 %) and does not raise
   Sharpe significantly anywhere, so the gate rejects it. On real data it must earn its place the same way.
3. **The gross cap binds hard in cell mode** (every signal takes the full cache notional, so 20 symbols can ask for
   20x capital); with a capacity limit positions are capital / max_positions and the cap is inactive at 1.0.
   Cell-mode Sharpe is therefore not a tradable number; capacity-mode rows are.
4. The daily vol target equalises risk across volatility regimes exactly (tested), but with a 63-day lookback it
   cuts Sharpe on these markets; it is a risk tool, not a return tool.

## One call
```
from sfactory.evaluation.sizing_ablation import run_sizing
rep = run_sizing(fm, cache, bars_dev, membership, accepted_row_cfg, registry)
```

## Not yet
- Portfolio-level risk budget across rows (the combined policy still uses inverse-vol weights with a correlation
  cap, `portfolio/combine.py`); a per-row risk cap there is the natural next step.
- Evidence-package and run_real integration of `run_sizing` (per accepted row), like run_edge_state.
