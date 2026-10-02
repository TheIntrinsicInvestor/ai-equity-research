"""Every WRDS query must use params= binding, never f-string interpolation of runtime values."""
import re
from pathlib import Path

import pandas as pd

QUERY_FILES = [
    "pipeline/data/compustat.py",
    "pipeline/data/crsp.py",
    "pipeline/data/ibes.py",
    "pipeline/data/sec_analytics.py",
    "pipeline/analysis/peer_selector.py",
]
ROOT = Path(__file__).resolve().parent.parent


def test_no_fstring_sql():
    """No raw_sql call may be fed an f-string containing a { placeholder."""
    offenders = []
    for rel in QUERY_FILES:
        src = (ROOT / rel).read_text(encoding="utf-8")
        # any f-triple-quoted string containing SELECT and a brace interpolation
        for m in re.finditer(r'f"""(.*?)"""', src, re.DOTALL):
            body = m.group(1)
            if "SELECT" in body.upper() and "{" in body:
                offenders.append(rel)
                break
    assert not offenders, f"f-string SQL interpolation remains in: {offenders}"


class _StubConn:
    """Captures raw_sql calls; returns a one-row lookup then empty frames."""
    def __init__(self):
        self.calls = []

    def raw_sql(self, sql, params=None):
        self.calls.append((sql, params))
        if "comp.security" in sql:
            return pd.DataFrame([{
                "gvkey": "006066", "conm": "TEST CO", "gsector": 45.0,
                "ggroup": 4510.0, "gind": 451020.0, "gsubind": 45102010.0,
                "sic": 7372.0, "exchg": 11.0,
            }])
        return pd.DataFrame()


def test_ticker_is_bound_not_interpolated():
    from pipeline.data.compustat import fetch_financials
    conn = _StubConn()
    malicious = "AAPL'; DROP TABLE comp.security;--"
    fetch_financials(conn, malicious)
    lookup_sql, lookup_params = conn.calls[0]
    assert "DROP TABLE" not in lookup_sql          # never lands in SQL text
    assert lookup_params is not None
    assert malicious.upper() in lookup_params.values()
    assert "%(ticker)s" in lookup_sql
