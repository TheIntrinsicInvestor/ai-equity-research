"""
Auto-selects comparable peers using a fallback chain:
  1. GICS sub-industry + US + dynamic market cap band
  2. GICS sub-industry + international + dynamic market cap band
  3. GICS industry + US + dynamic market cap band
  4. GICS industry + international + dynamic market cap band
  5. Flag to user if < MIN_PEERS found.

Returns (peers: list[dict], warning: str | None).
Each peer dict includes a `flags` list for structural outlier labels.
"""

import pandas as pd
from config import MIN_PEERS, MAX_PEERS
from pipeline.data.gics import subind_name, ind_name

# Expanding market cap bands (min_ratio, max_ratio of target mktcap)
_MKTCAP_BANDS = [(0.3, 3.0), (0.1, 10.0), (0.05, 20.0)]

# Compustat exchg codes for US-listed securities
_US_EXCHG = "(11, 12, 13, 14, 15)"

# GICS sub-industry codes for REITs (sector 60) — 8-digit codes
_REIT_SUBIND = {
    60101010, 60101020, 60101030, 60101040, 60101050,
    60101060, 60101070, 60101080,
    60102010, 60102020,
    60103010,
    60104010, 60104020,
    60105010,
    60106010,
    60107010,
}

# GICS sub-industry code often used for Business Development Companies
_BDC_SUBIND = {40203040}

# SIC codes and name patterns that indicate SPACs
_SPAC_SIC = {6770}
_SPAC_NAME_KWORDS = ("acquisition corp", "blank check", " spac ")


def _detect_flags(row: pd.Series) -> list[str]:
    flags = []

    try:
        subind = int(row.get("gsubind") or 0)
    except (ValueError, TypeError):
        subind = 0

    if subind in _REIT_SUBIND:
        flags.append("REIT")
    elif subind in _BDC_SUBIND:
        flags.append("BDC")

    try:
        sic = int(row.get("sic") or 0)
    except (ValueError, TypeError):
        sic = 0
    name_lower = str(row.get("conm") or "").lower()
    if sic in _SPAC_SIC or any(kw in name_lower for kw in _SPAC_NAME_KWORDS):
        flags.append("SPAC")

    _s = row.get("sale");    sale      = float(_s) if not pd.isna(_s) else 0.0
    _e = row.get("ebitda"); ebitda_val = float(_e) if not pd.isna(_e) else 0.0
    if sale <= 0 or ebitda_val < 0:
        flags.append("Pre-revenue")

    _m = row.get("mktcap"); mktcap = float(_m) if not pd.isna(_m) else 0.0
    if 0 < mktcap < 50e6:
        flags.append("Nano-cap")

    return flags


def _query_peers(conn, gics_col: str, gics_val: str, target_gvkey: str,
                 low: float, high: float, us_only: bool) -> pd.DataFrame:
    # Use c.loc (country of HQ) rather than exchange codes alone so that
    # ADRs listed on US exchanges (AZN, NVS, NVO) are excluded from the
    # US pass and captured in the international pass.
    geo_filter = "AND c.loc = 'USA'" if us_only else "AND c.loc != 'USA'"
    # Column names can't be bound as parameters, so gics_col is allowlisted;
    # every value is bound (review 1.1/4.2).
    if gics_col not in {"gsubind", "gind", "ggroup", "gsector"}:
        raise ValueError(f"unexpected GICS column: {gics_col!r}")
    try:
        return conn.raw_sql(
            """
            SELECT DISTINCT ON (c.gvkey)
                   s.tic, c.conm, c.gvkey, c.gsubind, c.gind, c.sic,
                   f.csho * f.prcc_f * 1e6 AS mktcap,
                   f.sale, f.ebitda
            FROM comp.company c
            JOIN comp.security s ON c.gvkey = s.gvkey
            JOIN (
                SELECT gvkey, csho, prcc_f, sale, ebitda
                FROM comp.funda
                WHERE indfmt = 'INDL'
                  AND datafmt = 'STD'
                  AND popsrc = 'D'
                  AND consol = 'C'
                  AND fyear = (
                      SELECT MAX(fyear) FROM comp.funda f2
                      WHERE f2.gvkey = comp.funda.gvkey
                        AND f2.indfmt = 'INDL'
                  )
            ) f ON c.gvkey = f.gvkey
            WHERE c.{gics_col} = %(gics_val)s
              AND c.costat = 'A'
              AND c.gvkey != %(target_gvkey)s
              {geo_filter}
              AND f.csho * f.prcc_f * 1e6 BETWEEN %(low)s AND %(high)s
            ORDER BY c.gvkey, LENGTH(s.tic), s.tic
            """.format(gics_col=gics_col, geo_filter=geo_filter),
            params={"gics_val": gics_val, "target_gvkey": target_gvkey,
                    "low": float(low), "high": float(high)},
        )
    except Exception:
        return pd.DataFrame()


def _find_best_round(conn, gics_col: str, gics_val: str,
                     target_gvkey: str, target_mktcap: float,
                     us_only: bool) -> pd.DataFrame:
    """Try progressively wider bands; return as soon as MIN_PEERS found."""
    best = pd.DataFrame()
    for min_r, max_r in _MKTCAP_BANDS:
        df = _query_peers(conn, gics_col, gics_val, target_gvkey,
                          target_mktcap * min_r, target_mktcap * max_r, us_only)
        if len(df) >= MIN_PEERS:
            return df
        if len(df) > len(best):
            best = df
    return best


def select_peers(conn, target_financials: dict) -> tuple[list[dict], str | None]:
    gsubind      = target_financials.get("gsubind", "")
    gind         = target_financials.get("gind", "")
    target_gvkey = target_financials.get("gvkey", "")

    annual = target_financials.get("annual", pd.DataFrame())
    if annual.empty:
        return [], "No financial data available for peer selection."

    latest = annual.iloc[-1]
    if not (pd.notna(latest.get("csho")) and pd.notna(latest.get("prcc_f"))):
        return [], "Cannot compute target market cap; peer selection skipped."
    target_mktcap = float(latest["csho"]) * float(latest["prcc_f"]) * 1e6

    # Fallback chain: exhaust US options before going international.
    # gind+US comes before gsubind+international so a wider domestic set
    # is preferred over sparse ADR data from a narrow sub-industry match.
    fallback_chain = [
        ("gsubind", gsubind, True),
        ("gind",    gind,    True),
        ("gsubind", gsubind, False),
        ("gind",    gind,    False),
    ]

    candidates = pd.DataFrame()
    for gics_col, gics_val, us_only in fallback_chain:
        if not gics_val:
            continue
        df = _find_best_round(conn, gics_col, gics_val, target_gvkey,
                              target_mktcap, us_only)
        if len(df) >= MIN_PEERS:
            candidates = df
            break
        if len(df) > len(candidates):
            candidates = df

    warning = None
    if len(candidates) < MIN_PEERS:
        found = len(candidates)
        warning = (
            f"Insufficient peers found automatically ({found} found, {MIN_PEERS} needed). "
            "Please add peers manually using the field below."
        )

    if candidates.empty:
        return [], warning

    candidates["_dist"] = (candidates["mktcap"] - target_mktcap).abs()
    candidates = candidates.sort_values("_dist").head(MAX_PEERS)

    peers = []
    for _, row in candidates.iterrows():
        tic = str(row.get("tic") or "").strip()
        if not tic:
            continue
        peers.append({
            "ticker": tic,
            "company_name": row.get("conm", ""),
            "market_cap": float(row["mktcap"]),
            "sub_industry": (subind_name(row.get("gsubind"))
                             or ind_name(row.get("gind"))),
            "flags": _detect_flags(row),
        })

    return peers, warning
