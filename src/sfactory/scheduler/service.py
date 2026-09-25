"""The scheduler service (docs/spec/scheduling.md, option C): a standalone process that fires `schedules`
documents from the admin configuration directory.

    uv run python -m sfactory.scheduler --config D:/sf2_admin [--tick 15]

Every tick it reads the schedule documents (edits in the dashboard apply at the next tick), fires the ones that
are due, and writes a heartbeat. A fire runs the schedule's job presets one after the other through the same job
manager as the dashboard (so every step appears in Jobs with its log) and stops at the first failure. Rules:
- a fire missed by more than `grace_minutes` (service down, machine asleep) is recorded as "missed", or run once
  late when the schedule says `misfire: run_once` (paper jobs catch up by themselves; never for live);
- a schedule whose previous run is still going is not started again ("skipped: still running");
- a new or edited schedule is planned from now: saving a document never fires it immediately.
Preset arguments may contain `{today}` (the fire's local date in the schedule's time zone) and `{now}`.
Files: `<config>/scheduler/state.json` (next fire per schedule), `runs/<run>.json`, `heartbeat.json`.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sfactory.forward import alerts
from sfactory.web.config_store import ConfigStore
from sfactory.web.jobs import JobManager
from sfactory.web.schemas import Schedule

HEARTBEAT_STALE = timedelta(minutes=2)


def _now() -> datetime:
    return datetime.now(UTC)


def substitute(args: dict, due: datetime, tz: str) -> dict:
    local = due.astimezone(ZoneInfo(tz))
    rep = {"{today}": local.date().isoformat(), "{now}": local.isoformat(timespec="minutes")}

    def sub(v):
        if isinstance(v, str):
            for k, x in rep.items():
                v = v.replace(k, x)
        return v
    return {k: sub(v) for k, v in args.items()}


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
    tmp.replace(path)


class SchedulerService:
    def __init__(self, config_dir: str | Path, clock=_now, repo_root: str | Path | None = None, poll: float = 0.5):
        self.config_dir = Path(config_dir)
        self.store = ConfigStore(config_dir)
        self.dir = self.config_dir / "scheduler"
        (self.dir / "runs").mkdir(parents=True, exist_ok=True)
        self.clock, self.poll = clock, poll
        self.repo_root = Path(repo_root) if repo_root else Path(__file__).resolve().parents[3]
        p = self.dir / "state.json"
        self.state: dict = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        self.threads: dict[str, threading.Thread] = {}
        self._managers: dict = {}

    # --- jobs ---------------------------------------------------------------------------------------------
    def manager(self) -> JobManager:
        s = self.store.settings()
        key = (s.scripts_dir or str(self.repo_root / "scripts"), s.python)
        if key not in self._managers:
            self._managers[key] = JobManager(self.config_dir, key[0], s.python if s.python != "python" else None,
                                             runner="scheduler")
        return self._managers[key]

    def _save_run(self, run: dict) -> None:
        _write(self.dir / "runs" / f"{run['id']}.json", run)

    def _new_run(self, sch: Schedule, due: datetime, status: str, note: str = "") -> dict:
        now = self.clock()
        run = {"id": now.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "schedule": sch.id, "name": sch.name,
               "due": due.isoformat(), "started_at": now.isoformat(timespec="seconds"), "status": status,
               "note": note, "steps": []}
        self._save_run(run)
        return run

    def _run_chain(self, sch: Schedule, run: dict, due: datetime) -> None:
        mgr = self.manager()
        status = "succeeded"
        for preset in sch.steps:
            try:
                p = self.store.get("job_presets", preset)
            except FileNotFoundError:
                status, run["note"] = "failed", f"job preset {preset!r} not found"
                break
            try:
                meta = mgr.launch(p["kind"], substitute(p.get("args", {}), due, sch.tz), preset,
                                  origin=f"schedule:{sch.id}")
            except (ValueError, FileNotFoundError) as e:
                status, run["note"] = "failed", f"{preset}: {e}"
                break
            run["steps"].append({"preset": preset, "job": meta["id"], "status": "running"})
            self._save_run(run)
            end = mgr.wait(meta["id"], poll=self.poll)
            run["steps"][-1]["status"] = end["status"]
            if end["status"] != "succeeded":
                status, run["note"] = "failed", f"step {preset} {end['status']}"
                break
        run.update(status=status, finished_at=self.clock().isoformat(timespec="seconds"))
        self._save_run(run)
        if status != "succeeded":
            alerts.emit(self.config_dir / "events.jsonl", "warning", "scheduler",
                        f"schedule {sch.name}: {run['note']}", schedule=sch.id, run=run["id"])

    # --- the loop -----------------------------------------------------------------------------------------
    def tick(self) -> list[dict]:
        """One pass: plan, fire what is due, record. Returns the runs started or recorded in this pass."""
        now = self.clock()
        docs = {d["id"]: d for d in self.store.list("schedules")}
        events = []
        for sid in set(self.state) - set(docs):
            del self.state[sid]                                   # schedule deleted in the dashboard
        for sid, d in docs.items():
            try:
                sch = Schedule(**d)
                trig = sch.trigger()
            except ValueError as e:                               # edited by hand into something invalid
                self.state[sid] = {"error": str(e)}
                continue
            st = self.state.setdefault(sid, {})
            fp = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()
            if st.get("fingerprint") != fp or "next" not in st:  # new or edited: plan from now, never fire now
                st.clear()
                st.update(fingerprint=fp, next=trig.next_fire(now).isoformat())
                continue
            due = datetime.fromisoformat(st["next"])
            if now < due:
                continue
            st["next"] = trig.next_fire(now).isoformat()
            if not sch.enabled:
                continue
            late = now - due
            if late > timedelta(minutes=sch.grace_minutes) and sch.misfire == "skip":
                events.append(self._new_run(sch, due, "missed", f"{int(late.total_seconds() // 60)} min late"))
                alerts.emit(self.config_dir / "events.jsonl", "warning", "scheduler",
                            f"schedule {sch.name}: run due {due.isoformat(timespec='minutes')} was missed",
                            schedule=sch.id)
                continue
            t = self.threads.get(sid)
            if t is not None and t.is_alive():
                events.append(self._new_run(sch, due, "skipped", "previous run still going"))
                continue
            run = self._new_run(sch, due, "running", "late run" if late > timedelta(minutes=sch.grace_minutes) else "")
            events.append(run)
            t = threading.Thread(target=self._run_chain, args=(sch, run, due), name=f"schedule-{sid}", daemon=True)
            self.threads[sid] = t
            t.start()
            st["last_run"] = run["id"]
        _write(self.dir / "state.json", self.state)
        _write(self.dir / "heartbeat.json", {"at": now.isoformat(timespec="seconds"), "pid": os.getpid(),
                                             "schedules": len(docs)})
        return events

    def join(self, timeout: float = 60) -> None:
        for t in list(self.threads.values()):
            t.join(timeout)

    def run_forever(self, tick_seconds: float = 15) -> None:
        while True:
            self.tick()
            time.sleep(tick_seconds)


def heartbeat_age(config_dir: str | Path, now: datetime | None = None) -> timedelta | None:
    p = Path(config_dir) / "scheduler" / "heartbeat.json"
    if not p.exists():
        return None
    at = datetime.fromisoformat(json.loads(p.read_text(encoding="utf-8"))["at"])
    return (now or _now()) - at
