"""Validated documents the admin edits. Every document is a JSON file under `<config_dir>/<collection>/<id>.json`.

Row configurations inside catalogues and policies are validated by building the real research objects
(`forward.daily.config_from_dict`), so the admin can never save a row the platform cannot run.
"""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _row_ok(d: dict) -> dict:
    from sfactory.forward.daily import config_from_dict
    config_from_dict(d)                                  # raises on unknown fields / methods
    return d


class Doc(BaseModel):
    id: str
    name: str
    description: str = ""

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not ID_RE.match(v):
            raise ValueError("id: lowercase letters, digits, '-' or '_' (max 64)")
        return v


class PlatformSettings(BaseModel):
    store_path: str = ""
    runs_root: str = ""
    registry_path: str = ""
    live_dir: str = ""
    scripts_dir: str = ""
    python: str = "python"
    costs: str = "moneta"
    membership_path: str = ""
    dividends_path: str = ""
    workers: int = 1
    capital: float = 100_000.0
    timezone: str = "America/New_York"
    telegram_chat_id: str = ""                           # the bot token stays in SF_TELEGRAM_TOKEN
    telegram_min_level: Literal["critical", "warning", "info"] = "warning"
    telegram_proxy: str = ""                             # HTTP proxy, e.g. http://127.0.0.1:10809
    telegram_api: str = ""                               # empty = https://api.telegram.org


class Catalogue(Doc):
    version: str = "v1"
    rows: list[dict] = Field(default_factory=list)

    @field_validator("rows")
    @classmethod
    def _rows(cls, v: list[dict]) -> list[dict]:
        return [_row_ok(r) for r in v]


class PolicyEntry(BaseModel):
    config: dict
    activation: dict | None = None
    overlay: dict | None = None                          # daily sizing overlay (forward.daily.OVERLAY_DEFAULTS)

    @field_validator("config")
    @classmethod
    def _cfg(cls, v: dict) -> dict:
        return _row_ok(v)

    @field_validator("activation")
    @classmethod
    def _act(cls, v: dict | None) -> dict | None:
        if v is not None:
            from sfactory.policy.edge_state import ActivationConfig
            ActivationConfig(**v)
        return v

    @field_validator("overlay")
    @classmethod
    def _ov(cls, v: dict | None) -> dict | None:
        if v is not None:
            from sfactory.forward.daily import overlay_of
            overlay_of({"overlay": v})
        return v


class Policy(Doc):
    entries: list[PolicyEntry] = Field(default_factory=list)
    source_run: str = ""
    frozen: bool = False


class RiskBudgetDoc(Doc):
    max_weight: float | None = Field(None, gt=0, le=1)
    family_cap: float | None = Field(None, gt=0, le=1)
    target_vol_ann: float | None = Field(None, gt=0, le=2)
    lev_max: float = Field(2.0, gt=0, le=10)


class CostProfile(Doc):
    spread_bps: float = Field(2.0, ge=0)
    commission_bps: float = Field(0.0, ge=0)
    slippage_bps: float = Field(1.0, ge=0)
    swap_long_pct: float = 0.0
    swap_short_pct: float = 0.0
    overrides_csv: str = ""                              # per-symbol CSV (scripts/convert_moneta_costs.py)


class SymbolMap(Doc):
    mapping: dict[str, str] = Field(default_factory=dict)


class BrokerAccount(Doc):
    kind: Literal["sim", "mt5"] = "sim"
    login: int | None = None
    server: str = ""
    symbol_map: str = ""                                 # id of a SymbolMap
    dry_run: bool = True                                 # the password is never stored: SF_MT5_PASSWORD


class JobPreset(Doc):
    kind: Literal["run_real", "run_daily", "run_intraday", "convert_costs", "bench_speed", "check_survivorship"]
    args: dict[str, Any] = Field(default_factory=dict)


class Schedule(Doc):
    """A chain of job presets on a market-time trigger (sfactory.scheduler). Steps run one after the other and the
    chain stops at the first failure. misfire: what to do when a fire was missed by more than `grace_minutes`
    (service down): "skip" records it as missed (always right for live jobs), "run_once" runs it once late."""
    enabled: bool = True
    steps: list[str] = Field(default_factory=list)
    kind: Literal["daily", "bars"] = "daily"
    time: str = "16:30"
    tz: str = "America/New_York"
    weekdays: list[int] = Field(default_factory=lambda: [0, 1, 2, 3, 4])
    every_minutes: int = 60
    session_start: str = "09:30"
    session_end: str = "16:00"
    delay_minutes: int = 5
    misfire: Literal["skip", "run_once"] = "skip"
    grace_minutes: int = Field(10, ge=0, le=720)

    @field_validator("steps")
    @classmethod
    def _steps(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("a schedule needs at least one job preset")
        bad = [x for x in v if not ID_RE.match(x)]
        if bad:
            raise ValueError(f"not a preset id: {bad}")
        return v

    @model_validator(mode="after")
    def _trigger(self):
        self.trigger()
        return self

    def trigger(self):
        from sfactory.scheduler.triggers import Trigger
        return Trigger(self.kind, self.time, self.tz, tuple(self.weekdays), self.every_minutes, self.session_start,
                       self.session_end, self.delay_minutes)


COLLECTIONS: dict[str, type[Doc]] = {
    "catalogues": Catalogue, "policies": Policy, "risk_budgets": RiskBudgetDoc, "cost_profiles": CostProfile,
    "symbol_maps": SymbolMap, "brokers": BrokerAccount, "job_presets": JobPreset, "schedules": Schedule,
}
