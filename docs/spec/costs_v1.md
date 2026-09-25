# v1 cost profiles -> v2 cost CSV (roadmap 7)

Code: `costs/convert.py` (pure conversion, no YAML), `scripts/convert_moneta_costs.py` (reads v1's YAML with
pyyaml, only for this script). Tests: `tests/test_cost_convert.py`.

## What it reads
The v1 `configs/costs` directory exactly as v1 loads it: every profile file, the generated
`moneta/moneta_profiles.yaml`, `assignments.yaml` (groups + symbols) merged with the generated
`moneta/assignments.yaml` (a symbol in both is an error, as in v1). Each symbol is resolved as v1's
`resolve_profile` does: symbol entry first, then its asset-class group, overrides merged.

## Units
| v1 | v2 |
|---|---|
| spread `fixed` (bps / price / pip), `hourly_profile` (mean of 24 h), `broker_scaled` (broker spread in price), `from_data` (fallback x scale) | `spread_bps`, full spread (half is charged per side) |
| commission `percent` / `per_lot` / `per_order` / `per_share` (min/max per order); USD only | `commission_bps` per side |
| slippage `fixed` + `atr_fraction` x ATR% | `slippage_bps` per side |
| swap `annual_rate` (fraction/yr, day_count) / `points_per_day` / `currency_per_lot_day` | `swap_long_pct`, `swap_short_pct` (% per year, 360-day accrual) |

Price-unit costs need a reference price (median close of the last 250 daily bars in the v1 store, or `--prices`);
per-order and per-share commissions need a trade notional (`--notional`); ATR slippage needs ATR% (store).
**A symbol whose profile needs a value that is missing is left out and listed with the reason** - `load_cost_overrides`
would otherwise give it the default silently. Non-USD commissions / per-lot swaps are reported, not converted.
The D-324 proxy (`status: placeholder`) is converted and flagged; `to_verify` flags (e.g. `swap_assumed` for
ETFs) are carried to the CSV.

## Outputs
- cost CSV for `run_real.py --costs costs_moneta.csv` (extra provenance columns: profile, status, broker symbol,
  reference price, to_verify, notes; ignored by the loader)
- `broker_symbols.csv` (research -> broker symbol) for `MT5Broker(symbol_map=read_broker_map(...))`
- a JSON report: converted, skipped with reasons, placeholders, to-verify flags

## Command (owner's machine)
```
uv run --with pyyaml python scripts/convert_moneta_costs.py --v1-costs <v1>/configs/costs \
    --universe <v1>/configs/universe/us_equity_daily.csv --store <StrategyFactory_data/store> \
    --notional 10000 --out costs_moneta.csv --map-out broker_symbols.csv
```

## Limits
- One reference price per symbol: price-unit spreads and per-lot commissions become constant bps (v1 applies them
  bar by bar). For US shares (bps spread, no commission) this is exact.
- The hourly spread shape (`broker_scaled`, `hourly_profile`) collapses to its mean: fine for daily rows, a
  simplification for intraday rows.
