"""Render the Persian RTL HTML report from an evidence JSON (see scripts/demo_holdout.py).

Run: uv run python scripts/render_report.py evidence.json report.html [narrative.html]
"""
from __future__ import annotations

import json
import sys

from sfactory.report.html import render_html

if __name__ == "__main__":
    with open(sys.argv[1], encoding="utf-8") as fh:
        pkg = json.load(fh)
    narrative = ""
    if len(sys.argv) > 3:
        with open(sys.argv[3], encoding="utf-8") as fh:
            narrative = fh.read()
    with open(sys.argv[2], "w", encoding="utf-8") as fh:
        fh.write(render_html(pkg, narrative_html=narrative))
