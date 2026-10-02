"""
Fetches earnings release text for NLP analysis.
Strategy:
  1. Resolve gvkey → CIK via wrdssec.wciklink_gvkey
  2. Find item 2.02 8-K filings via wrdssec.items8k (earnings releases)
  3. If items8k is empty or stale (>18 months), fall back to EDGAR submissions REST API
  4. Fetch EX-99.1 exhibit text from the EDGAR REST API (free, no key needed)
"""

import re
import requests
import pandas as pd
from config import TRANSCRIPT_COUNT, SEC_USER_AGENT

EDGAR_BASE = "https://www.sec.gov/Archives/"
EDGAR_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"
# SEC requires a User-Agent naming the app and a contact email.
HEADERS = {"User-Agent": SEC_USER_AGENT}


def _resolve_cik_via_edgar(ticker: str) -> str | None:
    """Look up CIK by ticker directly from SEC's company tickers list."""
    try:
        resp = requests.get(EDGAR_TICKERS, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None
        data = resp.json()
        ticker_upper = ticker.upper()
        for entry in data.values():
            if str(entry.get("ticker", "")).upper() == ticker_upper:
                cik_int = int(entry["cik_str"])
                return str(cik_int).zfill(10)
    except Exception:
        pass
    return None


def _resolve_cik(conn, gvkey: str, ticker: str = "") -> str | None:
    result = conn.raw_sql(
        """
        SELECT cik FROM wrdssec.wciklink_gvkey
        WHERE gvkey = %(gvkey)s
        LIMIT 1
        """,
        params={"gvkey": gvkey},
    )
    if not result.empty:
        return str(result.iloc[0]["cik"]).lstrip("0").zfill(10)
    if ticker:
        print(f"  wciklink_gvkey has no CIK for gvkey={gvkey} — falling back to EDGAR ticker lookup")
        return _resolve_cik_via_edgar(ticker)
    return None


def _fetch_ex99_text(fname: str) -> str | None:
    """Fetch the EX-99.1 exhibit text for a given filing index fname.

    fname format: edgar/data/<cik>/<accession>.txt
    Exhibit files live in the accession subdirectory (dashes stripped), not the CIK dir.
    In EDGAR SGML, <TYPE> precedes <FILENAME> within each document block.
    """
    index_url = EDGAR_BASE + fname
    # Accession number dir: strip .txt suffix and dashes from the filename component
    accession_no_dashes = fname.rsplit("/", 1)[-1].replace(".txt", "").replace("-", "")
    cik_dir = EDGAR_BASE + "/".join(fname.split("/")[:-1])
    base_dir = cik_dir + "/" + accession_no_dashes
    try:
        resp = requests.get(index_url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return None

        text = resp.text
        # TYPE always precedes FILENAME in EDGAR SGML document blocks
        ex99_match = re.search(
            r"<TYPE>EX-99\.1.*?<FILENAME>(.*?)\n",
            text,
            re.DOTALL | re.IGNORECASE,
        )
        if not ex99_match:
            # Fall back to returning the full submission text stripped of tags
            body = re.sub(r"<[^>]+>", " ", text)
            body = re.sub(r"\s{3,}", "\n\n", body)
            return body[:15000] if body.strip() else None

        exhibit_file = ex99_match.group(1).strip()
        exhibit_url = base_dir + "/" + exhibit_file
        ex_resp = requests.get(exhibit_url, headers=HEADERS, timeout=15)
        if ex_resp.status_code != 200:
            return None

        raw = ex_resp.text
        clean = re.sub(r"<[^>]+>", " ", raw)
        clean = re.sub(r"\s{3,}", "\n\n", clean)
        return clean.strip()[:20000]
    except Exception:
        return None


def _edgar_submissions_filings(cik: str) -> pd.DataFrame:
    """Fetch 8-K item 2.02 filings from the EDGAR submissions REST API.

    Falls back to this when wrdssec.items8k is empty or stale (>18 months).
    cik must be the zero-padded 10-digit string used in the EDGAR submissions URL.
    Returns a DataFrame with columns matching wrdssec.items8k: fdate, fname, coname.
    """
    url = EDGAR_SUBMISSIONS.format(cik=cik)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            return pd.DataFrame()
        data = resp.json()
    except Exception:
        return pd.DataFrame()

    coname = data.get("name", "")
    cik_stripped = str(int(cik))  # remove leading zeros for the EDGAR archive path
    recent = data.get("filings", {}).get("recent", {})
    if not recent:
        return pd.DataFrame()

    accessions = recent.get("accessionNumber", [])
    dates = recent.get("filingDate", [])
    forms = recent.get("form", [])
    items_list = recent.get("items", [])

    rows = []
    for acc, fdate, form, items in zip(accessions, dates, forms, items_list):
        if form != "8-K":
            continue
        # items field is a string like "2.02" or "2.02,8.01" or "Results of Operations..."
        if "2.02" not in str(items) and "Results of Operations" not in str(items):
            continue
        # Build fname in the same format as wrdssec.items8k uses
        acc_with_dashes = acc  # already formatted as "0001234567-25-000123"
        fname = f"edgar/data/{cik_stripped}/{acc_with_dashes}.txt"
        rows.append({"fdate": fdate, "fname": fname, "coname": coname})

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["fdate"] = pd.to_datetime(df["fdate"])
    df = df.sort_values("fdate", ascending=False)
    return df


def fetch_transcripts(conn, ticker: str, financials: dict,
                      known_fnames: set | None = None) -> list[dict]:
    gvkey = financials.get("gvkey")
    if not gvkey:
        return []

    cik = _resolve_cik(conn, gvkey, ticker)
    if not cik:
        return []

    # Fetch enough filings to find new ones beyond what's already cached
    look_ahead = TRANSCRIPT_COUNT * 4
    filings = conn.raw_sql(
        """
        SELECT fdate, fname, coname
        FROM wrdssec.items8k
        WHERE cik = %(cik)s
          AND item = 'Results of Operations and Financial Condition'
        ORDER BY fdate DESC
        LIMIT %(limit)s
        """,
        params={"cik": cik, "limit": int(look_ahead)},
    )
    filings = filings.drop_duplicates(subset=["fname"])

    # Fall back to EDGAR submissions API if items8k is empty or coverage is stale (>18 months)
    stale = filings.empty or (
        pd.to_datetime(filings["fdate"].max()) < pd.Timestamp.now() - pd.DateOffset(months=18)
    )
    if stale:
        print(f"  items8k empty or stale for CIK {cik} — falling back to EDGAR submissions API")
        edgar_filings = _edgar_submissions_filings(cik)
        if not edgar_filings.empty:
            filings = edgar_filings.head(look_ahead)

    if filings.empty:
        return []

    results = []
    for _, row in filings.iterrows():
        fname = str(row["fname"])
        if known_fnames and fname in known_fnames:
            continue  # already cached — skip text download
        text = _fetch_ex99_text(fname)
        if not text:
            continue
        results.append(
            {
                "ticker": ticker,
                "period": str(row["fdate"])[:7],
                "filing_date": str(row["fdate"])[:10],
                "fname": fname,
                "text": text,
            }
        )

    return list(reversed(results))  # chronological order
