# Data contracts (v0.1)

All frames are Polars, long format. Code: `src/sfactory/data/contracts.py`.

| Frame | Columns | Rules |
|---|---|---|
| bars | symbol, date, open, high, low, close, volume | OHLC are **split-only** (execution series). One row per symbol per trading day while listed. |
| bars + adj | ... + adj_factor | Signal series = price × adj_factor (backward, CRSP-style: bars before ex-date d × (1 − div_d / close_{d−1})). |
| dividends | symbol, ex_date, amount | Amount per share on the split-only basis. |
| membership | symbol, start, end | Point-in-time index membership; end exclusive, null = still member; includes delisted symbols. |
| trades (cache) | symbol, signal_date, entry_date, exit_date, entry_px, exit_px, shares, gross_pnl, cost, dividends, net_pnl, forced_exit | Produced once per (rule, params, data_version, cost model). |
| registry.trials | trial_id, row_id, config, data_version, code_version, created_at, metrics | One row per evaluated policy configuration. |
| registry.fold_decisions | trial_id, fold_index, dp, decision | Every in-fold decision, for audit. |

## Consequences
- Signal rules must be **scale-invariant** on the signal series (returns, ratios, RSI, relative highs). The backward adjustment rescales all history before a future ex-date by a constant, so level-based rules on the signal series would leak future dividends. Level rules (min price) use split-only prices.
- Research data = `FoldManager.dev_view(...)`; the trade cache is built only from the dev view, so no trade can touch the holdout.
