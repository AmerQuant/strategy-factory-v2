"""`python -m sfactory.web --config <dir> [--host 127.0.0.1] [--port 8765] [--token-file token.txt]`.

The API token comes from --token-file or the SF_WEB_TOKEN environment variable. Binding to anything other than
localhost without a token is refused: anyone on the network could otherwise edit the configuration and start jobs.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

LOCAL = {"127.0.0.1", "localhost", "::1"}


def resolve_token(token_file: str | None) -> str:
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    return os.environ.get("SF_WEB_TOKEN", "").strip()


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="admin configuration directory (created if missing)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--static", help="built dashboard (default: web/dist in the repository)")
    ap.add_argument("--token-file", help="file holding the API token (default: SF_WEB_TOKEN)")
    a = ap.parse_args(argv)
    token = resolve_token(a.token_file)
    if a.host not in LOCAL and not token:
        raise SystemExit(f"refusing to listen on {a.host} without a token: set SF_WEB_TOKEN or --token-file")
    if token and len(token) < 16:
        raise SystemExit("the API token must be at least 16 characters")
    import uvicorn

    from sfactory.web.app import create_app
    uvicorn.run(create_app(a.config, a.static, token), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
