# Real data path (v1 store → v2 research run)

Code: `data/sfac_store.py`, `data/audit.py`, `data/universe.eligible_top_liquidity / universe_at`,
`data/folds_from_data.py`, `costs/model.py` (swap), `scripts/run_real.py`, `scripts/fetch_alpaca_dividends.py`.

## Source
The v1 store `StrategyFactory_data/store` (= v1 `SFAC_DATA_ROOT`) is read as written by v1:
`catalog.parquet` (one reference snapshot per symbol and timeframe) and
`<source>/<symbol>/<timeframe>/<hash>.parquet` (ts UTC bar start, OHLCV). Only references are read;
`quality_status == critical` and any price basis other than `split` (v1 D-021) are skipped and listed.
The data version is a hash of the (symbol, snapshot hash) pairs, so a changed store is a new data version
(and a new holdout).

## Gaps and how they are handled now
| Missing | Handling | Caveat in the evidence |
|---|---|---|
| index membership | universe = top-N by trailing 60-day median dollar volume at each DP, from bars before the DP, still trading at the DP (`universe_mode="top_liquidity"`) — point-in-time; survivorship-free as far as the store keeps delisted symbols | yes |
| dividends | `adj_factor = 1` (signal = split-only); `scripts/fetch_alpaca_dividends.py` can fill them later (stdlib only, not yet run against the live API) | yes |
| per-symbol broker costs | `CostModel.moneta_share_cfd_proxy()`: 2 bps spread, 1 bp commission, 1 bp slippage, swap −6.88 % long / −3.5 % short per year (v1 moneta.yaml D-322); per-symbol CSV via `--costs file.csv` | cost preset recorded |

Swap (overnight financing) is now part of `CostModel` (design invariant 4): notional × rate / 360 × calendar
days held, added to net pnl; stress scales charges only. For a 4-day MR hold at −6.88 % that is ≈ 0.08 % per
trade — material next to short-horizon MR edges.

## Audit
Critical (symbol excluded): non-positive price, OHLC inconsistent, duplicate date. Warning (reported only):
price spike that reverts, ≥ 10 identical closes, zero volume, gap > 10 days. Written to `audit_issues.csv`
and summarised in the evidence.

## Folds
From the data span (v1 D-008): holdout = last 20 % of the span, at least 18 months; first DP after 3 years
of history; DPs every 6 months; rolling 3-year IS.

## One command
```
uv run python scripts/run_real.py --store <StrategyFactory_data/store> --out <run dir> --registry <registry.duckdb>
    [--symbols broker_symbols.txt] [--top-n 500] [--costs moneta|flat5|costs.csv] [--dividends d.parquet]
    [--membership m.parquet] [--methods rsi,ibs] [--no-robustness] [--open-holdout]
```
Writes `evidence.json`, `report.html`, `audit_issues.csv` and the Parquet trade cache. The registry file must be
the same for every run (DSR counts all trials). `--open-holdout` burns the holdout of that data version.
Tested end to end on a fake store in the exact v1 layout (`tests/test_real_data_path.py`).
