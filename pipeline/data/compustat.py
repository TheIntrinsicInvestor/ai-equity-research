"""
Fetches 5-year annual and quarterly financials from Compustat for a given ticker.
Returns None if the ticker is not found.
"""

import pandas as pd
from datetime import date
from config import HISTORY_YEARS


def fetch_financials(conn, ticker: str,
                     annual_since: str | None = None,
                     quarterly_since: str | None = None) -> dict | None:
    default_cutoff = date(date.today().year - HISTORY_YEARS, 1, 1).isoformat()
    annual_cutoff = annual_since or default_cutoff
    quarterly_cutoff = quarterly_since or default_cutoff

    # Ticker lives in comp.security; company metadata (GICS, name) in comp.company
    lookup = conn.raw_sql(
        """
        SELECT c.gvkey, c.conm, c.gsector, c.ggroup, c.gind, c.gsubind, c.sic,
               s.exchg
        FROM comp.security s
        JOIN comp.company c ON s.gvkey = c.gvkey
        WHERE s.tic = %(ticker)s
          AND c.costat = 'A'
        LIMIT 1
        """,
        params={"ticker": ticker.upper()},
    )
    if lookup.empty:
        return None

    row = lookup.iloc[0]
    gvkey = row["gvkey"]
    company_name = row["conm"]
    exchg = int(row["exchg"]) if pd.notna(row["exchg"]) else None
    gsector  = str(int(row["gsector"]))  if pd.notna(row.get("gsector"))  else ""
    ggroup   = str(int(row["ggroup"]))   if pd.notna(row.get("ggroup"))   else ""
    gind     = str(int(row["gind"]))     if pd.notna(row.get("gind"))     else ""
    gsubind  = str(int(row["gsubind"]))  if pd.notna(row.get("gsubind"))  else ""
    sic      = str(int(row["sic"]))      if pd.notna(row.get("sic"))      else ""

    # Annual data
    annual = conn.raw_sql(
        """
        SELECT fyear, datadate,
               sale, ebitda, ni, oancf, capx,
               at, dltt, dlc, che, ceq,
               dp,
               epspx, csho, prcc_f,
               gp, revt
        FROM comp.funda
        WHERE gvkey = %(gvkey)s
          AND indfmt = 'INDL'
          AND datafmt = 'STD'
          AND popsrc = 'D'
          AND consol = 'C'
          AND datadate >= %(cutoff)s
        ORDER BY datadate ASC
        """,
        params={"gvkey": gvkey, "cutoff": annual_cutoff},
    )

    # Compute FCF and net debt as derived columns on annual data
    if not annual.empty:
        oancf = annual.get("oancf", pd.Series(dtype=float))
        capx_s = annual.get("capx", pd.Series(dtype=float))
        annual["fcf"] = oancf.sub(capx_s.fillna(0)).where(oancf.notna())
        dltt_s = annual.get("dltt", pd.Series(0.0, index=annual.index)).fillna(0)
        dlc_s  = annual.get("dlc",  pd.Series(0.0, index=annual.index)).fillna(0)
        che_s  = annual.get("che",  pd.Series(0.0, index=annual.index)).fillna(0)
        annual["net_debt"] = dltt_s + dlc_s - che_s

    # Quarterly data (for TTM construction)
    quarterly = conn.raw_sql(
        """
        SELECT fyearq, fqtr, datadate,
               saleq, oibdpq, niq,
               atq, dlttq, dlcq, cheq,
               epspxq, cshoq, prccq, ceqq
        FROM comp.fundq
        WHERE gvkey = %(gvkey)s
          AND indfmt = 'INDL'
          AND datafmt = 'STD'
          AND popsrc = 'D'
          AND consol = 'C'
          AND datadate >= %(cutoff)s
        ORDER BY datadate ASC
        """,
        params={"gvkey": gvkey, "cutoff": quarterly_cutoff},
    )

    # Exchange name mapping (Compustat exchg codes)
    exchange_map = {11: "NYSE", 12: "AMEX", 14: "NYSE", 17: "OTC", 19: "TSX"}
    exchange = exchange_map.get(exchg, "NASDAQ") if exchg else "N/A"

    return {
        "ticker": ticker.upper(),
        "gvkey": gvkey,
        "company_name": company_name,
        "exchange": exchange,
        "gsector": gsector,
        "ggroup": ggroup,
        "gind": gind,
        "gsubind": gsubind,
        "sic": sic,
        "annual": annual,
        "quarterly": quarterly,
    }
