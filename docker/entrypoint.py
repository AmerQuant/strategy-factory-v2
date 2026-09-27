"""Container entrypoint (docs/docker.md): web | scheduler | tests | shell | any command.

On first start it writes /config/platform.json with the container paths (/store, /runs, /live), so the dashboard
works without editing settings; an existing file is never changed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

CONFIG = Path(os.environ.get("SF_CONFIG", "/config"))
DEFAULTS = {"store_path": "/store", "runs_root": "/runs", "registry_path": "/runs/registry.duckdb",
            "live_dir": "/live", "scripts_dir": "/app/scripts", "python": "python"}


def seed() -> None:
    CONFIG.mkdir(parents=True, exist_ok=True)
    p = CONFIG / "platform.json"
    if not p.exists():
        from sfactory.web.schemas import PlatformSettings
        p.write_text(json.dumps(PlatformSettings(**DEFAULTS).model_dump(), indent=1), encoding="utf-8")
        print(f"wrote {p} with the container paths", flush=True)


def main(argv: list[str]) -> None:
    mode, rest = (argv[0], argv[1:]) if argv else ("web", [])
    if mode == "web":
        seed()
        cmd = [sys.executable, "-m", "sfactory.web", "--config", str(CONFIG), "--host", "0.0.0.0", "--port", "8765"]
    elif mode == "scheduler":
        seed()
        hb = CONFIG / "scheduler" / "heartbeat.json"
        if os.environ.get("SF_SCHEDULER_TAKEOVER") == "1" and hb.exists():
            hb.unlink()                    # this container is the only scheduler on this config (docs/docker.md)
        cmd = [sys.executable, "-m", "sfactory.scheduler", "--config", str(CONFIG)]
    elif mode == "tests":
        cmd = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]
    elif mode == "shell":
        cmd = ["/bin/bash"]
    else:
        cmd = [mode]
    if Path("/app").is_dir():
        os.chdir("/app")
    os.execvp(cmd[0], cmd + rest)


if __name__ == "__main__":
    main(sys.argv[1:])
