"""FastAPI application: JSON API under /api, the built dashboard (web/dist) at /.

    uv run --group web python -m sfactory.web --config D:/sf2_admin --port 8765

Authentication: set `SF_WEB_TOKEN` (or pass `token=`) and every /api route except /api/health and /api/login needs
either `Authorization: Bearer <token>` or the session cookie that /api/login sets (an HMAC of the token, HttpOnly,
SameSite=Strict; the token itself is never stored in the browser). Without a token the API is open, which is only
acceptable on 127.0.0.1: `python -m sfactory.web` refuses to bind elsewhere without one. Use HTTPS (a reverse proxy)
beyond a trusted LAN. No other secrets are stored (MT5 password: SF_MT5_PASSWORD).
"""
from __future__ import annotations

import hashlib
import hmac
import os
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from sfactory.web import readers
from sfactory.web.config_store import ConfigStore
from sfactory.web.jobs import JobManager
from sfactory.web.schemas import COLLECTIONS, PlatformSettings

API_VERSION = "1"
COOKIE = "sf_session"
OPEN_PATHS = {"/api/health", "/api/login", "/api/logout"}


def session_value(token: str) -> str:
    """What the cookie holds: an HMAC of the token, so a stolen cookie does not reveal the token itself."""
    return hmac.new(token.encode(), b"sfactory-web-session-v1", hashlib.sha256).hexdigest()


def platform_meta() -> dict:
    """Everything a form needs to offer valid choices: methods, grids, exits, filters, activation modes."""
    from sfactory.policy.catalog import (
        CATALOG_VERSION,
        DIVERSE_CATALOG_VERSION,
        DIVERSE_METHODS,
        MR_METHODS,
        TF_METHODS,
    )
    from sfactory.policy.edge_state import MODES, ActivationConfig
    from sfactory.policy.ladder import RUNG_LEVEL, LadderConfig
    from sfactory.signals.methods import DEFAULT, FAMILY, GRID, NEEDS_CTX
    from sfactory.signals.specs import (
        MR_FILTER_LIBRARY,
        STRUCTURAL_MARKET_UP,
        exit_library_for,
        neutral_exit_for,
    )
    methods = [{"method": m, "family": FAMILY[m], "grid": list(GRID[m]), "default": DEFAULT[m],
                "needs_context": NEEDS_CTX.get(m), "neutral_exit": asdict(neutral_exit_for(m)),
                "exit_library": [asdict(e) for e in exit_library_for(m)]} for m in FAMILY]
    return {"api_version": API_VERSION, "methods": methods, "families": sorted(set(FAMILY.values())),
            "rungs": list(RUNG_LEVEL), "filters": [asdict(f) for f in MR_FILTER_LIBRARY],
            "structural_filters": [asdict(STRUCTURAL_MARKET_UP)], "activation_modes": list(MODES),
            "activation_defaults": asdict(ActivationConfig()), "row_defaults": asdict(LadderConfig()),
            "catalogues": {"equity": {"version": CATALOG_VERSION, "methods": list(MR_METHODS + TF_METHODS)},
                           "diverse": {"version": DIVERSE_CATALOG_VERSION, "methods": list(DIVERSE_METHODS)}},
            "collections": list(COLLECTIONS)}


def create_app(config_dir: str | Path, static_dir: str | Path | None = None, token: str | None = None) -> FastAPI:
    """token: required API token; default from the SF_WEB_TOKEN environment variable (empty = no authentication)."""
    token = token if token is not None else os.environ.get("SF_WEB_TOKEN", "")
    session = session_value(token) if token else ""
    store = ConfigStore(config_dir)
    repo_root = Path(__file__).resolve().parents[3]

    managers: dict = {}

    def jobs() -> JobManager:
        """One manager per (scripts dir, interpreter): it owns the running child processes."""
        s = store.settings()
        key = (s.scripts_dir or str(repo_root / "scripts"), s.python)
        if key not in managers:
            prev = [p for m in managers.values() for p in m._procs.items()]
            managers[key] = JobManager(config_dir, key[0], s.python if s.python != "python" else None)
            managers[key]._procs.update(prev)
        return managers[key]

    app = FastAPI(title="Strategy Factory v2", version=API_VERSION)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
                       allow_methods=["*"], allow_headers=["*"])

    def authenticated(request: Request) -> bool:
        if not token:
            return True
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer ") and hmac.compare_digest(auth[7:].strip(), token):
            return True
        return hmac.compare_digest(request.cookies.get(COOKIE, ""), session)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api") and path not in OPEN_PATHS and not authenticated(request):
            return JSONResponse({"detail": "authentication required"}, status_code=401)
        return await call_next(request)

    def nf(e):
        raise HTTPException(404, str(e))

    @app.get("/api/health")
    def health(request: Request):
        ok = authenticated(request)
        out = {"ok": True, "api_version": API_VERSION, "auth_required": bool(token), "authenticated": ok}
        if ok:
            out["config_dir"] = str(store.root)
        return out

    @app.post("/api/login")
    def login(body: dict, response: Response, request: Request):
        if not token:
            return {"authenticated": True}
        if not hmac.compare_digest(str(body.get("token", "")), token):
            raise HTTPException(401, "wrong token")
        response.set_cookie(COOKIE, session, httponly=True, samesite="strict", secure=request.url.scheme == "https",
                            max_age=14 * 24 * 3600, path="/")
        return {"authenticated": True}

    @app.post("/api/logout")
    def logout(response: Response):
        response.delete_cookie(COOKIE, path="/")
        return {"authenticated": False}

    @app.get("/api/meta")
    def meta():
        return platform_meta()

    # --- settings & documents ---------------------------------------------------------------------------
    @app.get("/api/settings")
    def get_settings():
        return store.settings()

    @app.put("/api/settings")
    def put_settings(s: PlatformSettings):
        return store.save_settings(s)

    @app.get("/api/config/{collection}")
    def list_docs(collection: str):
        try:
            return store.list(collection)
        except KeyError as e:
            nf(e)

    @app.get("/api/config/{collection}/{doc_id}")
    def get_doc(collection: str, doc_id: str):
        try:
            return store.get(collection, doc_id)
        except (KeyError, FileNotFoundError) as e:
            nf(e)

    def _save(collection: str, doc: dict, create: bool):
        try:
            return store.put(collection, doc, create)
        except KeyError as e:
            nf(e)
        except FileExistsError as e:
            raise HTTPException(409, f"{e} already exists") from e
        except (ValueError, TypeError) as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/api/config/{collection}", status_code=201)
    def create_doc(collection: str, doc: dict):
        return _save(collection, doc, True)

    @app.put("/api/config/{collection}/{doc_id}")
    def update_doc(collection: str, doc_id: str, doc: dict):
        if doc.get("id") != doc_id:
            raise HTTPException(422, "the id in the path and in the document differ")
        return _save(collection, doc, False)

    @app.delete("/api/config/{collection}/{doc_id}", status_code=204)
    def delete_doc(collection: str, doc_id: str):
        try:
            store.delete(collection, doc_id)
        except (KeyError, FileNotFoundError) as e:
            nf(e)

    # --- research outputs ---------------------------------------------------------------------------------
    @app.get("/api/runs")
    def runs():
        return readers.list_runs(store.settings().runs_root)

    @app.get("/api/runs/{run_id}")
    def run(run_id: str):
        try:
            return readers.run_detail(store.settings().runs_root, run_id)
        except FileNotFoundError as e:
            nf(e)

    @app.get("/api/runs/{run_id}/rows/{row}")
    def run_row(run_id: str, row: str):
        try:
            return readers.run_row(store.settings().runs_root, run_id, row)
        except FileNotFoundError as e:
            nf(e)

    @app.get("/api/runs/{run_id}/report", response_class=HTMLResponse)
    def run_report(run_id: str):
        p = Path(store.settings().runs_root) / run_id / "report.html"
        if not p.exists():
            raise HTTPException(404, "no report")
        return HTMLResponse(p.read_text(encoding="utf-8"))

    @app.post("/api/runs/{run_id}/policy", status_code=201)
    def policy_from_run(run_id: str, body: dict):
        """Create a policy document from the accepted rows of a run (the analyst then edits / freezes it)."""
        pkg = readers.load_json(Path(store.settings().runs_root) / run_id / "evidence.json")
        if pkg is None:
            nf(run_id)
        hold = (pkg.get("holdout") or {}).get("policy") or {}
        entries = [{"config": r} for r in hold.get("rows") or []]
        if not entries:
            raise HTTPException(422, "the run has no frozen policy rows (open the holdout first)")
        doc = {"id": body.get("id") or f"policy-{run_id}"[:64].lower(), "name": body.get("name") or f"From {run_id}",
               "entries": entries, "source_run": run_id}
        return _save("policies", doc, True)

    @app.get("/api/registry")
    def registry():
        return readers.registry_summary(store.settings().registry_path)

    @app.get("/api/live")
    def live():
        return readers.list_live(store.settings().live_dir)

    @app.get("/api/live/{name}")
    def live_one(name: str):
        try:
            return readers.live_detail(store.settings().live_dir, name)
        except FileNotFoundError as e:
            nf(e)

    # --- jobs ------------------------------------------------------------------------------------------------
    @app.get("/api/jobs")
    def list_jobs():
        return jobs().list()

    @app.post("/api/jobs", status_code=201)
    def launch(body: dict):
        preset = body.get("preset")
        if preset:
            try:
                p = store.get("job_presets", preset)
            except FileNotFoundError as e:
                nf(e)
            kind, args = p["kind"], {**p.get("args", {}), **(body.get("args") or {})}
        else:
            kind, args = body.get("kind"), body.get("args") or {}
        try:
            return jobs().launch(kind, args, preset or "")
        except (ValueError, FileNotFoundError) as e:
            raise HTTPException(422, str(e)) from e

    @app.get("/api/jobs/{jid}")
    def job(jid: str):
        try:
            return jobs().get(jid)
        except FileNotFoundError as e:
            nf(e)

    @app.post("/api/jobs/{jid}/cancel")
    def cancel(jid: str):
        try:
            return jobs().cancel(jid)
        except FileNotFoundError as e:
            nf(e)

    # --- the dashboard ---------------------------------------------------------------------------------------
    dist = Path(static_dir) if static_dir else repo_root / "web" / "dist"
    if (dist / "index.html").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/"):
                raise HTTPException(404, "unknown API path")
            f = dist / path
            return FileResponse(f if path and f.is_file() else dist / "index.html")

    return app
