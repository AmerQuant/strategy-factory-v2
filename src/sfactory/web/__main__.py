"""`python -m sfactory.web --config <dir> [--host 127.0.0.1] [--port 8765]`."""
from __future__ import annotations

import argparse


def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="admin configuration directory (created if missing)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--static", help="built dashboard (default: web/dist in the repository)")
    a = ap.parse_args(argv)
    import uvicorn

    from sfactory.web.app import create_app
    uvicorn.run(create_app(a.config, a.static), host=a.host, port=a.port)


if __name__ == "__main__":
    main()
