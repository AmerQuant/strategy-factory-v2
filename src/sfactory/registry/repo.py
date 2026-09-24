"""Two-level registry (ADR-0003): policy-level trials + fold-level decisions, in DuckDB."""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import duckdb


class HoldoutError(RuntimeError):
    pass


class Registry:
    def __init__(self, path: str = ":memory:"):
        self.con = duckdb.connect(path)
        self.con.execute("""CREATE TABLE IF NOT EXISTS trials(trial_id VARCHAR PRIMARY KEY, row_id VARCHAR,
            config JSON, data_version VARCHAR, code_version VARCHAR, created_at TIMESTAMP, metrics JSON)""")
        self.con.execute("""CREATE TABLE IF NOT EXISTS fold_decisions(trial_id VARCHAR, fold_index INTEGER,
            dp DATE, decision JSON)""")
        self.con.execute("""CREATE TABLE IF NOT EXISTS holdout_log(data_version VARCHAR PRIMARY KEY,
            policy_hash VARCHAR, criteria JSON, registered_at TIMESTAMP, opened_at TIMESTAMP, result JSON)""")

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

    # --- holdout (design ch. 14): pre-register, open exactly once per data version, record the outcome -------
    def register_holdout(self, data_version: str, policy_hash: str, criteria: dict) -> None:
        row = self.con.execute("SELECT opened_at FROM holdout_log WHERE data_version=?", [data_version]).fetchone()
        if row is not None and row[0] is not None:
            raise HoldoutError(f"holdout for {data_version} already opened (burned); use forward testing")
        self.con.execute("DELETE FROM holdout_log WHERE data_version=?", [data_version])
        self.con.execute("INSERT INTO holdout_log VALUES (?,?,?,?,NULL,NULL)",
                         [data_version, policy_hash, json.dumps(criteria, sort_keys=True, default=str),
                          datetime.now(UTC).replace(tzinfo=None)])

    def open_holdout(self, data_version: str, policy_hash: str) -> dict:
        row = self.con.execute("SELECT policy_hash, criteria, opened_at FROM holdout_log WHERE data_version=?",
                               [data_version]).fetchone()
        if row is None:
            raise HoldoutError("holdout criteria must be pre-registered before opening")
        if row[2] is not None:
            raise HoldoutError(f"holdout for {data_version} already opened (burned); use forward testing")
        if row[0] != policy_hash:
            raise HoldoutError("policy changed after pre-registration; register the frozen policy again")
        self.con.execute("UPDATE holdout_log SET opened_at=? WHERE data_version=?",
                         [datetime.now(UTC).replace(tzinfo=None), data_version])
        return json.loads(row[1])

    def record_holdout_result(self, data_version: str, result: dict) -> None:
        self.con.execute("UPDATE holdout_log SET result=? WHERE data_version=?",
                         [json.dumps(result, sort_keys=True, default=str), data_version])
