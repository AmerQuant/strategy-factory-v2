"""Persian RTL HTML report from the evidence package (internal team use).

No third-party dependencies: one self-contained HTML file, charts drawn as inline SVG by this module.
Every number comes from the evidence JSON. The deterministic parts (tables, charts, checks, decision status)
are rendered here; the analytical narrative is written separately from the same JSON with
docs/templates/report_prompt_fa.md and can be pasted into the `narrative` slot.
"""
from __future__ import annotations

import html
import math

FA_DIGITS = str.maketrans("0123456789.-", "۰۱۲۳۴۵۶۷۸۹٫−")

CSS = """
body{font-family:Vazirmatn,Vazir,Tahoma,sans-serif;direction:rtl;margin:0;background:#f6f7f9;color:#1f2937}
main{max-width:1100px;margin:auto;padding:24px}
h1{font-size:22px;margin:0 0 4px} h2{font-size:17px;margin:28px 0 10px;border-bottom:2px solid #e5e7eb;padding-bottom:6px}
.card{background:#fff;border-radius:10px;padding:16px 18px;box-shadow:0 1px 3px rgba(0,0,0,.06);margin-bottom:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.kpi{background:#fff;border-radius:10px;padding:12px;box-shadow:0 1px 3px rgba(0,0,0,.06)}
.kpi b{display:block;font-size:20px;margin-top:4px}
.status{display:inline-block;padding:6px 14px;border-radius:999px;font-weight:700}
.pass{background:#dcfce7;color:#166534}.fail{background:#fee2e2;color:#991b1b}.none{background:#e5e7eb;color:#374151}
table{border-collapse:collapse;width:100%;font-size:13px} th,td{padding:6px 8px;border-bottom:1px solid #eef0f3;text-align:right}
th{background:#f9fafb;position:sticky;top:0} td.num{direction:ltr;text-align:right;font-variant-numeric:tabular-nums}
.ok{color:#15803d;font-weight:700}.bad{color:#b91c1c;font-weight:700}.muted{color:#6b7280;font-size:12px}
.wrap{overflow-x:auto} svg{max-width:100%;height:auto} .ltr{direction:ltr;display:inline-block}
"""


LABELS = {
    "buy": "خرید", "sell": "فروش", "standalone": "مستقل", "portfolio": "پرتفو",
    "standalone-rejected_by_robustness": "مستقل — رد در روباستنس",
    "portfolio-rejected_by_robustness": "پرتفو — رد در روباستنس",
    "cost_x1.5_profitable": "سود با هزینه ×۱٫۵", "mc_dd_p95_within_limit": "افت MC صدک ۹۵ در حد مجاز",
    "cost_x2_profitable": "سود با هزینه ×۲", "delay_keeps_half": "تأخیر یک کندل: حفظ نیمی از expectancy",
    "noise_keeps_half": "نویز: حفظ نیمی از شارپ", "no_catastrophic_regime": "بدون رژیم فاجعه‌بار",
}


def lab(x) -> str:
    return html.escape(LABELS.get(str(x), str(x))) if x is not None else "—"


def fa(x, nd: int = 2) -> str:
    if x is None or (isinstance(x, float) and not math.isfinite(x)):
        return "—"
    if isinstance(x, bool):
        return "بله" if x else "خیر"
    if isinstance(x, (int, float)):
        s = f"{x:,.{nd}f}" if isinstance(x, float) else f"{x:,}"
        return s.replace(",", "٬").translate(FA_DIGITS)
    return html.escape(str(x))


def mark(v) -> str:
    if v is None:
        return "—"
    return '<span class="ok">✓</span>' if v else '<span class="bad">✗</span>'


def svg_lines(dates: list, series: dict, w: int = 1000, h: int = 300, colors=("#2563eb", "#9ca3af", "#dc2626")) -> str:
    vals = [v for s in series.values() for v in s]
    if not vals or not dates:
        return '<p class="muted">داده‌ای برای نمودار نیست.</p>'
    lo, hi = min(vals), max(vals)
    hi = hi if hi > lo else lo + 1
    pad_l, pad_r, pad_t, pad_b = 70, 20, 20, 40
    n = len(dates)

    def x(i):
        return pad_l + (w - pad_l - pad_r) * (i / max(n - 1, 1))

    def y(v):
        return pad_t + (h - pad_t - pad_b) * (1 - (v - lo) / (hi - lo))

    out = [(f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" font-family="Vazirmatn,Tahoma" '
            f'font-size="11" style="direction:ltr">')]
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eef0f3"/>')
        out.append(f'<text x="{pad_l - 6}" y="{y(v) + 4:.1f}" text-anchor="end" fill="#6b7280">{v:,.0f}</text>')
    if lo < 0 < hi:
        out.append(f'<line x1="{pad_l}" x2="{w - pad_r}" y1="{y(0):.1f}" y2="{y(0):.1f}" stroke="#9ca3af"/>')
    for k in range(0, n, max(1, n // 6)):
        out.append(f'<text x="{x(k):.1f}" y="{h - 12}" text-anchor="middle" fill="#6b7280">{dates[k][:7]}</text>')
    for (name, s), col in zip(series.items(), colors):
        pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(s))
        out.append(f'<polyline points="{pts}" fill="none" stroke="{col}" stroke-width="2"/>')
    ly = pad_t + 4
    for (name, _), col in zip(series.items(), colors):   # legend top-right, Persian labels right-aligned
        out.append(f'<rect x="{w - pad_r - 14}" y="{ly}" width="12" height="3" fill="{col}"/>'
                   f'<text x="{w - pad_r - 20}" y="{ly + 5}" text-anchor="end" fill="#374151">{html.escape(name)}</text>')
        ly += 16
    out.append("</svg>")
    return "".join(out)


def svg_bars(items: list[tuple[str, float, bool]], w: int = 1000, bar_h: int = 18) -> str:
    """Horizontal bars (label, value, highlighted). Labels are LTR row ids, drawn on the left."""
    if not items:
        return ""
    h = bar_h * len(items) + 30
    lo, hi = min(0.0, min(v for _, v, _ in items)), max(0.0, max(v for _, v, _ in items))
    hi = hi if hi > lo else lo + 1
    left, right = 250, 60

    def x(v):
        return left + (w - left - right) * (v - lo) / (hi - lo)

    out = [(f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" font-family="Tahoma" font-size="11" '
            f'style="direction:ltr">'),
           f'<line x1="{x(0):.1f}" x2="{x(0):.1f}" y1="5" y2="{h - 20}" stroke="#9ca3af"/>']
    for k, (lab, v, hl) in enumerate(items):
        yy = 8 + k * bar_h
        col = "#16a34a" if hl else ("#93c5fd" if v >= 0 else "#fca5a5")
        x0, x1 = sorted((x(0), x(v)))
        out.append(f'<text x="{left - 8}" y="{yy + 12}" text-anchor="end" fill="#374151">{html.escape(lab)}</text>')
        out.append(f'<rect x="{x0:.1f}" y="{yy + 2}" width="{max(x1 - x0, 1):.1f}" height="{bar_h - 6}" fill="{col}"/>')
        out.append(f'<text x="{x1 + 4 if v >= 0 else x0 - 4:.1f}" y="{yy + 12}" '
                   f'text-anchor="{"start" if v >= 0 else "end"}" fill="#374151">{v:.2f}</text>')
    out.append("</svg>")
    return "".join(out)


def _decision(pkg: dict) -> tuple[str, str, str]:
    h = pkg.get("holdout") or {}
    st = h.get("status")
    if st == "pass":
        return "pass", "قبول در holdout — آماده‌ی انکوباسیون فوروارد", "همه‌ی چک‌های از پیش ثبت‌شده‌ی holdout قبول شدند."
    if st == "fail":
        return "fail", "رد در holdout — رویه سوخته است", "فقط فوروارد تست می‌تواند نسخه‌ی اصلاح‌شده را ارزیابی کند."
    if st == "nothing_to_test":
        return "none", "هیچ ردیفی پذیرفته نشد", "holdout باز نشد و نسوخت."
    return "none", "holdout هنوز اجرا نشده", "این گزارش فقط مرحله‌ی توسعه را پوشش می‌دهد."


def render_html(pkg: dict, title: str = "گزارش رویه‌ی معاملاتی", narrative_html: str = "") -> str:
    cls, headline, sub = _decision(pkg)
    rows = pkg.get("rows", [])
    acc = [r for r in rows if r.get("path") and "rejected" not in str(r.get("path"))]
    comb = pkg.get("combined_dev") or {}
    h = pkg.get("holdout") or {}
    parts = [(f'<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8">'
              f'<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>'
              f'<style>{CSS}</style></head><body><main>'),
             (f'<h1>{html.escape(title)}</h1><p class="muted">تاریخ تولید: <span class="ltr">'
              f'{fa(pkg.get("generated_at"))}</span> · داده: <span class="ltr">'
              f'{fa((pkg.get("meta") or {}).get("data"))}</span> · کاتالوگ: '
              f'<span class="ltr">{fa((pkg.get("meta") or {}).get("catalog"))}</span></p>'),
             f'<div class="card"><span class="status {cls}">{headline}</span><p>{sub}</p></div>']
    hold = h.get("holdout") or {}
    kpis = [("trialهای رجیستری", fa(pkg.get("trials_in_registry"))),
            ("ردیف‌های پذیرفته‌شده", f"{fa(len(acc))} از {fa(len(rows))}"),
            ("شارپ ترکیب (توسعه)", fa(comb.get("sharpe"))),
            ("بنچمارک وزن برابر (توسعه)", fa(pkg.get("benchmark_all_rows_equal_dev"))),
            ("تعداد مؤثر شرط‌ها", fa(comb.get("effective_n"))),
            ("شارپ holdout", fa(hold.get("sharpe"))),
            ("افت حداکثر holdout", fa(hold.get("max_dd") * 100 if hold.get("max_dd") is not None else None, 1) + "٪"
             if hold.get("max_dd") is not None else "—")]
    parts.append('<div class="kpis">' + "".join(f'<div class="kpi">{k}<b>{v}</b></div>' for k, v in kpis) + "</div>")
    if narrative_html:
        parts.append(f'<h2>تحلیل</h2><div class="card">{narrative_html}</div>')
    dc = pkg.get("dev_curve")
    if dc:
        parts.append('<h2>سود تجمعی OOS دوره‌ی توسعه</h2><div class="card">'
                     + svg_lines(dc["dates"], {"رویه‌ی ترکیبی": dc["combined"], "وزن برابر همه‌ی ردیف‌ها": dc["benchmark"]})
                     + "</div>")
    if h.get("curve"):
        c = h["curve"]
        parts.append('<h2>holdout</h2><div class="card">'
                     + svg_lines(c["dates"], {"رویه‌ی ترکیبی": c["combined"], "بنچمارک": c["benchmark"]}) + "</div>")
        crit, chk = h.get("criteria", {}), h.get("checks", {})
        parts.append('<div class="card wrap"><table><tr><th>چک از پیش ثبت‌شده</th><th>معیار</th><th>مقدار</th>'
                     '<th>نتیجه</th></tr>'
                     f'<tr><td>شارپ ≥ صدک ۵ بوت‌استرپ توسعه</td><td class="num">{fa(crit.get("sharpe_min"))}</td>'
                     f'<td class="num">{fa(hold.get("sharpe"))}</td><td>{mark(chk.get("sharpe"))}</td></tr>'
                     f'<tr><td>افت حداکثر ≤ صدک ۹۵</td><td class="num">{fa(crit.get("max_dd_max"), 3)}</td>'
                     f'<td class="num">{fa(hold.get("max_dd"), 3)}</td><td>{mark(chk.get("max_dd"))}</td></tr>'
                     f'<tr><td>بهتر از وزن برابر همه‌ی ردیف‌ها</td><td class="num">{fa(hold.get("benchmark_sharpe"))}</td>'
                     f'<td class="num">{fa(hold.get("sharpe"))}</td><td>{mark(chk.get("benchmark"))}</td></tr></table>'
                     f'<p class="muted">هش رویه: <span class="ltr">{fa(h.get("policy_hash"))}</span></p></div>')
    parts.append('<h2>شارپ OOS ردیف‌ها</h2><div class="card">'
                 + svg_bars([(r["row"], r.get("sharpe") or 0.0, r in acc) for r in
                             sorted(rows, key=lambda r: -(r.get("sharpe") or 0))]) + "</div>")
    body = "".join(
        f'<tr><td class="num">{fa(r["row"])}</td><td>{fa(r.get("family"))}</td><td>{lab(r.get("direction"))}</td>'
        f'<td class="num">{fa(r.get("n"))}</td><td class="num">{fa(r.get("sharpe"))}</td>'
        f'<td class="num">{fa(r.get("max_dd"), 3)}</td><td class="num">{fa(r.get("p"), 4)}</td>'
        f'<td>{mark(r.get("bh"))}</td><td class="num">{fa(r.get("dsr"), 3)}</td>'
        f'<td>{mark(r.get("robust")) if "robust" in r else "—"}</td><td>{lab(r.get("path"))}</td></tr>'
        for r in rows)
    parts.append('<h2>همه‌ی ردیف‌ها</h2><div class="card wrap"><table><tr><th>ردیف</th><th>خانواده</th><th>جهت</th>'
                 '<th>معاملات</th><th>شارپ</th><th>افت</th><th>p</th><th>BH</th><th>DSR</th><th>روباستنس</th>'
                 f'<th>مسیر</th></tr>{body}</table></div>')
    rob = pkg.get("robustness_of_accepted_rows") or {}
    if rob:
        keys_m = sorted({k for v in rob.values() for k in v.get("mandatory", {})})
        keys_w = sorted({k for v in rob.values() for k in v.get("warnings", {})})
        head = "".join(f"<th>{lab(k)}</th>" for k in keys_m + keys_w)
        trs = "".join(f'<tr><td class="num">{fa(name)}</td>' + "".join(
            f"<td>{mark(v['mandatory'].get(k))}</td>" for k in keys_m) + "".join(
            f"<td>{mark(v['warnings'].get(k))}</td>" for k in keys_w) + "</tr>" for name, v in rob.items())
        parts.append('<h2>روباستنس ردیف‌های پذیرفته‌شده</h2><div class="card wrap"><p class="muted">ستون‌های اول '
                     f'اجباری‌اند، بقیه هشدار.</p><table><tr><th>ردیف</th>{head}</tr>{trs}</table></div>')
    ens = pkg.get("family_ensembles") or {}
    if ens:
        trs = "".join(f'<tr><td>{fa(k)}</td><td class="num">{fa(v["sharpe"])}</td><td class="num">{fa(v["best_member"])}'
                      f'</td><td class="num">{fa(v["best_member_sharpe"])}</td><td>{mark(v["ensemble_better"])}</td></tr>'
                      for k, v in ens.items())
        parts.append('<h2>ردیف‌های مرکب هم‌خانواده</h2><div class="card wrap"><table><tr><th>گروه</th><th>شارپ ترکیب</th>'
                     f'<th>بهترین عضو</th><th>شارپ بهترین عضو</th><th>ترکیب بهتر؟</th></tr>{trs}</table></div>')
    spa1, spa2 = pkg.get("spa_any_vs_cash"), pkg.get("spa_combined_vs_all_equal")
    parts.append('<h2>آزمون‌های چندگانه</h2><div class="card"><table>'
                 f'<tr><td>SPA: دست‌کم یک استراتژی بهتر از نقد</td><td class="num">p = {fa((spa1 or {}).get("p_value"), 3)}</td></tr>'
                 f'<tr><td>SPA: رویه‌ی ترکیبی بهتر از وزن برابر</td><td class="num">p = {fa((spa2 or {}).get("p_value"), 3)}</td></tr>'
                 f'<tr><td>تعداد مؤثر شرط‌ها در کل کاتالوگ</td><td class="num">{fa(pkg.get("effective_n_all_rows"))}</td></tr>'
                 "</table></div>")
    mg = pkg.get("meta_grid")
    if mg:
        trs = "".join(f'<tr><td class="num">{fa(s["is_years"])}</td><td class="num">{fa(s["max_positions"])}/'
                      f'{fa(s["max_new_per_day"])}</td><td class="num">{fa(s["accepted"])}</td>'
                      f'<td class="num">{fa(s["combined_sharpe"])}</td><td class="num">{fa(s["benchmark_sharpe"])}</td></tr>'
                      for s in mg["settings"])
        parts.append('<h2>گرید متا</h2><div class="card wrap"><table><tr><th>IS (سال)</th><th>ظرفیت</th><th>پذیرفته</th>'
                     f'<th>شارپ ترکیب</th><th>بنچمارک</th></tr>{trs}</table><p class="muted">سهم تنظیمات بهتر از بنچمارک: '
                     f'{fa(mg["share_beating_benchmark"] * 100, 0)}٪</p></div>')
    parts.append('<p class="muted">همه‌ی اعداد این گزارش مستقیماً از بسته‌ی شواهد خوانده شده‌اند.</p></main></body></html>')
    return "".join(parts)
