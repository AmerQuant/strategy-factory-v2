# Costs from the MT5 export (FX / index / metals path, part 2)

Code: `costs/mt5_costs.py`, `scripts/convert_mt5_costs.py`. Tests: `tests/test_mt5_costs.py` (synthetic specs).

```
uv run python scripts/convert_mt5_costs.py --specs mt5_specs.csv --manual manual_costs.csv \
    --spread-stat median --out costs_fx.csv --map-out broker_symbols_fx.csv
```
The output is the same cost CSV as the v1 converter (`run_real.py --costs costs_fx.csv`), with provenance columns
(`profile=mt5_spec`, broker symbol, reference price, to_verify, notes).

## Inputs
- the MT5 export (part 1, `docs/spec/mt5_specs.md`);
- `manual_costs.csv`: `symbol,commission_per_lot_side,commission_currency,slippage_bps` - the ECN commission per lot
  per side and the slippage are not in `symbol_info`; every column is required per symbol (a commission of 0 needs
  no currency);
- `--spread-stat mean|median`: required, no default.

## Conversion
| item | rule |
|---|---|
| spread | the chosen statistic of the export's bar spread, in bps (full spread) |
| commission | per lot per side (USD) / USD notional of one lot x 1e4 |
| slippage | manual bps |
| swap `points` | points x point / reference price x 360 x 100 (% per year) |
| swap `interest_current` / `interest_open` | taken as % per year (`interest_open` flagged `swap_interest_open_approx`) |
| swap `currency_symbol` / `currency_margin` / `currency_deposit` | only when that currency is USD: amount per lot per day / USD notional x 360 x 100 |
| swap `disabled` | 0 |
| swap `reopen_*`, unknown | not converted |

USD notional of one lot: `contract_size x ref_close_median` if the profit currency is USD; `contract_size` if the
base currency is USD and the calculation mode is `forex` / `forex_no_leverage`; otherwise unknown (owner's choice:
no FX conversion rates, only USD).

## Nothing silent
A symbol is left out of the CSV, with its reason in the report, when: it is not in the export, it has no spread
history, it has no manual row or an empty slippage / commission, an amount is in a currency other than USD, the USD
notional is unknown but needed, or the swap mode is not converted. Bars with zero spread are flagged
(`zero_spread_bars`).

## Limits
- One reference price per symbol (median close of the exported bars): points-based costs become constant bps.
- Swap accrues per calendar day / 360 (owner's choice); the triple-swap weekday is in the notes only.
- The spread is a statistic of MT5's per-bar `spread` field; which value MT5 stores there is not verified here.
