"""Survivorship check of a bar store (run before trusting any universe built from it).

A store that holds only today's listed symbols has (almost) no symbol whose data stops before the end of the
history: every delisted, merged or bankrupt company is missing, and the top-liquidity universe then picks the
survivors at every DP. That biases every mean-reversion and momentum result upward.

The report is evidence, not proof:
- `ended_early`: symbols whose last bar is more than `end_tolerance_days` before the store's last date (their
  data stops: delisted, merged, renamed or simply not refreshed). A US equity universe loses a few percent of its
  names every year, so over several years a survivorship-free store shows a clear share of them; the verdict uses
  `min_ended_per_year` (default 0.5 % of symbols per year of history) as a deliberately low bar.
- `started_late`: symbols whose first bar is well after the store's first date (IPOs, spin-offs).
- `long_gaps`: symbols with a hole longer than `gap_days` calendar days (possible ticker reuse or missing data).
- With a membership file the check becomes direct: members of the index at some time with no bars at all, or
  with bars that stop before their membership ends, are named - each one is a survivorship leak.
Verdicts: `delistings_present`, `suspicious` (fewer than the bar), `likely_survivor_only` (none at all over >= 3
years), `too_short` (under `min_years` of history, no verdict).
"""
from __future__ import annotations

from datetime import timedelta

import polars as pl


def listing_profile(bars: pl.DataFrame) -> pl.DataFrame:
    """Per symbol: first and last bar date, number of bars, longest calendar gap between bars (days)."""
    d = bars.select("symbol", pl.col("date").cast(pl.Date).alias("day")).unique().sort(["symbol", "day"])
    return d.group_by("symbol").agg(
        pl.col("day").min().alias("first"), pl.col("day").max().alias("last"), pl.len().alias("n_days"),
        pl.col("day").diff().dt.total_days().max().fill_null(0).alias("max_gap_days"),
    ).sort("symbol")


def survivorship_report(bars: pl.DataFrame, membership: pl.DataFrame | None = None, end_tolerance_days: int = 10,
                        start_tolerance_days: int = 30, gap_days: int = 30, min_years: float = 3.0,
                        min_ended_per_year: float = 0.005, max_listed: int = 25) -> dict:
    prof = listing_profile(bars)
    if prof.is_empty():
        return {"verdict": "empty", "symbols": 0}
    start, end = prof["first"].min(), prof["last"].max()
    years = (end - start).days / 365.25
    ended = prof.filter(pl.col("last") < end - timedelta(days=end_tolerance_days))
    started = prof.filter(pl.col("first") > start + timedelta(days=start_tolerance_days))
    gaps = prof.filter(pl.col("max_gap_days") > gap_days)
    n = len(prof)
    per_year = len(ended) / n / years if years > 0 else 0.0
    if years < min_years:
        verdict = "too_short"
    elif len(ended) == 0:
        verdict = "likely_survivor_only"
    elif per_year < min_ended_per_year:
        verdict = "suspicious"
    else:
        verdict = "delistings_present"
    by_year = (ended.group_by(pl.col("last").dt.year().alias("year")).len().sort("year")
               .rename({"len": "ended"}).to_dicts())
    rep = {
        "verdict": verdict, "symbols": n, "first_date": str(start), "last_date": str(end), "years": round(years, 2),
        "ended_early": len(ended), "ended_early_share": round(len(ended) / n, 4),
        "ended_early_per_year": round(per_year, 5), "ended_by_year": by_year,
        "started_late": len(started), "long_gaps": len(gaps),
        "examples_ended": ended.sort("last").head(max_listed).select("symbol", pl.col("last").cast(pl.Utf8)).to_dicts(),
        "examples_gaps": gaps.sort("max_gap_days", descending=True).head(max_listed)
        .select("symbol", "max_gap_days").to_dicts(),
        "thresholds": {"end_tolerance_days": end_tolerance_days, "min_years": min_years,
                       "min_ended_per_year": min_ended_per_year},
    }
    if membership is not None and len(membership):
        m = membership.select("symbol", pl.col("start").cast(pl.Date), pl.col("end").cast(pl.Date))
        m = m.filter(pl.col("start") <= end)
        missing = sorted(set(m["symbol"]) - set(prof["symbol"]))
        j = m.join(prof, on="symbol", how="inner").with_columns(
            pl.min_horizontal(pl.col("end").fill_null(end), pl.lit(end)).alias("member_until"))
        short = j.filter(pl.col("last") < pl.col("member_until") - timedelta(days=end_tolerance_days))
        rep["membership"] = {
            "members_ever": m["symbol"].n_unique(), "members_without_bars": len(missing),
            "examples_without_bars": missing[:max_listed],
            "members_with_bars_ending_before_membership": short["symbol"].n_unique(),
            "examples_short": short.select("symbol", pl.col("last").cast(pl.Utf8), pl.col("member_until").cast(pl.Utf8))
            .unique("symbol").head(max_listed).to_dicts(),
        }
        if missing or len(short):
            rep["verdict"] = "membership_gaps"
    rep["advice"] = _advice(rep["verdict"])
    return rep


def _advice(verdict: str) -> str:
    return {
        "delistings_present": "The store contains symbols whose data stops before the end: delisted names are "
                              "present. Check the examples are real delistings, not stale downloads.",
        "suspicious": "Fewer symbols stop early than a survivorship-free equity store would show; delisted names "
                      "may be missing. Treat results as upper bounds until the history is completed.",
        "likely_survivor_only": "No symbol stops before the end of the history: the store very likely holds only "
                                "today's survivors. Every universe built from it is survivorship-biased.",
        "membership_gaps": "Index members are missing from the store (or their data stops while still members): "
                           "the membership universe is incomplete. Fill these symbols before trusting results.",
        "too_short": "The history is too short for a verdict.",
        "empty": "No bars.",
    }[verdict]
