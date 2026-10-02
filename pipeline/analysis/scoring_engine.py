"""
Rules-based scoring engine. Assigns points across valuation, growth, sentiment,
analyst signals, price momentum, EPS surprise, and ROIC trend.
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta


def _revenue_growth_trend(annual: pd.DataFrame) -> tuple[float | None, str]:
    """
    Returns (cagr_delta, description) comparing recent 3yr CAGR to prior 3yr CAGR.
    cagr_delta > 0 means accelerating, < 0 means decelerating.
    """
    if annual.empty or "sale" not in annual.columns:
        return None, "insufficient data"
    rev = annual["sale"].dropna().tolist()
    if len(rev) < 4:
        return None, "insufficient data"

    def cagr(start, end, years):
        if not start or not end or start <= 0:
            return None
        return (end / start) ** (1 / years) - 1

    # Windows: split the series at a midpoint; years always equal the index
    # distance so CAGR exponents are correct (review 1.6).
    if len(rev) >= 7:
        recent = cagr(rev[-4], rev[-1], 3)
        prior = cagr(rev[-7], rev[-4], 3)
    elif len(rev) == 6:
        recent = cagr(rev[-4], rev[-1], 3)
        prior = cagr(rev[0], rev[-4], 2)
    else:  # 4 or 5 years
        mid = len(rev) // 2
        recent = cagr(rev[mid], rev[-1], len(rev) - 1 - mid)
        prior = cagr(rev[0], rev[mid], mid)

    if recent is None or prior is None:
        return None, "insufficient data"
    delta = recent - prior
    if delta > 0.02:
        return delta, f"Accelerating (recent {recent:.1%} vs prior {prior:.1%})"
    if delta < -0.02:
        return delta, f"Decelerating (recent {recent:.1%} vs prior {prior:.1%})"
    return delta, f"Stable (recent {recent:.1%} vs prior {prior:.1%})"


def _price_momentum(prices: pd.DataFrame, sp500_series: pd.Series | None) -> tuple[float | None, str]:
    """
    6-month price return vs S&P 500. Returns (excess_return, description).
    Positive = outperforming.
    """
    if prices is None or prices.empty or "adj_price" not in prices.columns:
        return None, "No price data"
    cutoff = pd.Timestamp(date.today() - timedelta(days=183))
    prices_sorted = prices.sort_index()
    recent = prices_sorted[prices_sorted.index >= cutoff]
    if len(recent) < 5:
        return None, "Insufficient recent price data"
    stock_ret = recent["adj_price"].iloc[-1] / recent["adj_price"].iloc[0] - 1

    if sp500_series is not None and not sp500_series.empty:
        sp_recent = sp500_series[sp500_series.index >= cutoff]
        if len(sp_recent) >= 5:
            sp_ret = sp_recent.iloc[-1] / sp_recent.iloc[0] - 1
            excess = stock_ret - sp_ret
            direction = "outperforming" if excess > 0 else "underperforming"
            return excess, f"6M {direction} S&P 500 by {abs(excess):.1%} (stock {stock_ret:+.1%} vs S&P {sp_ret:+.1%})"

    return stock_ret, f"6M return {stock_ret:+.1%} (no S&P benchmark for comparison)"


def _eps_surprise_trend(eps_history: pd.DataFrame | None) -> tuple[int | None, str]:
    """
    Count of beats vs misses across recent reported periods (IBES data here is annual — pdicity='ANN'; see review 1.8).
    Returns (net_beats, description) where net_beats = beats - misses.
    """
    if eps_history is None or eps_history.empty:
        return None, "No EPS history"
    if "actps" not in eps_history.columns:
        return None, "No actual EPS data in history"

    df = eps_history.dropna(subset=["actps", "meanest"]).tail(4)
    if len(df) < 2:
        return None, "Insufficient EPS actual/estimate pairs"

    beats = int((df["actps"] > df["meanest"]).sum())
    misses = int((df["actps"] < df["meanest"]).sum())
    net = beats - misses
    return net, f"{beats} beats, {misses} misses across last {len(df)} reported fiscal years"


def _roic_trend(annual: pd.DataFrame) -> tuple[float | None, str]:
    """
    ROIC = EBIT / InvestedCapital.
    EBIT = ebitda - dp (D&A subtracted; dp filled to 0 when missing for older cache rows).
    IC = ceq + dltt + dlc - che (equity + net debt, financing-side).
    Falls back to total assets when ceq is unavailable (old cache rows).
    Trend = recent 2yr avg vs prior 2yr avg; positive delta = improving.
    """
    if annual is None or annual.empty:
        return None, "No annual data"
    if not {"ebitda", "at"}.issubset(annual.columns):
        return None, "Insufficient data for ROIC"

    df = annual.copy().dropna(subset=["ebitda", "at"])
    if len(df) < 3:
        return None, "Insufficient years for ROIC trend"

    # EBIT: subtract D&A when available; older cache rows have dp=NaN → treat as 0
    dp_col = df["dp"].fillna(0) if "dp" in df.columns else pd.Series(0, index=df.index)
    df["ebit"] = df["ebitda"] - dp_col

    # Invested capital (financing side): equity + net debt.
    # Row-level fallback: old cache rows have ceq=NaN → use total assets for
    # those rows to avoid clipping 0+debt-cash to 1 for cash-rich companies.
    if "ceq" in df.columns and df["ceq"].notna().any():
        debt = df.get("dltt", pd.Series(0, index=df.index)).fillna(0)
        dc   = df.get("dlc",  pd.Series(0, index=df.index)).fillna(0)
        cash = df.get("che",  pd.Series(0, index=df.index)).fillna(0)
        ic_new = (df["ceq"] + debt + dc - cash).clip(lower=1)
        df["ic"] = np.where(df["ceq"].notna(), ic_new, df["at"].clip(lower=1))
        ic_label = "EBIT/IC"
    else:
        df["ic"] = df["at"].clip(lower=1)
        ic_label = "EBIT/Assets"

    df["roic"] = df["ebit"] / df["ic"]
    roics = df["roic"].tolist()

    if len(roics) >= 4:
        recent_avg = np.mean(roics[-2:])
        prior_avg = np.mean(roics[-4:-2])
    else:
        recent_avg = roics[-1]
        prior_avg = np.mean(roics[:-1])

    delta = recent_avg - prior_avg
    return delta, f"ROIC trend ({ic_label}): recent avg {recent_avg:.1%} vs prior {prior_avg:.1%} ({'+' if delta >= 0 else ''}{delta:.1%})"


RECOMMENDATION_MAP = [
    (5, "Strong Buy"),
    (3, "Buy"),
    (1, "Hold"),
    (-1, "Sell"),
    (float("-inf"), "Strong Sell"),
]


def _tier(score: int) -> str:
    for threshold, label in RECOMMENDATION_MAP:
        if score >= threshold:
            return label
    return "Strong Sell"


_SECTOR_NAMES = {
    "10": "Energy", "15": "Materials", "20": "Industrials",
    "25": "Consumer Discretionary", "30": "Consumer Staples", "35": "Health Care",
    "40": "Financials", "45": "Information Technology",
    "50": "Communication Services", "55": "Utilities", "60": "Real Estate",
}

# EV/EBITDA is structurally misleading for these sectors:
#   40 (Financials): bank "debt" is deposits/liabilities (their product), not leverage
#   60 (Real Estate): EBITDA ignores depreciation that REITs add back via FFO
_EV_EBITDA_SKIP = {"40", "60"}

# ROIC = EBITDA / Total Assets is undefined for banks; total assets are their loan book,
# funded by deposits — not invested capital in the industrial sense.
_ROIC_SKIP = {"40"}

# P/B (Price-to-Book) is the canonical bank valuation multiple — replaces EV/EBITDA
# for Financials where book equity is the primary measure of intrinsic value.
_PB_ONLY = {"40"}


def compute_score(
    target_data: dict,
    comps: dict,
    nlp_results: list[dict],
    estimates: dict,
    benchmarks: dict | None = None,
) -> dict:
    breakdown = []
    total = 0
    gsector = str(target_data.get("financials", {}).get("gsector", ""))

    def add(signal: str, detail: str, points: int):
        nonlocal total
        breakdown.append({"signal": signal, "detail": detail, "points": points})
        total += points

    # ── Valuation signals ──
    for metric, label, max_pts in [
        ("ev_ebitda", "EV/EBITDA", 2),
        ("pb", "P/B", 2),
        ("pe", "P/E", 2),
        ("ps", "P/S", 1),
    ]:
        if metric == "ev_ebitda" and gsector in _EV_EBITDA_SKIP:
            sector_name = _SECTOR_NAMES.get(gsector, gsector)
            add(f"{label} vs peers", f"Not applicable for {sector_name}", 0)
            continue

        if metric == "pb" and gsector not in _PB_ONLY:
            continue  # P/B only scored for Financials

        target_val = comps["target"].get(metric)
        stats = comps["peer_stats"].get(metric, {})
        p25 = stats.get("p25")
        p75 = stats.get("p75")
        median = stats.get("median")

        if target_val is None or median is None:
            add(f"{label} vs peers", "Insufficient data", 0)
            continue

        if p25 is not None and target_val < p25:
            pct = (target_val - median) / median * 100
            add(f"{label} vs peers", f"Below 25th pct ({target_val:.1f}x vs median {median:.1f}x, {pct:+.0f}%)", max_pts)
        elif p75 is not None and target_val > p75:
            pct = (target_val - median) / median * 100
            add(f"{label} vs peers", f"Above 75th pct ({target_val:.1f}x vs median {median:.1f}x, {pct:+.0f}%)", -max_pts)
        else:
            pct = (target_val - median) / median * 100
            add(f"{label} vs peers", f"In-line with peers ({target_val:.1f}x vs median {median:.1f}x, {pct:+.0f}%)", 0)

    # ── Revenue growth trend ──
    annual = target_data.get("financials", {}).get("annual", pd.DataFrame())
    delta, description = _revenue_growth_trend(annual)
    if delta is None:
        add("Revenue growth trend", description, 0)
    elif delta > 0.02:
        add("Revenue growth trend", description, 1)
    elif delta < -0.02:
        add("Revenue growth trend", description, -1)
    else:
        add("Revenue growth trend", description, 0)

    # ── NLP sentiment signal ──
    if nlp_results:
        latest_sentiment = nlp_results[-1]["overall_sentiment"]
        label_str = nlp_results[-1]["sentiment_label"]
        if latest_sentiment > 0.3:
            add("Earnings call sentiment", f"Positive ({latest_sentiment:.2f}) — {label_str}", 1)
        elif latest_sentiment < -0.3:
            add("Earnings call sentiment", f"Negative ({latest_sentiment:.2f}) — {label_str}", -1)
        else:
            add("Earnings call sentiment", f"Neutral ({latest_sentiment:.2f}) — {label_str}", 0)
    else:
        add("Earnings call sentiment", "No transcript data", 0)

    # ── Analyst consensus signal ──
    pct_buy = estimates.get("pct_buy")
    pct_sell = estimates.get("pct_sell")
    if pct_buy is not None and pct_buy >= 60:
        add("Analyst consensus", f"{pct_buy:.0f}% buy ratings", 1)
    elif pct_sell is not None and pct_sell >= 60:
        add("Analyst consensus", f"{pct_sell:.0f}% sell ratings", -1)
    else:
        buy_str = f"{pct_buy:.0f}%" if pct_buy is not None else "N/A"
        add("Analyst consensus", f"Mixed ({buy_str} buy)", 0)

    # ── Price momentum (6M vs S&P 500) ──
    prices = target_data.get("prices")
    sp500_series = (benchmarks or {}).get("SPY")
    momentum, mom_desc = _price_momentum(prices, sp500_series)
    if momentum is None:
        add("Price momentum (6M)", mom_desc, 0)
    elif momentum > 0.05:
        add("Price momentum (6M)", mom_desc, 1)
    elif momentum < -0.05:
        add("Price momentum (6M)", mom_desc, -1)
    else:
        add("Price momentum (6M)", mom_desc, 0)

    # ── EPS surprise track record ──
    eps_history = estimates.get("eps_history")
    net_beats, surprise_desc = _eps_surprise_trend(eps_history)
    if net_beats is None:
        add("EPS surprise track record", surprise_desc, 0)
    elif net_beats >= 3:
        add("EPS surprise track record", surprise_desc, 1)
    elif net_beats <= -2:
        add("EPS surprise track record", surprise_desc, -1)
    else:
        add("EPS surprise track record", surprise_desc, 0)

    # ── ROIC trend ──
    if gsector in _ROIC_SKIP:
        sector_name = _SECTOR_NAMES.get(gsector, gsector)
        add("ROIC trend", f"Not applicable for {sector_name} (use ROE/ROA instead)", 0)
    else:
        delta, roic_desc = _roic_trend(annual)
        if delta is None:
            add("ROIC trend", roic_desc, 0)
        elif delta > 0.01:
            add("ROIC trend", roic_desc, 1)
        elif delta < -0.01:
            add("ROIC trend", roic_desc, -1)
        else:
            add("ROIC trend", roic_desc, 0)

    recommendation = _tier(total)

    return {
        "score": total,
        "recommendation": recommendation,
        "breakdown": breakdown,
        "narrative": "",  # populated by claude_analyzer
    }


def format_signal_feed(score_result: dict) -> dict:
    """Convert score_result breakdown into a structured SignalFeed for Pass 2."""
    signals = []
    for row in score_result.get("breakdown", []):
        pts = row["points"]
        signals.append({
            "name": row["signal"],
            "value": row["detail"],
            "direction": "positive" if pts > 0 else "negative" if pts < 0 else "neutral",
            "points": pts,
        })
    return {
        "signals": signals,
        "mechanical_score": score_result.get("score", 0),
        "mechanical_recommendation": score_result.get("recommendation", "Hold"),
    }
