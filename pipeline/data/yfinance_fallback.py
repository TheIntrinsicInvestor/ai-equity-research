"""
Fetches current price and market cap.
Priority: yfinance live → disk cache (24h) → CRSP latest (any age).
CRSP on this account lags by months, so yfinance is always attempted first.
"""

import os
import pickle
import time
import pandas as pd
from datetime import date, timedelta
import yfinance as yf

_CACHE_DIR = os.path.join(os.path.dirname(__file__), "../../.price_cache")


def _load_cache(ticker: str) -> dict | None:
    path = os.path.join(_CACHE_DIR, f"{ticker.upper()}.pkl")
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < 86400:
        try:
            with open(path, "rb") as f:
                return pickle.load(f)
        except Exception:
            pass
    return None


def _save_cache(ticker: str, data: dict) -> None:
    os.makedirs(_CACHE_DIR, exist_ok=True)
    try:
        with open(os.path.join(_CACHE_DIR, f"{ticker.upper()}.pkl"), "wb") as f:
            pickle.dump(data, f)
    except Exception:
        pass


def fetch_current_price(ticker: str, crsp_prices: pd.DataFrame) -> dict:
    today = date.today()

    # 1. yfinance — most current price
    try:
        info = yf.Ticker(ticker).info
        price = (info.get("currentPrice")
                 or info.get("regularMarketPrice")
                 or info.get("previousClose"))
        market_cap = info.get("marketCap")
        if market_cap is None and price:
            shares = info.get("sharesOutstanding")
            if shares:
                market_cap = price * shares
        if price is not None:
            result = {"price": price, "market_cap": market_cap,
                      "as_of_date": str(today), "source": "yfinance"}
            _save_cache(ticker, result)
            return result
    except Exception:
        pass

    # 2. Disk cache from last successful yfinance fetch
    cached = _load_cache(ticker)
    if cached and cached.get("price"):
        return cached

    # 3. CRSP latest — stale but non-zero (better than showing $0.00)
    if not crsp_prices.empty:
        latest = crsp_prices.iloc[-1]
        latest_date = crsp_prices.index.max().date()
        if pd.notna(latest.get("adj_price")) and pd.notna(latest.get("shrout")):
            price = float(latest["adj_price"])
            mktcap = float(latest["adj_price"] * latest["shrout"] * 1000)
            if price > 0:
                return {"price": price, "market_cap": mktcap,
                        "as_of_date": str(latest_date), "source": "crsp"}

    return {"price": None, "market_cap": None, "as_of_date": str(today), "source": "none"}


def supplement_prices(ticker: str, crsp_df: pd.DataFrame) -> pd.DataFrame:
    """
    Append recent yfinance daily prices when CRSP lags behind the 6-month
    momentum window. Only fetches if the latest CRSP date is >30 days old.
    Returns the original DataFrame unchanged on any yfinance failure.
    """
    if crsp_df.empty:
        start = (date.today() - timedelta(days=365)).isoformat()
    else:
        last_crsp = crsp_df.index.max().date()
        if last_crsp >= date.today() - timedelta(days=30):
            return crsp_df  # CRSP is recent enough
        start = (last_crsp + timedelta(days=1)).isoformat()

    try:
        hist = yf.Ticker(ticker).history(start=start, auto_adjust=True)
        if hist.empty or "Close" not in hist.columns:
            return crsp_df

        idx = hist.index.tz_localize(None) if hist.index.tz is not None else hist.index
        new_rows = pd.DataFrame({
            "prc":      hist["Close"].values,
            "adj_price": hist["Close"].values,
            "ret":      hist["Close"].pct_change().values,
            "vol":      hist["Volume"].values,
            "shrout":   float("nan"),
            "cfacpr":   1.0,
            "ticker":   ticker.upper(),
        }, index=idx)

        combined = pd.concat([crsp_df, new_rows])
        combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        return combined
    except Exception:
        return crsp_df
