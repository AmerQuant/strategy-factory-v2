"""Two-level registry (ADR-0003): policy-level trials + fold-level decisions, in DuckDB."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import duckdb


class Registry:
    def __init__(self, path: str = ":memory:"):
        self.con = duckdb.connect(path)
        self.con.execute("""CREATE TABLE IF NOT EXISTS trials(trial_id VARCHAR PRIMARY KEY, row_id VARCHAR,
            config JSON, data_version VARCHAR, code_version VARCHAR, created_at TIMESTAMP, metrics JSON)""")
        self.con.execute("""CREATE TABLE IF NOT EXISTS fold_decisions(trial_id VARCHAR, fold_index INTEGER,
            dp DATE, decision JSON)""")

    def record_trial(self, row_id: str, config: dict, data_version: str, code_version: str,
                     metrics: dict) -> str:
        tid = uuid.uuid4().hex[:12]
        self.con.execute("INSERT INTO trials VALUES (?,?,?,?,?,?,?)",
                         [tid, row_id, json.dumps(config, sort_keys=True, default=str), data_version,
                          code_version, datetime.now(UTC).replace(tzinfo=None), json.dumps(metrics, default=str)])
        return tid

    def record_fold(self, trial_id: str, fold_index: int, dp, decision: dict) -> None:
        self.con.execute("INSERT INTO fold_decisions VALUES (?,?,?,?)",
                         [trial_id, fold_index, dp, json.dumps(decision, sort_keys=True, default=str)])

    def count_trials(self, row_id: str | None = None) -> int:
        if row_id is None:
            return self.con.execute("SELECT count(*) FROM trials").fetchone()[0]
        return self.con.execute("SELECT count(*) FROM trials WHERE row_id=?", [row_id]).fetchone()[0]
