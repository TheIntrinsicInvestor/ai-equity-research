import os
import secrets
from dotenv import load_dotenv

load_dotenv()

WRDS_USERNAME = os.environ.get("WRDS_USERNAME", "")
WRDS_PASSWORD = os.environ.get("WRDS_PASSWORD", "")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "equity-research-platform")
# Per-process random fallback: sessions reset on restart, but cookies are never
# forgeable via a key that's checked into the repo (review 1.2/4.3).
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

FINBERT_MODEL = "ProsusAI/finbert"
CLAUDE_MODEL = "claude-sonnet-4-6"          # guidance extraction (fast, cheap)
CLAUDE_ANALYSIS_MODEL = "claude-opus-4-7"   # main analysis agent (reasoning-grade)

HISTORY_YEARS = 5
TRANSCRIPT_COUNT = 5

MIN_PEERS = 5
MAX_PEERS = 10

# Maps GICS sector codes to a representative ETF ticker for relative performance charting
GICS_SECTOR_ETF = {
    "10": "XLE",   # Energy
    "15": "XLB",   # Materials
    "20": "XLI",   # Industrials
    "25": "XLY",   # Consumer Discretionary
    "30": "XLP",   # Consumer Staples
    "35": "XLV",   # Health Care
    "40": "XLF",   # Financials
    "45": "XLK",   # Information Technology
    "50": "XLC",   # Communication Services
    "55": "XLU",   # Utilities
    "60": "XLRE",  # Real Estate
}
