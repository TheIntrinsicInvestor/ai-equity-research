import pickle
from pathlib import Path

_CACHE_DIR = Path(".finbert_cache")
_CACHE_DIR.mkdir(exist_ok=True)


def get_or_compute(ticker: str, period: str, compute_fn):
    """Return cached FinBERT result for (ticker, period) or call compute_fn and cache it."""
    key = f"{ticker.upper()}_{period.replace('/', '-').replace(' ', '_')}"
    path = _CACHE_DIR / f"{key}.pkl"
    if path.exists():
        try:
            return pickle.loads(path.read_bytes())
        except Exception:
            path.unlink(missing_ok=True)
    result = compute_fn()
    path.write_bytes(pickle.dumps(result))
    return result
