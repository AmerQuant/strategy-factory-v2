# Persian RTL report (internal)

Code: `report/html.py`; CLI: `scripts/render_report.py evidence.json report.html [narrative.html]`.
Sample: `uv run python scripts/demo_holdout.py mean_revert ev.json && uv run python scripts/render_report.py ev.json report.html`.

Chosen by the owner for internal team use: **option A — plain HTML + inline SVG, no new dependency**
(alternatives considered: Jinja2 + Plotly, Jinja2 + matplotlib, python-docx; see chat of 2026-09-24).

- One self-contained file: no scripts, no external images or stylesheets (tested); font stack Vazirmatn → Tahoma.
- Sections: decision status (from holdout status), KPI cards, dev OOS cumulative pnl vs all-rows benchmark,
  holdout curve + pre-registered checks + policy hash, row Sharpe bars (accepted rows highlighted), full row table
  (p, BH, DSR, robustness, path), robustness matrix of accepted rows (mandatory then warnings), family ensembles,
  SPA, meta-grid.
- Numbers in Persian digits; row ids and dates stay LTR. Every number is read from the evidence JSON.
- The analytical narrative is written from the same JSON with `docs/templates/report_prompt_fa.md` and passed as
  `narrative.html`.
- Evidence additions for charts: `dev_curve` (weekly cumulative combined vs benchmark) and `holdout.curve`.

Interactive charts (Plotly) can be added later as an optional group without changing the evidence format.
