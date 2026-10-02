"""
Closes the outcomes audit loop (review 5.3): joins outcomes.jsonl against
current prices and SPY to show how recommendations have performed since logging.

Run: python audit_outcomes.py
"""
import os

import pandas as pd
import yfinance as yf

PATH = os.path.join(os.environ.get("OUTCOMES_DIR") or os.path.dirname(__file__), "outcomes.jsonl")


def main() -> None:
    if not os.path.exists(PATH):
        print(f"No outcomes file at {PATH} — generate some reports first.")
        return
    df = pd.read_json(PATH, lines=True)
    if df.empty:
        print("outcomes.jsonl is empty.")
        return

    tickers = sorted(df["ticker"].unique().tolist())
    quotes = yf.download(tickers + ["SPY"], period="1d", progress=False)["Close"].iloc[-1]
    spy_hist = yf.download("SPY", start=df["date"].min(), progress=False)["Close"]

    rows = []
    for _, r in df.iterrows():
        entry_price = r.get("current_price")
        now_price = float(quotes.get(r["ticker"])) if r["ticker"] in quotes.index else None
        if not entry_price or not now_price:
            continue
        stock_ret = now_price / entry_price - 1
        spy_at_entry = spy_hist[spy_hist.index >= pd.Timestamp(r["date"])]
        spy_ret = (float(spy_hist.iloc[-1]) / float(spy_at_entry.iloc[0]) - 1) if len(spy_at_entry) else None
        rows.append({
            "ticker": r["ticker"], "date": r["date"], "rec": r["recommendation"],
            "conviction": r.get("conviction"), "stock_ret": round(stock_ret * 100, 1),
            "vs_spy": round((stock_ret - spy_ret) * 100, 1) if spy_ret is not None else None,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        print("No joinable outcomes (missing prices).")
        return
    print(out.to_string(index=False))
    print("\nBy recommendation (mean excess return vs SPY, %):")
    print(out.groupby("rec")["vs_spy"].agg(["count", "mean"]).round(1).to_string())


if __name__ == "__main__":
    main()
