"""
Runs FinBERT sentiment analysis and topic categorization on earnings call transcripts.
Model is loaded once at module import and reused across all transcripts.

Sentiment score convention: score = positive_prob - negative_prob  →  range [-1, +1]
"""

import re
import time
import requests
import numpy as np
from pipeline.analysis.finbert_cache import get_or_compute

_sentiment_pipeline = None
_finbert_unavailable = False  # set True permanently after an OOM/load failure

_HF_API_URL = "https://router.huggingface.co/hf-inference/models/ProsusAI/finbert"
_HF_BATCH_SIZE = 10


def _hf_api_token() -> str:
    from config import HF_API_TOKEN
    return HF_API_TOKEN


def _infer_via_api(paragraphs: list[str]) -> list[list[dict]] | None:
    """Call HuggingFace Inference API; returns same shape as local pipeline output."""
    token = _hf_api_token()
    if not token:
        return None
    headers = {"Authorization": f"Bearer {token}"}
    all_outputs: list[list[dict]] = []
    for i in range(0, len(paragraphs), _HF_BATCH_SIZE):
        batch = paragraphs[i : i + _HF_BATCH_SIZE]
        payload = {"inputs": batch, "parameters": {"top_k": None}}
        for attempt in range(3):
            try:
                resp = requests.post(_HF_API_URL, headers=headers, json=payload, timeout=60)
                if resp.status_code == 503:
                    # Model cold-starting on HuggingFace — wait and retry
                    wait = resp.json().get("estimated_time", 20)
                    time.sleep(min(wait, 30))
                    continue
                resp.raise_for_status()
                result = resp.json()
                if not isinstance(result, list):
                    raise ValueError(f"Unexpected HF API response: {result}")
                # Single-item batches come back as a list[dict]; wrap to list[list[dict]]
                if result and isinstance(result[0], dict):
                    result = [result]
                all_outputs.extend(result)
                break
            except Exception:
                if attempt == 2:
                    return None
    return all_outputs if len(all_outputs) == len(paragraphs) else None


def _get_pipeline():
    global _sentiment_pipeline, _finbert_unavailable
    if _finbert_unavailable:
        return None
    if _sentiment_pipeline is None:
        try:
            from transformers import pipeline as hf_pipeline
            from config import FINBERT_MODEL
            _sentiment_pipeline = hf_pipeline(
                "text-classification",
                model=FINBERT_MODEL,
                tokenizer=FINBERT_MODEL,
                top_k=None,
                truncation=True,
                max_length=512,
            )
        except Exception:
            _finbert_unavailable = True
            return None
    return _sentiment_pipeline

TOPIC_KEYWORDS = {
    "Revenue": ["revenue", "sales", "growth", "top-line", "volumes", "demand", "bookings"],
    "Margins": ["margin", "gross profit", "ebitda", "cost", "expense", "efficiency", "profitability"],
    "Guidance": ["guidance", "outlook", "expect", "forecast", "next quarter", "full year", "target"],
    "Macro": ["macro", "economy", "inflation", "interest rate", "market conditions", "recession", "fx"],
    "Competition": ["competitor", "market share", "pricing", "competitive", "landscape", "rival"],
}


def _split_paragraphs(text: str, max_chars: int = 1800) -> list[str]:
    """Split transcript text into chunks small enough for FinBERT (≤512 tokens ≈ 1800 chars)."""
    raw = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks = []
    for para in raw:
        if len(para) <= max_chars:
            chunks.append(para)
        else:
            # Split long paragraphs by sentence
            sentences = re.split(r"(?<=[.!?])\s+", para)
            buf = ""
            for s in sentences:
                if len(buf) + len(s) + 1 > max_chars:
                    if buf:
                        chunks.append(buf.strip())
                    buf = s
                else:
                    buf = (buf + " " + s).strip()
            if buf:
                chunks.append(buf)
    return chunks


def _score_paragraph(label_scores: list[dict]) -> float:
    """Convert FinBERT label probability list to a signed [-1, +1] score."""
    prob = {d["label"].lower(): d["score"] for d in label_scores}
    return prob.get("positive", 0.0) - prob.get("negative", 0.0)


def _classify_topic(text: str) -> str | None:
    text_lower = text.lower()
    scores = {
        topic: sum(1 for kw in keywords if kw in text_lower)
        for topic, keywords in TOPIC_KEYWORDS.items()
    }
    best_topic = max(scores, key=scores.get)
    return best_topic if scores[best_topic] > 0 else None


def _analyze_single(transcript: dict) -> dict | None:
    text = transcript.get("text", "")
    if not text:
        return None

    paragraphs = _split_paragraphs(text)
    if not paragraphs:
        return None

    if _hf_api_token():
        raw_outputs = _infer_via_api(paragraphs)
        if raw_outputs is None:
            return None
    else:
        pipe = _get_pipeline()
        if pipe is None:
            return None
        raw_outputs = pipe(paragraphs)

    para_scores = []
    category_buckets: dict[str, list[float]] = {cat: [] for cat in TOPIC_KEYWORDS}

    for para, output in zip(paragraphs, raw_outputs):
        score = _score_paragraph(output)
        para_scores.append((len(para), score))
        topic = _classify_topic(para)
        if topic:
            category_buckets[topic].append(score)

    total_weight = sum(w for w, _ in para_scores)
    overall = sum(w * s for w, s in para_scores) / total_weight if total_weight else 0.0

    def _label(s: float) -> str:
        if s > 0.1:
            return "positive"
        if s < -0.1:
            return "negative"
        return "neutral"

    categories = {}
    for cat, scores in category_buckets.items():
        cat_score = float(np.mean(scores)) if scores else 0.0
        categories[cat] = {"score": cat_score, "label": _label(cat_score)}

    return {
        "period": transcript.get("period", ""),
        "filing_date": transcript.get("filing_date", ""),
        "overall_sentiment": round(overall, 4),
        "sentiment_label": _label(overall),
        "categories": categories,
    }


def analyze_transcripts(transcripts: list[dict]) -> list[dict]:
    import os
    # Skip if explicitly disabled AND no HF API token to fall back on
    if os.environ.get("DISABLE_FINBERT", "").lower() == "true" and not _hf_api_token():
        return []

    results = []
    for transcript in transcripts:
        ticker = transcript.get("ticker", "UNK")
        period = transcript.get("period") or transcript.get("filing_date", "unknown")

        cached = get_or_compute(
            ticker,
            period,
            lambda t=transcript: _analyze_single(t),
        )
        if cached is not None:
            results.append(cached)
    return results


