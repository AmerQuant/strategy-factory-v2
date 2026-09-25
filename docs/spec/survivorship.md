# Survivorship check

Code: `data/survivorship.py`, `scripts/check_survivorship.py`; part of every `run_real.py` run.
Tests: `tests/test_survivorship.py`.

Why: if the store holds only symbols that are listed today, every universe built from it (top liquidity at each
DP, or index membership without the delisted members' data) picks survivors, and mean-reversion and momentum
results are biased upward. This is the most important data check before the first real run.

## What it measures
| item | meaning |
|---|---|
| `ended_early` | symbols whose data stops more than 10 days before the store's last date (delisted, merged, renamed, or not refreshed) |
| `ended_early_per_year` | that count as a share of all symbols, per year of history |
| `started_late` | symbols that start more than 30 days after the store's first date (IPOs, spin-offs) |
| `long_gaps` | symbols with a hole of more than 30 calendar days (ticker reuse or missing data) |
| `membership` (with a membership file) | members that have no bars at all, and members whose data stops while they are still members; both are named |

## Verdicts
- `delistings_present` - symbols stop before the end at a plausible rate. Check the examples are real delistings
  and not stale downloads.
- `suspicious` - fewer than 0.5 % of symbols per year stop early (a deliberately low bar for US equities).
- `likely_survivor_only` - none stop early over at least 3 years: the store almost surely holds survivors only.
- `membership_gaps` - the membership file names members that are missing or cut short in the store.
- `too_short` - under 3 years of history.

The verdict is a heuristic: a store can pass it and still miss some delisted names. Only a membership check against
point-in-time index membership (or a delisting list from the data vendor) is direct evidence.

## In run_real
The report (without the example lists) is stored in the evidence under `meta.survivorship`; any verdict other than
`delistings_present` / `too_short` adds a caveat, which the report and the dashboard show.

## Command
```
uv run python scripts/check_survivorship.py --store <store> [--membership membership.parquet] --out survivorship.json
```
