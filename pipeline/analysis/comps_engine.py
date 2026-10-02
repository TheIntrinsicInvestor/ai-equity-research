"""
Computes EV/EBITDA, P/E, and P/S multiples for the target and each peer.
TTM figures are constructed from the four most recent fiscal quarters.
"""

import numpy as np
import pandas as pd


def _fval(val, default: float = 0.0) -> float:
    """Convert value to float, treating None/NA/NaN as default."""
    if val is None:
        return default
    try:
        v = float(val)
        return default if np.isnan(v) else v
    except (TypeError, ValueError):
        return default


def _ttm(quarterly: pd.DataFrame, col: str) -> float | None:
    """Sum the last 4 quarters of a column to get trailing twelve months."""
    if quarterly.empty or col not in quarterly.columns:
        return None
    recent = quarterly.sort_values("datadate").tail(4)[col].dropna()
    return float(recent.sum()) if len(recent) == 4 else None


def _compute_multiples(financials: dict, current_price: dict) -> dict:
    q = financials.get("quarterly", pd.DataFrame())

    price = _fval(current_price.get("price") if current_price else None) or None
    mktcap = _fval(current_price.get("market_cap") if current_price else None) or None

    if not price or not mktcap:
        # Fall back to last annual filing price and shares
        annual = financials.get("annual", pd.DataFrame())
        if not annual.empty:
            latest = annual.iloc[-1]
            p = _fval(latest.get("prcc_f"))
            csho = _fval(latest.get("csho"))
            if p and csho:
                price = p
                mktcap = p * csho * 1e6

    ebitda_ttm = _ttm(q, "oibdpq")
    revenue_ttm = _ttm(q, "saleq")
    ni_ttm = _ttm(q, "niq")
    eps_ttm = _ttm(q, "epspxq")

    # Debt and cash from most recent quarter
    latest_q = q.sort_values("datadate").iloc[-1] if not q.empty else pd.Series()
    if not latest_q.empty:
        debt = (_fval(latest_q.get("dlttq")) + _fval(latest_q.get("dlcq"))) * 1e6
        cash = _fval(latest_q.get("cheq")) * 1e6
        ceqq = _fval(latest_q.get("ceqq")) or None  # common equity (millions)
    else:
        debt = cash = 0
        ceqq = None

    ev = (mktcap + debt - cash) if mktcap else None

    ev_ebitda = None
    if ev and ebitda_ttm and ebitda_ttm > 0:
        ev_ebitda = ev / (ebitda_ttm * 1e6)

    pe = None
    if price and eps_ttm and eps_ttm > 0:
        pe = float(price) / eps_ttm

    ps = None
    if mktcap and revenue_ttm and revenue_ttm > 0:
        ps = mktcap / (revenue_ttm * 1e6)

    pb = None
    if mktcap and ceqq and ceqq > 0:
        pb = mktcap / (ceqq * 1e6)

    return {
        "ticker": financials.get("ticker", ""),
        "ev_ebitda": ev_ebitda,
        "pe": pe,
        "ps": ps,
        "pb": pb,
        "ev": ev,
        "mktcap": mktcap,
    }


def _peer_stats(peers: list[dict], metric: str) -> dict:
    values = [p[metric] for p in peers if p.get(metric) is not None]
    # Require at least 2 valid peers — a single data point is not a meaningful benchmark.
    # This prevents one sparse ADR from setting a wildly wrong "median".
    if len(values) < 2:
        return {"median": None, "p25": None, "p75": None}
    return {
        "median": float(np.median(values)),
        "p25": float(np.percentile(values, 25)),
        "p75": float(np.percentile(values, 75)),
    }


def compute_comps(target_data: dict, peer_data: list[dict]) -> dict:
    target_multiples = _compute_multiples(
        target_data["financials"], target_data.get("current_price")
    )

    peer_multiples = []
    for peer in peer_data:
        m = _compute_multiples(peer["financials"], peer.get("current_price"))
        peer_multiples.append(m)

    peer_stats = {
        "ev_ebitda": _peer_stats(peer_multiples, "ev_ebitda"),
        "pe": _peer_stats(peer_multiples, "pe"),
        "ps": _peer_stats(peer_multiples, "ps"),
        "pb": _peer_stats(peer_multiples, "pb"),
    }

    # Compute target premium/discount vs peer median
    discounts = {}
    for metric in ["ev_ebitda", "pe", "ps", "pb"]:
        target_val = target_multiples.get(metric)
        peer_median = peer_stats[metric]["median"]
        if target_val and peer_median:
            discounts[metric] = (target_val - peer_median) / peer_median * 100
        else:
            discounts[metric] = None

    return {
        "target": target_multiples,
        "peers": peer_multiples,
        "peer_stats": peer_stats,
        "discounts": discounts,
    }
