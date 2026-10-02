"""
Renders the final HTML investment memo using Jinja2.
Returns a fully self-contained HTML string.
"""

from datetime import date
from jinja2 import Environment, FileSystemLoader, select_autoescape
import os
import pandas as pd
from pipeline.data.gics import ind_name, subind_name

_env = Environment(
    loader=FileSystemLoader(os.path.join(os.path.dirname(__file__), "../../templates")),
    autoescape=select_autoescape(["html"]),
)

_DEFAULT_CHART_ORDER = ["price", "relative_perf", "revenue_eps", "margins", "comps", "estimates", "sentiment"]

RECOMMENDATION_CSS_CLASS = {
    "Strong Buy": "strong-buy",
    "Buy": "buy",
    "Hold": "hold",
    "Sell": "sell",
    "Strong Sell": "strong-sell",
}


def render_report(
    ticker: str,
    target_data: dict,
    peer_data: list[dict],
    comps: dict,
    nlp_results: list[dict],
    guidance: list[dict],
    final_analysis: dict,
    signal_feed: dict,
    charts: dict,
) -> str:
    fin = target_data["financials"]
    current_price_info = target_data.get("current_price", {})
    estimates = target_data.get("estimates", {})
    gsector_str = str(fin.get("gsector", ""))

    # Compute balance sheet / FCF stats for Section 07
    annual_df = fin.get("annual")
    mktcap = current_price_info.get("market_cap") or 0
    bs_stats = _compute_bs_stats(annual_df, mktcap, gsector_str)

    chart_order = final_analysis.get("chart_order") or _DEFAULT_CHART_ORDER
    # Filter to only keys that actually exist in charts dict
    chart_order = [k for k in chart_order if k in charts] + [
        k for k in _DEFAULT_CHART_ORDER if k in charts and k not in chart_order
    ]

    template = _env.get_template("report.html")
    return template.render(
        ticker=ticker,
        company_name=fin.get("company_name", ticker),
        exchange=fin.get("exchange", "N/A"),
        gsector=gsector_str,
        sector=_sector_label(fin.get("gsector", "")),
        industry=ind_name(fin.get("gind")) or subind_name(fin.get("gsubind")) or str(fin.get("gind", "")),
        current_price=current_price_info.get("price") or 0,
        market_cap=current_price_info.get("market_cap") or 0,
        report_date=date.today().strftime("%B %d, %Y"),
        recommendation=final_analysis.get("recommendation", "Hold"),
        conviction=final_analysis.get("conviction", "medium"),
        headline=final_analysis.get("headline", ""),
        investment_thesis=final_analysis.get("investment_thesis", ""),
        valuation_commentary=final_analysis.get("valuation_commentary", ""),
        sentiment_interpretation=final_analysis.get("sentiment_interpretation", ""),
        signal_agreements=final_analysis.get("signal_agreements", []),
        signal_disagreements=final_analysis.get("signal_disagreements", []),
        key_risks=final_analysis.get("key_risks", []),
        catalysts=final_analysis.get("catalysts", []),
        mechanical_score=signal_feed.get("mechanical_score", 0),
        mechanical_recommendation=signal_feed.get("mechanical_recommendation", "Hold"),
        signal_breakdown=signal_feed.get("signals", []),
        comps=comps,
        estimates=estimates,
        nlp_results=nlp_results,
        guidance=guidance,
        charts=charts,
        chart_order=chart_order,
        bs_stats=bs_stats,
    )


# Sectors where FCF/net debt section is suppressed (Financials: debt is their product)
_FCF_SKIP_SECTORS = {"40"}


def _compute_bs_stats(annual_df, mktcap: float, gsector: str) -> dict:
    """Compute FCF and balance sheet summary stats for the report template."""
    if annual_df is None or annual_df.empty or gsector in _FCF_SKIP_SECTORS:
        return {"show": False}

    latest = annual_df.iloc[-1]
    fcf_raw    = latest.get("fcf")    if "fcf"      in annual_df.columns else None
    nd_raw     = latest.get("net_debt") if "net_debt" in annual_df.columns else None
    ebitda_raw = latest.get("ebitda")

    fcf_bn = round(float(fcf_raw) / 1000, 1) if pd.notna(fcf_raw) else None
    nd_bn  = round(float(nd_raw)  / 1000, 1) if pd.notna(nd_raw)  else None

    nd_ebitda = None
    if pd.notna(nd_raw) and pd.notna(ebitda_raw) and float(ebitda_raw) > 0:
        nd_ebitda = round(float(nd_raw) / float(ebitda_raw), 1)

    fcf_yield = None
    if fcf_bn is not None and mktcap and mktcap > 0:
        fcf_yield = round(fcf_bn * 1e9 / mktcap * 100, 1)

    has_data = any(v is not None for v in [fcf_bn, nd_bn, nd_ebitda, fcf_yield])
    return {
        "show": has_data,
        "fcf_bn": fcf_bn,
        "nd_bn": nd_bn,
        "nd_ebitda": nd_ebitda,
        "fcf_yield": fcf_yield,
    }


_SECTOR_LABELS = {
    "10": "Energy",
    "15": "Materials",
    "20": "Industrials",
    "25": "Consumer Discretionary",
    "30": "Consumer Staples",
    "35": "Health Care",
    "40": "Financials",
    "45": "Information Technology",
    "50": "Communication Services",
    "55": "Utilities",
    "60": "Real Estate",
}


def _sector_label(gsector: str) -> str:
    return _SECTOR_LABELS.get(str(gsector), "Unknown Sector")
