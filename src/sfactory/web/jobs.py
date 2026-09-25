"""Background jobs: the admin launches the platform's own scripts (run_real, run_daily, the cost converter, the
speed benchmark) as subprocesses from a saved preset. Logs go to `<config_dir>/jobs/<job>.log`; job metadata to
`<config_dir>/jobs/<job>.json`, so the list survives a server restart."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

SCRIPTS = {"run_real": "run_real.py", "run_daily": "run_daily.py", "convert_costs": "convert_moneta_costs.py",
           "bench_speed": "bench_speed.py"}


def build_args(args: dict) -> list[str]:
    """{"store": "D:/x", "diverse": True, "workers": 0} -> ["--store", "D:/x", "--diverse", "--workers", "0"]."""
    out: list[str] = []
    for k, v in args.items():
        flag = "--" + k.replace("_", "-")
        if v is None or v is False or v == "":
            continue
        if v is True:
            out.append(flag)
        elif isinstance(v, list):
            for x in v:
                out += [flag, str(x)]
        else:
            out += [flag, str(v)]
    return out


class JobManager:
    def __init__(self, root: str | Path, scripts_dir: str | Path, python: str | None = None):
        self.root = Path(root) / "jobs"
        self.root.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = Path(scripts_dir)
        self.python = python or sys.executable
        self._procs: dict[str, subprocess.Popen] = {}

    def launch(self, kind: str, args: dict, preset: str = "") -> dict:
        if kind not in SCRIPTS:
            raise ValueError(f"unknown job kind {kind}")
        script = self.scripts_dir / SCRIPTS[kind]
        if not script.exists():
            raise FileNotFoundError(f"script not found: {script}")
        jid = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        cmd = [self.python, str(script), *build_args(args)]
        log = open(self.root / f"{jid}.log", "w", encoding="utf-8")  # noqa: SIM115 - handed to the child
        env = dict(os.environ)                       # the package importable even from a bare interpreter
        src = self.scripts_dir.parent / "src"
        if src.is_dir():
            env["PYTHONPATH"] = os.pathsep.join(p for p in (str(src), env.get("PYTHONPATH", "")) if p)
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=str(self.scripts_dir.parent), env=env)
        self._procs[jid] = proc
        meta = {"id": jid, "kind": kind, "preset": preset, "cmd": cmd, "pid": proc.pid,
                "started_at": datetime.now(UTC).isoformat(timespec="seconds"), "status": "running",
                "returncode": None}
        self._save(meta)
        return meta

    def _save(self, meta: dict) -> None:
        (self.root / f"{meta['id']}.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")

    def _refresh(self, meta: dict) -> dict:
        proc = self._procs.get(meta["id"])
        if meta["status"] == "running":
            if proc is None:                                  # server restarted: the process is not ours now
                meta["status"] = "unknown"
            elif proc.poll() is not None:
                meta["returncode"] = proc.returncode
                meta["status"] = "succeeded" if proc.returncode == 0 else "failed"
                meta["finished_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            self._save(meta)
        return meta

    def list(self) -> list[dict]:
        metas = [json.loads(p.read_text(encoding="utf-8")) for p in self.root.glob("*.json")]
        return sorted((self._refresh(m) for m in metas), key=lambda m: m["started_at"], reverse=True)

    def get(self, jid: str, tail: int = 400) -> dict:
        p = self.root / f"{jid}.json"
        if not p.exists():
            raise FileNotFoundError(jid)
        meta = self._refresh(json.loads(p.read_text(encoding="utf-8")))
        log = self.root / f"{jid}.log"
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines() if log.exists() else []
        return {**meta, "log": lines[-tail:]}

    def cancel(self, jid: str) -> dict:
        proc = self._procs.get(jid)
        if proc is not None and proc.poll() is None:
            proc.terminate()
        meta = json.loads((self.root / f"{jid}.json").read_text(encoding="utf-8"))
        meta["status"] = "cancelled"
        self._save(meta)
        return meta
