"""
Fetches daily price and return data from CRSP (crsp.dsf).
Handles PERMNO lookup and adjusted price calculation.
Also fetches SPY and a sector ETF as benchmarks via yfinance
(CRSP does not carry ETF data).
"""

import pandas as pd
import yfinance as yf
from datetime import date, timedelta
from config import HISTORY_YEARS


def _lookup_permno(conn, ticker: str) -> str | None:
    result = conn.raw_sql(
        """
        SELECT a.permno
        FROM crsp.stocknames a
        WHERE a.ticker = %(ticker)s
          AND a.nameenddt = (
              SELECT MAX(b.nameenddt)
              FROM crsp.stocknames b
              WHERE b.ticker = %(ticker)s
          )
        LIMIT 1
        """,
        params={"ticker": ticker.upper()},
    )
    if result.empty:
        return None
    return str(int(result.iloc[0]["permno"]))


def fetch_prices(conn, ticker: str, since: str | None = None) -> pd.DataFrame:
    permno = _lookup_permno(conn, ticker)
    if permno is None:
        return pd.DataFrame()

    default_cutoff = (date.today() - timedelta(days=HISTORY_YEARS * 365 + 30)).isoformat()
    cutoff = since or default_cutoff

    df = conn.raw_sql(
        """
        SELECT date, prc, ret, vol, shrout, cfacpr
        FROM crsp.dsf
        WHERE permno = %(permno)s
          AND date >= %(cutoff)s
        ORDER BY date ASC
        """,
        params={"permno": int(permno), "cutoff": cutoff},
    )
    if df.empty:
        return df

    df["date"] = pd.to_datetime(df["date"])
    df["prc"] = df["prc"].abs()  # CRSP uses negative prices for bid/ask midpoints
    df["cfacpr"] = df["cfacpr"].fillna(1.0)
    df["adj_price"] = df["prc"] / df["cfacpr"]
    df["ticker"] = ticker.upper()
    return df.set_index("date")


def fetch_benchmarks(conn, sector_etf: str) -> dict:
    """
    Returns daily price series for benchmarks indexed by date.
    Both SPY and sector ETF sourced from yfinance with 24h disk cache.
    conn is kept for backward compatibility but is unused.
    """
    import time, os, pickle

    start_str = (date.today() - timedelta(days=HISTORY_YEARS * 365 + 30)).strftime("%Y-%m-%d")
    benchmarks = {}

    cache_dir = os.path.join(os.path.dirname(__file__), "../../.benchmark_cache")
    os.makedirs(cache_dir, exist_ok=True)

    def _fetch_or_cache(symbol: str) -> pd.Series | None:
        cache_path = os.path.join(cache_dir, f"{symbol}.pkl")
        if os.path.exists(cache_path):
            try:
                if time.time() - os.path.getmtime(cache_path) < 86400:
                    with open(cache_path, "rb") as f:
                        return pickle.load(f)
            except Exception:
                pass
        for attempt in range(3):
            try:
                df = yf.Ticker(symbol).history(start=start_str)
                if not df.empty and "Close" in df.columns:
                    series = df["Close"].rename(symbol)
                    if series.index.tz is not None:
                        series.index = series.index.tz_localize(None)
                    with open(cache_path, "wb") as f:
                        pickle.dump(series, f)
                    return series
                if attempt < 2:
                    time.sleep(10)
            except Exception:
                if attempt < 2:
                    time.sleep(10)
        return None

    spy = _fetch_or_cache("SPY")
    if spy is not None:
        benchmarks["SPY"] = spy

    etf = _fetch_or_cache(sector_etf)
    if etf is not None:
        benchmarks[sector_etf] = etf

    return benchmarks
