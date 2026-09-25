"""Refresh the v1 data store by running v1's own `sfac` commands, one after the other (docs/spec/data_refresh.md).

    uv run python scripts/refresh_v1_data.py --v1-repo D:/AmerAndish/Projects/Trade/strategy-factory \
        --step "data download dukascopy --series h1" --step "data ingest dukascopy --series h1 --set-reference"
    # or, in one argument (dashboard presets): --steps "data download dukascopy --series h1; data ingest ..."

v2 never writes into the store itself (CLAUDE.md): this job only starts v1's CLI in v1's repository, with v1's
environment (`--runner`, default `uv run sfac`, i.e. v1's own uv project). Steps run in order and the job stops at the
first one that fails (exit code != 0), so a failed download never leads to an ingest. `--data-root` sets
SFAC_DATA_ROOT for v1 when the store is not at v1's default place. Prints a JSON report of every step.

Verified against the v1 code: `data download dukascopy --series h1|m1 [--from YYYY-MM --to YYYY-MM]` and
`data ingest dukascopy --series h1 [--set-reference]`. Other v1 commands (Alpaca, Yahoo) are passed through as given;
their flags are not checked here. A new reference snapshot is a new data version for v2 (its hash changes).
"""
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path


def run_steps(v1_repo: Path, steps: list[str], runner: str = "uv run sfac", data_root: str | None = None,
              timeout: float | None = None) -> dict:
    env = dict(os.environ)
    if data_root:
        env["SFAC_DATA_ROOT"] = str(data_root)
    out = {"v1_repo": str(v1_repo), "steps": [], "ok": True}
    base = shlex.split(runner, posix=os.name != "nt")
    for step in steps:
        cmd = base + shlex.split(step, posix=os.name != "nt")
        t0 = time.monotonic()
        try:
            p = subprocess.run(cmd, cwd=v1_repo, env=env, capture_output=True, text=True, timeout=timeout, check=False)
            rc, tail = p.returncode, (p.stdout + p.stderr).strip().splitlines()[-20:]
        except (OSError, subprocess.TimeoutExpired) as e:
            rc, tail = -1, [f"{type(e).__name__}: {e}"]
        rec = {"step": step, "returncode": rc, "seconds": round(time.monotonic() - t0, 1), "tail": tail}
        out["steps"].append(rec)
        for line in tail:
            print(f"[{step}] {line}")
        if rc != 0:
            out["ok"], out["failed_step"] = False, step
            break
    return out


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser()
    ap.add_argument("--v1-repo", required=True, help="the v1 strategy-factory repository (where `sfac` runs)")
    ap.add_argument("--step", action="append", default=[], help="one sfac command line, repeatable, run in order")
    ap.add_argument("--steps", help="several sfac command lines separated by ';' (for dashboard presets)")
    ap.add_argument("--runner", default="uv run sfac", help="how to start v1's CLI (default: uv run sfac)")
    ap.add_argument("--data-root", help="SFAC_DATA_ROOT for v1 (default: v1's own setting)")
    ap.add_argument("--timeout", type=float, help="seconds per step")
    a = ap.parse_args(argv)
    repo = Path(a.v1_repo)
    if not repo.is_dir():
        raise SystemExit(f"v1 repository not found: {repo}")
    steps = a.step + [x.strip() for x in (a.steps or "").split(";") if x.strip()]
    if not steps:
        raise SystemExit("give at least one --step (or --steps 'a; b')")
    rep = run_steps(repo, steps, a.runner, a.data_root, a.timeout)
    print(json.dumps(rep, indent=1))
    if not rep["ok"]:
        sys.exit(1)                                   # the scheduler chain stops here
    return rep


if __name__ == "__main__":
    main()
