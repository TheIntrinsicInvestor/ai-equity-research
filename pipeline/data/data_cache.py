"""
Per-ticker incremental disk cache for WRDS data.

First run: fetches full history, saves to .data_cache/<TICKER>/
Subsequent runs: loads cached data, fetches only rows newer than the
max cached date, merges, and saves back.

Layout:  .data_cache/<TICKER>/
           annual.pkl        Compustat funda rows (DataFrame)
           quarterly.pkl     Compustat fundq rows (DataFrame)
           prices.pkl        CRSP dsf daily prices (date-indexed DataFrame)
           eps_history.pkl   IBES statsum EPS time series (DataFrame)
           transcripts.json  EDGAR earnings releases (list of dicts with 'fname' key)
           meta.json         Company metadata and fetch timestamps
"""

import json
import pickle
import pandas as pd
from datetime import date, timedelta, datetime
from pathlib import Path

CACHE_ROOT = Path(".data_cache")


class TickerDataCache:
    def __init__(self, ticker: str):
        self.ticker = ticker.upper()
        self.dir = CACHE_ROOT / self.ticker
        self.dir.mkdir(parents=True, exist_ok=True)
        self._meta = self._load_meta()

    # ── meta ──────────────────────────────────────────────────────────────────

    def _load_meta(self) -> dict:
        p = self.dir / "meta.json"
        return json.loads(p.read_text()) if p.exists() else {}

    def _save_meta(self):
        (self.dir / "meta.json").write_text(
            json.dumps(self._meta, indent=2, default=str)
        )

    def get(self, key: str, default=None):
        return self._meta.get(key, default)

    def set(self, key: str, value):
        self._meta[key] = value
        self._save_meta()

    # ── DataFrames ────────────────────────────────────────────────────────────

    def _pkl(self, name: str) -> Path:
        return self.dir / f"{name}.pkl"

    def load_df(self, name: str) -> pd.DataFrame | None:
        p = self._pkl(name)
        if not p.exists():
            return None
        with p.open("rb") as f:
            return pickle.load(f)

    def save_df(self, name: str, df: pd.DataFrame):
        with self._pkl(name).open("wb") as f:
            pickle.dump(df, f)
        self._meta[f"{name}_saved"] = datetime.utcnow().isoformat()
        self._save_meta()

    def since_date(self, name: str, col: str | None = None,
                   overlap_days: int = 0, use_index: bool = False) -> str | None:
        """
        Returns the incremental-fetch cutoff: max cached date minus overlap_days.
        Returns None when no cache exists, which triggers a full history fetch.
        """
        df = self.load_df(name)
        if df is None or df.empty:
            return None
        val = df.index.max() if use_index else df[col].max()
        if pd.isnull(val):
            return None
        d = date.fromisoformat(str(val)[:10]) - timedelta(days=overlap_days)
        return d.isoformat()

    def merge_col_df(self, name: str, new_df: pd.DataFrame,
                     date_col: str) -> pd.DataFrame:
        """Merge a column-dated DataFrame (Compustat, IBES) with cached data."""
        cached = self.load_df(name)
        if cached is None or cached.empty:
            combined = new_df
        else:
            combined = pd.concat([cached, new_df])
            # Normalize date column — cached pickle and fresh fetch can have
            # mismatched types (e.g. str vs Timestamp) after incremental runs.
            combined[date_col] = pd.to_datetime(combined[date_col], errors="coerce")
            combined = (combined
                        .drop_duplicates(subset=[date_col], keep="last")
                        .sort_values(date_col)
                        .reset_index(drop=True))
        self.save_df(name, combined)
        return combined

    def merge_index_df(self, name: str, new_df: pd.DataFrame) -> pd.DataFrame:
        """Merge a date-indexed DataFrame (CRSP prices) with cached data."""
        cached = self.load_df(name)
        if cached is None or cached.empty:
            combined = new_df
        else:
            combined = pd.concat([cached, new_df])
            combined = combined[~combined.index.duplicated(keep="last")].sort_index()
        self.save_df(name, combined)
        return combined

    # ── JSON ──────────────────────────────────────────────────────────────────

    def load_json(self, name: str):
        p = self.dir / f"{name}.json"
        return json.loads(p.read_text()) if p.exists() else None

    def save_json(self, name: str, obj):
        (self.dir / f"{name}.json").write_text(
            json.dumps(obj, indent=2, default=str)
        )
        self._meta[f"{name}_saved"] = datetime.utcnow().isoformat()
        self._save_meta()
