"""
Pipeline orchestrator. Coordinates all data, analysis, and output modules.

Phase 1 (pre-peer-gate): fetch target data (incremental cache), suggest peers.
Phase 2 (post-peer-gate): fetch peer data (incremental cache), run all analysis.

Caching: all WRDS data is stored incrementally in .data_cache/<TICKER>/.
  On first run: full 5-year history fetched and saved.
  On subsequent runs: only rows newer than the last cached date are fetched and merged.
  Cache is shared with run_report.py — data fetched by either path is reused by both.
  Peer lists are NOT cached (user controls them interactively); peer financial data IS.
"""

import json
import os
from datetime import date

from pipeline.data.wrds_connector import get_connection
from pipeline.data.compustat import fetch_financials
from pipeline.data.crsp import fetch_prices, fetch_benchmarks
from pipeline.data.ibes import fetch_estimates
from pipeline.data.sec_analytics import fetch_transcripts
from pipeline.data.yfinance_fallback import fetch_current_price, supplement_prices
from pipeline.data.data_cache import TickerDataCache
from pipeline.analysis.peer_selector import select_peers
from pipeline.analysis.comps_engine import compute_comps
from pipeline.analysis.finbert_analyzer import analyze_transcripts
from pipeline.analysis.claude_analyzer import extract_guidance, form_initial_thesis, reconcile_and_finalize
from pipeline.analysis.scoring_engine import compute_score, format_signal_feed
from pipeline.output.chart_generator import generate_all_charts
from pipeline.output.report_renderer import render_report
from config import GICS_SECTOR_ETF, TRANSCRIPT_COUNT

# OUTCOMES_DIR lets Railway point this at a mounted volume; without it,
# container-root writes vanish on every deploy (review 3.11).
_OUTCOMES_PATH = os.path.join(
    os.environ.get("OUTCOMES_DIR") or os.path.dirname(os.path.dirname(__file__)),
    "outcomes.jsonl",
)


def _log_outcome(ticker: str, final_analysis: dict, score_result: dict, current_price: float | None) -> None:
    entry = {
        "ticker":           ticker,
        "date":             date.today().isoformat(),
        "recommendation":   final_analysis.get("recommendation"),
        "conviction":       final_analysis.get("conviction"),
        "mechanical_score": score_result.get("score"),
        "current_price":    current_price,
    }
    with open(_OUTCOMES_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _fetch_target_with_cache(conn, ticker: str) -> dict | None:
    cache = TickerDataCache(ticker)

    annual_since    = cache.since_date("annual",    "datadate", overlap_days=120)
    quarterly_since = cache.since_date("quarterly", "datadate", overlap_days=120)
    fin = fetch_financials(conn, ticker, annual_since, quarterly_since)
    if fin is None:
        return None
    fin["annual"]    = cache.merge_col_df("annual",    fin["annual"],    "datadate")
    fin["quarterly"] = cache.merge_col_df("quarterly", fin["quarterly"], "datadate")

    price_since = cache.since_date("prices", use_index=True, overlap_days=0)
    new_prices  = fetch_prices(conn, ticker, since=price_since)
    prices      = cache.merge_index_df("prices", new_prices)
    prices      = supplement_prices(ticker, prices)
    cache.save_df("prices", prices)

    eps_since = cache.since_date("eps_history", "statpers", overlap_days=30)
    estimates = fetch_estimates(conn, ticker, eps_since=eps_since)
    estimates["eps_history"] = cache.merge_col_df(
        "eps_history", estimates["eps_history"], "statpers"
    )

    cache.set("initialised", True)
    return {
        "ticker":        ticker,
        "financials":    fin,
        "prices":        prices,
        "estimates":     estimates,
        "current_price": fetch_current_price(ticker, prices),
    }


def _fetch_peer_with_cache(conn, peer_ticker: str) -> dict | None:
    cache = TickerDataCache(peer_ticker)

    annual_since    = cache.since_date("annual",    "datadate", overlap_days=120)
    quarterly_since = cache.since_date("quarterly", "datadate", overlap_days=120)
    fin = fetch_financials(conn, peer_ticker, annual_since, quarterly_since)
    if fin is None:
        return None
    fin["annual"]    = cache.merge_col_df("annual",    fin["annual"],    "datadate")
    fin["quarterly"] = cache.merge_col_df("quarterly", fin["quarterly"], "datadate")

    price_since = cache.since_date("prices", use_index=True, overlap_days=0)
    new_prices  = fetch_prices(conn, peer_ticker, since=price_since)
    prices      = cache.merge_index_df("prices", new_prices)
    prices      = supplement_prices(peer_ticker, prices)
    cache.save_df("prices", prices)

    return {
        "ticker":        peer_ticker,
        "financials":    fin,
        "prices":        prices,
        "current_price": fetch_current_price(peer_ticker, prices),
    }


def run_phase1(ticker: str) -> dict:
    """
    Fetch target company data (incremental cache) and auto-suggest peers.
    Returns target_data dict and suggested_peers list, or an error string.
    """
    try:
        conn = get_connection()
        target_data = _fetch_target_with_cache(conn, ticker)
        if target_data is None:
            return {"error": f"Ticker '{ticker}' not found in Compustat. Verify the ticker."}

        suggested_peers, peer_warning = select_peers(conn, target_data["financials"])
        return {
            "target_data":    target_data,
            "suggested_peers": suggested_peers,
            "peer_warning":   peer_warning,
        }
    except Exception as e:
        return {"error": str(e)}


def run_phase2(ticker: str, target_data: dict, confirmed_peer_tickers: list) -> str:
    """
    Fetch peer data (incremental cache), run all analysis modules, render report.
    Returns fully self-contained HTML string.
    """
    conn = get_connection()

    # Peers — each peer's financial data is cached independently
    peer_data = []
    for pt in confirmed_peer_tickers:
        peer = _fetch_peer_with_cache(conn, pt)
        if peer is not None:
            peer_data.append(peer)

    # Transcripts — only download filings not already on disk
    cache = TickerDataCache(ticker)
    cached_transcripts = cache.load_json("transcripts") or []
    known_fnames = {t["fname"] for t in cached_transcripts if "fname" in t}
    new_transcripts = fetch_transcripts(
        conn, ticker, target_data["financials"], known_fnames=known_fnames
    )
    if new_transcripts:
        all_transcripts = sorted(
            cached_transcripts + new_transcripts,
            key=lambda t: t["filing_date"],
        )[-TRANSCRIPT_COUNT:]
        cache.save_json("transcripts", all_transcripts)
    else:
        all_transcripts = cached_transcripts

    # Benchmarks
    sector_code = str(target_data["financials"].get("gsector", ""))
    sector_etf  = GICS_SECTOR_ETF.get(sector_code, "XLK")
    benchmarks  = fetch_benchmarks(conn, sector_etf)

    # Analysis
    comps       = compute_comps(target_data, peer_data)
    nlp_results = analyze_transcripts(all_transcripts)
    guidance    = extract_guidance(all_transcripts)
    score_result = compute_score(
        target_data, comps, nlp_results, target_data["estimates"], benchmarks
    )

    signal_feed = format_signal_feed(score_result)

    initial_thesis = form_initial_thesis(ticker, target_data, comps, nlp_results, guidance)
    final_analysis = reconcile_and_finalize(ticker, initial_thesis, signal_feed)

    charts = generate_all_charts(target_data, peer_data, comps, nlp_results, benchmarks)
    _log_outcome(ticker, final_analysis, score_result, target_data.get("current_price"))
    return render_report(
        ticker, target_data, peer_data, comps, nlp_results, guidance,
        final_analysis, signal_feed, charts
    )
