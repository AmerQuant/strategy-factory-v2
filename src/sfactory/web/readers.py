"""Read-only views over the platform's outputs: research runs (evidence.json), the trial registry (DuckDB) and
paper / live state files. Nothing here writes; every reader tolerates missing or partial files."""
from __future__ import annotations

import json
from pathlib import Path


def load_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_runs(runs_root: str) -> list[dict]:
    root = Path(runs_root) if runs_root else None
    if root is None or not root.is_dir():
        return []
    out = []
    for ev in sorted(root.glob("*/evidence.json")):
        pkg = load_json(ev)
        if pkg is None:
            continue
        rows = pkg.get("rows") or []
        comb = pkg.get("combined_dev") or {}
        out.append({"id": ev.parent.name, "generated_at": pkg.get("generated_at"),
                    "data": (pkg.get("meta") or {}).get("data"), "catalog": (pkg.get("meta") or {}).get("catalog"),
                    "timeframe": (pkg.get("meta") or {}).get("timeframe"), "rows": len(rows),
                    "accepted": sum(1 for r in rows if r.get("path") in ("standalone", "portfolio")),
                    "trials": pkg.get("trials_in_registry"), "combined_sharpe": comb.get("sharpe"),
                    "effective_n": comb.get("effective_n"), "holdout": (pkg.get("holdout") or {}).get("status")})
    return sorted(out, key=lambda r: r.get("generated_at") or "", reverse=True)


def run_detail(runs_root: str, run_id: str) -> dict:
    p = Path(runs_root) / run_id / "evidence.json"
    pkg = load_json(p)
    if pkg is None:
        raise FileNotFoundError(run_id)
    for r in pkg.get("rows") or []:
        r.pop("fold_decisions", None)                 # large; served by run_row
    pkg["has_report"] = (Path(runs_root) / run_id / "report.html").exists()
    return pkg


def run_row(runs_root: str, run_id: str, row: str) -> dict:
    pkg = load_json(Path(runs_root) / run_id / "evidence.json")
    if pkg is None:
        raise FileNotFoundError(run_id)
    for r in pkg.get("rows") or []:
        if r.get("row") == row:
            return r
    raise FileNotFoundError(row)


def registry_summary(path: str, limit: int = 200) -> dict:
    if not path or not Path(path).exists():
        return {"available": False, "trials": 0, "recent": [], "by_row": []}
    import duckdb
    con = duckdb.connect(path, read_only=True)
    try:
        n = con.execute("SELECT count(*) FROM trials").fetchone()[0]
        recent = con.execute("SELECT trial_id, row_id, data_version, code_version, CAST(created_at AS VARCHAR), "
                             "metrics FROM trials ORDER BY created_at DESC LIMIT ?", [limit]).fetchall()
        by_row = con.execute("SELECT row_id, count(*) AS n FROM trials GROUP BY row_id ORDER BY n DESC").fetchall()
        by_data = con.execute("SELECT data_version, count(*) FROM trials GROUP BY data_version").fetchall()
    finally:
        con.close()
    keys = ("trial_id", "row_id", "data_version", "code_version", "created_at", "metrics")
    rec = []
    for r in recent:
        d = dict(zip(keys, r))
        try:
            d["metrics"] = json.loads(d["metrics"]) if d["metrics"] else {}
        except ValueError:
            d["metrics"] = {}
        rec.append(d)
    return {"available": True, "trials": n, "recent": rec, "by_row": [{"row": a, "n": b} for a, b in by_row],
            "by_data_version": [{"data_version": a, "n": b} for a, b in by_data]}


def list_live(live_dir: str) -> list[dict]:
    root = Path(live_dir) if live_dir else None
    if root is None or not root.is_dir():
        return []
    out = []
    for p in sorted(root.glob("*.json")):
        st = load_json(p)
        if not st or "books" not in st:
            continue
        pos = sum(len(b.get("positions") or []) for b in st["books"].values())
        closed = [t for b in st["books"].values() for t in b.get("closed") or []]
        log = st.get("log") or []
        out.append({"id": p.stem, "last_day": st.get("last_day"), "last_dp": st.get("last_dp"),
                    "rows": len(st.get("book") or {}), "open_positions": pos, "closed_trades": len(closed),
                    "net_pnl": sum(float(t.get("net_pnl") or 0) for t in closed),
                    "reconciled": all(x.get("reconciled") is not False for x in log[-20:]),
                    "pending": len(st.get("pending") or [])})
    return out


def live_detail(live_dir: str, name: str) -> dict:
    st = load_json(Path(live_dir) / f"{name}.json")
    if st is None:
        raise FileNotFoundError(name)
    closed = sorted((t for b in st["books"].values() for t in b.get("closed") or []),
                    key=lambda t: str(t.get("exit_date")))
    eq, cum = [], 0.0
    for t in closed:
        cum += float(t.get("net_pnl") or 0)
        eq.append({"date": str(t.get("exit_date"))[:19], "equity": cum})
    by_row: dict = {}
    for t in closed:
        x = by_row.setdefault(t["row"], {"row": t["row"], "trades": 0, "net_pnl": 0.0, "wins": 0})
        x["trades"] += 1
        x["net_pnl"] += float(t.get("net_pnl") or 0)
        x["wins"] += 1 if float(t.get("net_pnl") or 0) > 0 else 0
    positions = [p for b in st["books"].values() for p in b.get("positions") or []]
    book = [{"row": r, "method": v["config"].get("method"), "direction": v["config"].get("direction"),
             "threshold": v.get("threshold"), "exit": v.get("exit_id"), "eligible": len(v.get("eligible") or []),
             "filters": [f.get("kind") for f in v.get("filters") or []]} for r, v in (st.get("book") or {}).items()]
    return {"id": name, "last_day": st.get("last_day"), "last_dp": st.get("last_dp"), "book": book,
            "positions": positions, "pending": st.get("pending") or [], "closed": closed[-500:], "equity": eq,
            "by_row": list(by_row.values()), "log": (st.get("log") or [])[-250:], "weights": st.get("weights") or {},
            "policy_rows": len(st.get("policy") or [])}


def scheduler_summary(config_dir: str | Path, now=None) -> dict:
    """Heartbeat, next / last run per schedule and recent runs of the scheduler service (read-only)."""
    from datetime import UTC, datetime, timedelta
    root = Path(config_dir) / "scheduler"
    now = now or datetime.now(UTC)
    hb = load_json(root / "heartbeat.json")
    age = (now - datetime.fromisoformat(hb["at"])).total_seconds() if hb else None
    state = load_json(root / "state.json") or {}
    runs = [r for r in (load_json(p) for p in (root / "runs").glob("*.json")) if r] if (root / "runs").exists() else []
    runs.sort(key=lambda r: r.get("started_at", ""), reverse=True)
    last = {}
    for r in runs:
        last.setdefault(r["schedule"], r)
    scheds = []
    for p in sorted((Path(config_dir) / "schedules").glob("*.json")):
        d = load_json(p) or {}
        st = state.get(d.get("id"), {})
        scheds.append({"id": d.get("id"), "name": d.get("name"), "enabled": d.get("enabled", True),
                       "steps": d.get("steps", []), "next": st.get("next"), "error": st.get("error"),
                       "last": last.get(d.get("id"))})
    return {"heartbeat": hb, "age_seconds": age,
            "alive": age is not None and age < timedelta(minutes=2).total_seconds(),
            "schedules": scheds, "runs": runs[:50]}
