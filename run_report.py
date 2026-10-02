"""
Iterative test harness for CCL report generation.

Data caching (incremental):
  First run: fetches full history from WRDS/EDGAR, saves to .data_cache/<TICKER>/
  Subsequent runs: loads cached data, fetches only rows newer than the max
  cached date (e.g. run again a month later → ~30 new price days, 0–1 new
  transcripts, 1 new quarterly filing).

Analysis (Claude + FinBERT + scoring) always re-runs fresh so code changes
are exercised every run.
"""

import os
import sys
import traceback
from pathlib import Path
from config import GICS_SECTOR_ETF, TRANSCRIPT_COUNT

TICKER = sys.argv[1].upper() if len(sys.argv) > 1 else "AVGO"


def fetch_with_cache(ticker: str) -> dict:
    from pipeline.data.data_cache import TickerDataCache
    from pipeline.data.wrds_connector import get_connection
    from pipeline.data.compustat import fetch_financials
    from pipeline.data.crsp import fetch_prices, fetch_benchmarks
    from pipeline.data.ibes import fetch_estimates
    from pipeline.data.sec_analytics import fetch_transcripts
    from pipeline.data.yfinance_fallback import fetch_current_price, supplement_prices
    from pipeline.analysis.peer_selector import select_peers

    cache = TickerDataCache(ticker)
    is_first_run = cache.get("initialised") is None

    conn = get_connection()

    # ── Target financials (incremental: overlap 120 days to catch restatements) ──
    annual_since    = cache.since_date("annual",    "datadate", overlap_days=120)
    quarterly_since = cache.since_date("quarterly", "datadate", overlap_days=120)
    label = annual_since or "full history"
    print(f"  Fetching financials (annual since {label})...")
    target_financials = fetch_financials(conn, ticker, annual_since, quarterly_since)
    if target_financials is None:
        raise RuntimeError(f"Ticker {ticker!r} not found in Compustat")
    target_financials["annual"]    = cache.merge_col_df("annual",    target_financials["annual"],    "datadate")
    target_financials["quarterly"] = cache.merge_col_df("quarterly", target_financials["quarterly"], "datadate")

    # ── Prices (CRSP + yfinance supplement for the recent gap) ──────────────────
    price_since = cache.since_date("prices", use_index=True, overlap_days=0)
    print(f"  Fetching prices (since {price_since or 'full history'})...")
    new_prices    = fetch_prices(conn, ticker, since=price_since)
    target_prices = cache.merge_index_df("prices", new_prices)
    target_prices = supplement_prices(ticker, target_prices)
    # Save supplemented prices (includes yfinance) so momentum window is always warm
    cache.save_df("prices", target_prices)

    # ── IBES estimates (EPS history incremental; ratings/PT always fresh) ────────
    eps_since = cache.since_date("eps_history", "statpers", overlap_days=30)
    print(f"  Fetching estimates (EPS since {eps_since or 'full history'})...")
    target_estimates = fetch_estimates(conn, ticker, eps_since=eps_since)
    target_estimates["eps_history"] = cache.merge_col_df(
        "eps_history", target_estimates["eps_history"], "statpers"
    )

    current_price = fetch_current_price(ticker, target_prices)

    target_data = {
        "ticker": ticker,
        "financials": target_financials,
        "prices": target_prices,
        "estimates": target_estimates,
        "current_price": current_price,
    }

    # ── Peers (re-select on first run; use cached list afterwards) ───────────────
    cached_peer_tickers = cache.load_json("peer_tickers")
    if cached_peer_tickers and not is_first_run:
        peer_tickers = cached_peer_tickers
        print(f"  Using cached peers: {peer_tickers}")
    else:
        print("  Selecting peers...")
        suggested_peers, _ = select_peers(conn, target_financials)
        peer_tickers = [p["ticker"] for p in suggested_peers[:6]]
        cache.save_json("peer_tickers", peer_tickers)
        print(f"  Selected peers: {peer_tickers}")

    peer_data = []
    for pt in peer_tickers:
        peer_cache = TickerDataCache(pt)
        p_annual_since    = peer_cache.since_date("annual",    "datadate", overlap_days=120)
        p_quarterly_since = peer_cache.since_date("quarterly", "datadate", overlap_days=120)
        p_price_since     = peer_cache.since_date("prices",    use_index=True, overlap_days=0)

        fin = fetch_financials(conn, pt, p_annual_since, p_quarterly_since)
        if fin is None:
            continue
        fin["annual"]    = peer_cache.merge_col_df("annual",    fin["annual"],    "datadate")
        fin["quarterly"] = peer_cache.merge_col_df("quarterly", fin["quarterly"], "datadate")

        new_p  = fetch_prices(conn, pt, since=p_price_since)
        prices = peer_cache.merge_index_df("prices", new_p)
        prices = supplement_prices(pt, prices)
        peer_cache.save_df("prices", prices)

        cp = fetch_current_price(pt, prices)
        peer_data.append({"ticker": pt, "financials": fin, "prices": prices, "current_price": cp})

    # ── Transcripts (skip already-cached fnames; only download new ones) ─────────
    cached_transcripts = cache.load_json("transcripts") or []
    known_fnames = {t["fname"] for t in cached_transcripts if "fname" in t}
    print(f"  Checking for new transcripts ({len(known_fnames)} cached)...")
    new_transcripts = fetch_transcripts(conn, ticker, target_financials, known_fnames=known_fnames)
    if new_transcripts:
        print(f"  Found {len(new_transcripts)} new transcript(s)")
        all_transcripts = sorted(
            cached_transcripts + new_transcripts,
            key=lambda t: t["filing_date"]
        )[-TRANSCRIPT_COUNT:]
        cache.save_json("transcripts", all_transcripts)
    else:
        print("  No new transcripts")
        all_transcripts = cached_transcripts

    # ── Benchmarks ────────────────────────────────────────────────────────────────
    sector_code = str(target_financials.get("gsector", ""))
    sector_etf  = GICS_SECTOR_ETF.get(sector_code, "XLK")
    print(f"  Fetching benchmarks (sector ETF: {sector_etf})...")
    benchmarks = fetch_benchmarks(conn, sector_etf)

    cache.set("initialised", True)

    return {
        "target_data": target_data,
        "peer_data":   peer_data,
        "transcripts": all_transcripts,
        "benchmarks":  benchmarks,
    }


def run_pipeline(data: dict):
    from pipeline.analysis.comps_engine import compute_comps
    from pipeline.analysis.finbert_analyzer import analyze_transcripts
    from pipeline.analysis.claude_analyzer import extract_guidance, form_initial_thesis, reconcile_and_finalize
    from pipeline.analysis.scoring_engine import compute_score, format_signal_feed
    from pipeline.output.chart_generator import generate_all_charts
    from pipeline.output.report_renderer import render_report

    target_data = data["target_data"]
    peer_data   = data["peer_data"]
    transcripts = data["transcripts"]
    benchmarks  = data["benchmarks"]
    ticker      = target_data["ticker"]

    print("Running analysis pipeline...")

    print("  Computing comps...")
    comps = compute_comps(target_data, peer_data)

    print("  Running FinBERT sentiment (cached after first run)...")
    nlp_results = analyze_transcripts(transcripts)

    print("  Extracting guidance (Claude Sonnet)...")
    guidance = extract_guidance(transcripts)

    print("  Computing score + signal feed...")
    score_result = compute_score(target_data, comps, nlp_results, target_data["estimates"], benchmarks)
    signal_feed  = format_signal_feed(score_result)
    print(f"    Mechanical score: {signal_feed['mechanical_score']:+d} -> {signal_feed['mechanical_recommendation']}")

    print("  Pass 1 — forming initial thesis (Claude Sonnet)...")
    initial_thesis = form_initial_thesis(ticker, target_data, comps, nlp_results, guidance)
    print(f"    Initial: {initial_thesis.get('recommendation','?')} ({initial_thesis.get('conviction','?')} conviction)")
    print(f"    Headline: {initial_thesis.get('headline','')}")

    print("  Pass 2 — reconciling with signals (Claude Opus)...")
    final_analysis = reconcile_and_finalize(ticker, initial_thesis, signal_feed)
    print(f"    Final: {final_analysis.get('recommendation','?')} ({final_analysis.get('conviction','?')} conviction)")
    print(f"    Agreements: {len(final_analysis.get('signal_agreements', []))}  Overrides: {len(final_analysis.get('signal_disagreements', []))}")
    print(f"    Chart order: {final_analysis.get('chart_order', [])}")

    print("  Generating charts...")
    charts = generate_all_charts(target_data, peer_data, comps, nlp_results, benchmarks)

    print("  Rendering report...")
    return render_report(ticker, target_data, peer_data, comps, nlp_results,
                         guidance, final_analysis, signal_feed, charts)


def main():
    print(f"Loading / updating data for {TICKER}...")
    try:
        data = fetch_with_cache(TICKER)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    html = run_pipeline(data)

    out = Path(f"test_{TICKER.lower()}_report.html")
    out.write_text(html, encoding="utf-8")
    print(f"\nReport written to {out} ({len(html):,} bytes)")

    import subprocess, platform
    if platform.system() == "Windows":
        os.startfile(str(out.resolve()))
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(out.resolve())])
    else:
        subprocess.run(["xdg-open", str(out.resolve())])

    print("Done.")


if __name__ == "__main__":
    main()
