"""
Fetches analyst consensus estimates from WRDS I/B/E/S.
Uses ibes.statsum_epsus for EPS estimates, ibes.ptgdet for price targets,
ibes.recddet for buy/hold/sell ratings, and ibes.actu_epsus for actual EPS.
Includes CUSIP-based IBES ticker resolution for post-merger ticker mismatches.
"""

import pandas as pd
from datetime import date, timedelta


def _resolve_ibes_ticker(conn, ticker: str) -> str:
    """Resolve the correct IBES ticker via CUSIP cross-reference.

    Used when the standard ticker lookup returns empty — common after mergers
    where the IBES entity ID doesn't match the new combined-company ticker.
    Returns the resolved IBES ticker, or the original ticker if lookup fails.
    """
    try:
        cusip_res = conn.raw_sql(
            """
            SELECT cusip FROM comp.security
            WHERE tic = %(ticker)s AND iid = '01'
            LIMIT 1
            """,
            params={"ticker": ticker.upper()},
        )
        if cusip_res.empty or not pd.notna(cusip_res.iloc[0]["cusip"]):
            return ticker
        cusip8 = str(cusip_res.iloc[0]["cusip"])[:8]

        ibes_res = conn.raw_sql(
            """
            SELECT ticker FROM ibes.idsum
            WHERE cusip = %(cusip)s
            ORDER BY sdates DESC
            LIMIT 1
            """,
            params={"cusip": cusip8},
        )
        if ibes_res.empty or not pd.notna(ibes_res.iloc[0]["ticker"]):
            return ticker
        return str(ibes_res.iloc[0]["ticker"]).strip()
    except Exception:
        return ticker


def _fetch_actuals(conn, ibes_ticker: str, eps_cutoff: str) -> pd.DataFrame:
    """Fetch quarterly actual EPS from ibes.actu_epsus."""
    try:
        actuals = conn.raw_sql(
            """
            SELECT ticker, pends, value AS actps
            FROM ibes.actu_epsus
            WHERE ticker = %(ticker)s
              AND pdicity = 'ANN'
              AND pends >= %(cutoff)s
            ORDER BY pends ASC
            """,
            params={"ticker": ibes_ticker, "cutoff": eps_cutoff},
        )
        return actuals
    except Exception:
        return pd.DataFrame()


def fetch_estimates(conn, ticker: str, eps_since: str | None = None) -> dict:
    default_cutoff = (date.today() - timedelta(days=5 * 90)).isoformat()
    eps_cutoff = eps_since or default_cutoff
    ibes_ticker = ticker.upper()

    def _run_queries(t: str):
        eps = conn.raw_sql(
            """
            SELECT statpers, fiscalp, fpi, meanest, medest, numest, stdev
            FROM ibes.statsum_epsus
            WHERE ticker = %(ticker)s
              AND fpi = '1'
              AND statpers >= %(cutoff)s
            ORDER BY statpers ASC
            """,
            params={"ticker": t, "cutoff": eps_cutoff},
        )
        pt_cutoff = (date.today() - timedelta(days=180)).isoformat()
        pt = conn.raw_sql(
            """
            SELECT AVG(value) AS mean_pt, COUNT(*) AS numest
            FROM ibes.ptgdet
            WHERE ticker = %(ticker)s
              AND actdats >= %(cutoff)s
              AND value IS NOT NULL
              AND measure = 'PTG'
            """,
            params={"ticker": t, "cutoff": pt_cutoff},
        )
        rec = conn.raw_sql(
            """
            SELECT buypct, holdpct, sellpct, numrec
            FROM ibes.recdsum
            WHERE ticker = %(ticker)s
            ORDER BY statpers DESC
            LIMIT 1
            """,
            params={"ticker": t},
        )
        return eps, pt, rec

    eps, pt, rec = _run_queries(ibes_ticker)

    # If all three queries returned no real data, try CUSIP-based ticker resolution.
    # Note: pt is an aggregate query — it always returns 1 row even with no matches
    # (mean_pt will be NULL). Check for actual data, not just non-empty.
    pt_has_data = not pt.empty and pd.notna(pt.iloc[0].get("mean_pt"))
    if eps.empty and not pt_has_data and rec.empty:
        resolved = _resolve_ibes_ticker(conn, ticker)
        if resolved != ibes_ticker:
            print(f"  IBES: no data for '{ibes_ticker}', retrying with resolved ticker '{resolved}'")
            eps, pt, rec = _run_queries(resolved)
            if not eps.empty or not pt.empty or not rec.empty:
                ibes_ticker = resolved

    # Fetch actual EPS from ibes.actu_epsus and merge into eps_history
    actuals = _fetch_actuals(conn, ibes_ticker, eps_cutoff)
    if not actuals.empty and not eps.empty:
        # Merge actuals onto estimates by nearest period end date (within 45 days)
        eps["statpers"] = pd.to_datetime(eps["statpers"])
        actuals["pends"] = pd.to_datetime(actuals["pends"])
        actuals_sorted = actuals.sort_values("pends")
        eps_sorted = eps.sort_values("statpers")
        merged = pd.merge_asof(
            eps_sorted,
            actuals_sorted[["pends", "actps"]],
            left_on="statpers",
            right_on="pends",
            tolerance=pd.Timedelta(days=45),
            direction="nearest",
        )
        eps = merged.drop(columns=["pends"], errors="ignore")
    elif not actuals.empty and eps.empty:
        eps = actuals.rename(columns={"pends": "statpers"})

    last_eps = eps.iloc[-1] if not eps.empty else None
    mean_eps = float(last_eps["meanest"]) if last_eps is not None and pd.notna(last_eps.get("meanest")) else None
    num_analysts = int(last_eps["numest"]) if last_eps is not None and pd.notna(last_eps.get("numest")) else None

    mean_pt = None
    if not pt.empty and pd.notna(pt.iloc[0]["mean_pt"]):
        mean_pt = float(pt.iloc[0]["mean_pt"])

    pct_buy = pct_hold = pct_sell = None
    rec_analyst_count = None
    if not rec.empty:
        row = rec.iloc[0]
        total = int(row["numrec"]) if pd.notna(row.get("numrec")) else 0
        if total > 0:
            pct_buy  = float(row["buypct"])  if pd.notna(row.get("buypct"))  else None
            pct_hold = float(row["holdpct"]) if pd.notna(row.get("holdpct")) else None
            pct_sell = float(row["sellpct"]) if pd.notna(row.get("sellpct")) else None
            rec_analyst_count = total

    return {
        "mean_eps": mean_eps,
        "mean_pt": mean_pt,
        "num_analysts": num_analysts,        # EPS estimate count
        "num_raters": rec_analyst_count,     # recommendation rater count
        "pct_buy": pct_buy,
        "pct_hold": pct_hold,
        "pct_sell": pct_sell,
        "eps_history": eps,
        "ibes_ticker_used": ibes_ticker,
    }
