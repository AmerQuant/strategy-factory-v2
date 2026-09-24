from html.parser import HTMLParser

from sfactory.report.html import fa, render_html, svg_lines


class _Collect(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags, self.text = [], []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, data):
        self.text.append(data)


PKG = {
    "generated_at": "2026-09-24T00:00:00", "meta": {"data": "syn", "catalog": "v1"}, "trials_in_registry": 18,
    "rows": [{"row": "MR-RSI-BUY-EQ", "family": "MR", "direction": "buy", "n": 100, "sharpe": 2.5, "max_dd": 0.1,
              "p": 0.001, "bh": True, "dsr": 0.99, "path": "standalone", "robust": True},
             {"row": "TF-MA_CROSS-SELL-EQ", "family": "TF", "direction": "sell", "n": 80, "sharpe": -0.4,
              "max_dd": 0.3, "p": 0.8, "bh": False, "dsr": 0.01, "path": None}],
    "combined_dev": {"sharpe": 2.4, "effective_n": 1.0}, "benchmark_all_rows_equal_dev": 1.1,
    "effective_n_all_rows": 1.8,
    "dev_curve": {"dates": ["2015-01-01", "2015-02-01", "2015-03-01"], "combined": [0, 5, 9], "benchmark": [0, 2, 3]},
    "holdout": {"status": "fail", "checks": {"sharpe": False, "max_dd": True, "benchmark": True},
                "criteria": {"sharpe_min": 1.0, "max_dd_max": 0.2},
                "holdout": {"sharpe": 0.5, "max_dd": 0.05, "benchmark_sharpe": 0.1}, "policy_hash": "abc"},
}


def test_report_is_rtl_self_contained_and_reports_burned_holdout():
    out = render_html(PKG)
    p = _Collect()
    p.feed(out)
    assert ("html", {"lang": "fa", "dir": "rtl"}) in p.tags
    assert not any(t in ("script", "link", "img") for t, _ in p.tags)           # no external resources
    txt = "".join(p.text)
    assert "رویه سوخته است" in txt and "MR-RSI-BUY-EQ" in txt and "مستقل" in txt
    assert sum(1 for t, _ in p.tags if t == "svg") == 2                        # dev curve + row bars


def test_number_formatting_and_empty_chart():
    assert fa(1234.567) == "۱٬۲۳۴٫۵۷" and fa(None) == "—" and fa(True) == "بله"
    assert "داده‌ای" in svg_lines([], {})
