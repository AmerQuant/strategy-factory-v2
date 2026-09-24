"""FoldManager: the ONLY place where time is sliced (invariant #2).

Decision points (DPs) every `dp_months`. Fold k: IS = [is_start, dp) and OOS = [dp + embargo, next_dp).
IS trades whose exit date is >= dp are purged. Folds never reach into the holdout unless unlocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import polars as pl


class HoldoutLockedError(RuntimeError):
    pass


def add_months(d: date, m: int) -> date:
    """First day of the month `m` months after `d`'s month (m may be negative)."""
    y, mo = divmod(d.month - 1 + m, 12)
    return date(d.year + y, mo + 1, 1)


@dataclass(frozen=True)
class Fold:
    index: int
    dp: date
    is_start: date
    oos_start: date
    oos_end: date  # exclusive
    holdout: bool = False


@dataclass(frozen=True)
class FoldConfig:
    data_start: date
    first_dp: date
    holdout_start: date
    data_end: date  # exclusive
    dp_months: int = 6
    is_years: int = 5
    anchored: bool = False
    embargo_days: int = 0


class FoldManager:
    def __init__(self, cfg: FoldConfig):
        if not (cfg.data_start < cfg.first_dp < cfg.holdout_start <= cfg.data_end):
            raise ValueError("require data_start < first_dp < holdout_start <= data_end")
        self.cfg = cfg

    def _is_start(self, dp: date) -> date:
        if self.cfg.anchored:
            return self.cfg.data_start
        return max(self.cfg.data_start, date(dp.year - self.cfg.is_years, dp.month, 1))

    def _build(self, start: date, stop: date, holdout: bool, k0: int) -> list[Fold]:
        folds, dp, k = [], start, k0
        while dp < stop:
            nxt = min(add_months(dp, self.cfg.dp_months), stop)
            folds.append(Fold(k, dp, self._is_start(dp), dp + timedelta(days=self.cfg.embargo_days), nxt, holdout))
            dp, k = nxt, k + 1
        return folds

    def dev_folds(self) -> list[Fold]:
        return self._build(self.cfg.first_dp, self.cfg.holdout_start, False, 0)

    def holdout_folds(self, unlock: bool = False) -> list[Fold]:
        if not unlock:
            raise HoldoutLockedError("holdout is locked; it may be opened exactly once in stage H")
        return self._build(self.cfg.holdout_start, self.cfg.data_end, True, len(self.dev_folds()))

    # --- data views -------------------------------------------------------------------------------
    def dev_view(self, df: pl.DataFrame, date_col: str = "date") -> pl.DataFrame:
        """Data visible to research: strictly before holdout_start."""
        return df.filter(pl.col(date_col) < self.cfg.holdout_start)

    @staticmethod
    def history_before(df: pl.DataFrame, dp: date, date_col: str = "date") -> pl.DataFrame:
        return df.filter(pl.col(date_col) < dp)

    # --- trade slicing ------------------------------------------------------------------------------
    @staticmethod
    def slice_is(trades: pl.DataFrame, fold: Fold) -> pl.DataFrame:
        return trades.filter((pl.col("entry_date") >= fold.is_start) & (pl.col("exit_date") < fold.dp))

    @staticmethod
    def slice_oos(trades: pl.DataFrame, fold: Fold) -> pl.DataFrame:
        return trades.filter((pl.col("signal_date") >= fold.oos_start) & (pl.col("signal_date") < fold.oos_end))

    # --- fast clock (design 12.5, "two clocks"): monthly sub-DPs inside a fold's OOS window ------------
    @staticmethod
    def sub_folds(fold: Fold, months: int = 1) -> list[Fold]:
        """Sub-decision points every `months` inside [fold.oos_start, fold.oos_end). Parameters stay those of
        `fold`; only activation / weights are re-decided at each sub-DP. Same index as the parent fold."""
        out, dp = [], fold.oos_start
        while dp < fold.oos_end:
            nxt = min(add_months(dp, months), fold.oos_end)
            out.append(Fold(fold.index, dp, fold.is_start, dp, nxt, fold.holdout))
            dp = nxt
        return out

    @staticmethod
    def slice_lookback(trades: pl.DataFrame, dp: date, months: int) -> pl.DataFrame:
        """Trades fully closed before `dp` that entered within the last `months` months (short window)."""
        start = add_months(dp, -months) if dp.day == 1 else add_months(dp, -months + 1)
        return trades.filter((pl.col("entry_date") >= start) & (pl.col("exit_date") < dp))

    @staticmethod
    def slice_closed_before(trades: pl.DataFrame, dp: date) -> pl.DataFrame:
        """Every trade fully closed before `dp` (shadow equity curve)."""
        return trades.filter(pl.col("exit_date") < dp)
