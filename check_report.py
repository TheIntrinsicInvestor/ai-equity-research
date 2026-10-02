import sys
from pathlib import Path

ticker = sys.argv[1].upper() if len(sys.argv) > 1 else "CCL"
html = Path(f"test_{ticker.lower()}_report.html").read_text(encoding="utf-8")

checks = {
    "Header present":        '<div class="header">' in html,
    "Key metrics bar":       'class="key-metrics"' in html,
    "Plotly charts loaded":  "Plotly.newPlot" in html,
    "All 7 chart divs":      all(
        f'id="chart-{n}"' in html
        for n in ["price","revenue_eps","margins","comps","estimates","relative_perf","sentiment"]
    ),
    "FCF chart div":             'id="chart-fcf"' in html,
    "Recommendation badge":      "rec-badge" in html,
    "Investment thesis":         'class="narrative"' in html,
    "Risks/catalysts section":   "Key Risks" in html or "Near-Term Catalysts" in html,
    "Guidance table":            "Forward Guidance" in html,
    "Quant score present":       "Quant Score" in html,
    "Signal overrides present":  "overrides" in html,
    "No raw None in output":     "None" not in html,
    "No unrendered Jinja":       "{{" not in html and "{%" not in html,
    "Print button":              "btn-print" in html,
    "IBM Plex font":             "IBM+Plex" in html,
    "Sentiment read block":      "Sentiment Read" in html,
}

all_ok = True
for name, result in checks.items():
    status = "OK  " if result else "FAIL"
    if not result:
        all_ok = False
    print(f"  [{status}] {name}")

print()
if all_ok:
    print("All checks passed!")
else:
    print("Some checks FAILED — see above")
