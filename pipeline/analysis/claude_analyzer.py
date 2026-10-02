"""
Claude analysis components:
1. extract_guidance       — extracts forward-looking statements from earnings transcripts (Sonnet, prompt-cached).
2. form_initial_thesis    — Pass 1: Sonnet forms an independent thesis via agentic tool loop (no score in context).
3. reconcile_and_finalize — Pass 2: Opus reconciles the initial thesis against mechanical signals (single call).
"""

import hashlib
import json
import os
import re
import pandas as pd
import anthropic
from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_ANALYSIS_MODEL

_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

_GUIDANCE_CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".guidance_cache")


def _parse_guidance_json(raw: str) -> list:
    """Extract the first JSON array from a possibly prose/fence-wrapped response (review 1.16)."""
    m = re.search(r"\[.*\]", raw, re.DOTALL)
    if not m:
        return []
    try:
        parsed = json.loads(m.group(0))
        return parsed if isinstance(parsed, list) else []
    except json.JSONDecodeError:
        return []

_SECTOR_LABELS = {
    "10": "Energy", "15": "Materials", "20": "Industrials",
    "25": "Consumer Discretionary", "30": "Consumer Staples", "35": "Health Care",
    "40": "Financials", "45": "Information Technology",
    "50": "Communication Services", "55": "Utilities", "60": "Real Estate",
}

_SECTOR_VALUATION_NOTES = {
    "40": (
        "Sector note: For Financials, EV/EBITDA is not applicable — bank debt is their product "
        "(deposits/liabilities), not leverage. Focus on P/E and P/S. Use ROE and ROA, "
        "not ROIC, to assess capital efficiency. Net interest margin and loan-loss provisions "
        "are key drivers."
    ),
    "60": (
        "Sector note: For Real Estate/REITs, EBITDA is distorted by depreciation — the standard "
        "metric is FFO (Funds From Operations) or AFFO. P/E will also look stretched due to "
        "high D&A charges. Focus on P/S and dividend yield."
    ),
    "55": (
        "Sector note: For Utilities, low single-digit revenue growth is normal under regulatory "
        "rate structures — do not penalise stable growth as deterioration. Focus on dividend "
        "sustainability, rate base growth, and regulatory environment."
    ),
}

_DEFAULT_CHART_ORDER = ["price", "relative_perf", "revenue_eps", "margins", "comps", "estimates", "sentiment"]


# ── Tool definitions ──────────────────────────────────────────────────────────

_DATA_TOOLS = [
    {
        "name": "get_financials",
        "description": (
            "Get annual revenue, EBITDA, net income, EPS, gross margin, free cash flow (FCF), "
            "and net debt trends for the target company over the past 5 years. "
            "Also returns latest net debt/EBITDA leverage ratio."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_comps_table",
        "description": (
            "Get comparable company valuation multiples: EV/EBITDA, P/E, P/S for the target "
            "vs peer median, 25th/75th percentile, and individual peers."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_sentiment_scores",
        "description": (
            "Get FinBERT quantitative sentiment scores from earnings call transcripts. "
            "Includes overall score [-1,+1] and breakdown by category "
            "(Revenue, Margins, Guidance, Macro, Competition) across recent quarters."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_estimates",
        "description": (
            "Get sell-side analyst consensus: mean NTM EPS estimate, mean price target, "
            "and buy/hold/sell rating distribution."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "get_guidance",
        "description": (
            "Get specific forward-looking statements extracted from the most recent earnings call, "
            "including metric, value/range, period, and verbatim quote."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]

_SUBMIT_INITIAL_THESIS_TOOL = {
    "name": "submit_initial_thesis",
    "description": (
        "Submit your initial investment thesis formed from the data. "
        "Call this when you have gathered sufficient data to form a view."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"],
                "description": "Your investment recommendation based solely on the data you've examined.",
            },
            "conviction": {
                "type": "string",
                "enum": ["high", "medium", "low"],
                "description": "Confidence in your recommendation given data quality and coverage.",
            },
            "headline": {
                "type": "string",
                "description": "One sentence capturing the core thesis.",
            },
            "thesis": {
                "type": "string",
                "description": "3-4 sentences. Evidence-based. Reference specific figures.",
            },
            "key_evidence": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "signal": {"type": "string"},
                        "direction": {"type": "string", "enum": ["positive", "negative", "neutral"]},
                        "rationale": {"type": "string", "description": "One sentence."},
                    },
                    "required": ["signal", "direction", "rationale"],
                },
                "description": "2-4 signals that most drive your view.",
            },
            "key_risks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 2-4 risks, specific and quantitative where possible.",
            },
            "catalysts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 2-3 near-term catalysts that could re-rate the stock.",
            },
        },
        "required": ["recommendation", "conviction", "headline", "thesis", "key_evidence", "key_risks", "catalysts"],
    },
}

_SUBMIT_FINAL_ANALYSIS_TOOL = {
    "name": "submit_final_analysis",
    "description": "Submit the final reconciled investment analysis.",
    "input_schema": {
        "type": "object",
        "properties": {
            "recommendation": {
                "type": "string",
                "enum": ["Strong Buy", "Buy", "Hold", "Sell", "Strong Sell"],
            },
            "conviction": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
            "headline": {
                "type": "string",
                "description": "One sentence capturing the final thesis.",
            },
            "investment_thesis": {
                "type": "string",
                "description": (
                    "4-6 sentences. Institutional research note style. State the investment case "
                    "directly and authoritatively: what the company is, why it is attractive or "
                    "unattractive now, the key valuation and fundamental drivers, and one forward "
                    "catalyst or risk that defines the setup. Reference specific figures. "
                    "Do not describe your reasoning process, reference 'initial views', or explain "
                    "what was 'maintained' or 'revised'. Write as published research. "
                    "Do not use em-dashes (—) or semicolons (;)."
                ),
            },
            "valuation_commentary": {
                "type": "string",
                "description": "1-2 sentences on relative valuation vs peers. Reference specific multiples.",
            },
            "sentiment_interpretation": {
                "type": "string",
                "description": (
                    "Qualitative interpretation of the sentiment data — "
                    "what does it mean in the context of this company's narrative?"
                ),
            },
            "signal_agreements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "signal": {"type": "string", "description": "Signal name from the quantitative breakdown."},
                        "rationale": {"type": "string", "description": "One sentence explaining why you agree."},
                    },
                    "required": ["signal", "rationale"],
                },
                "description": "Quantitative signals where your qualitative view agrees with the mechanical direction.",
            },
            "signal_disagreements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "signal": {"type": "string"},
                        "mechanical_view": {"type": "string", "description": "What the mechanical signal says."},
                        "claude_view": {"type": "string", "description": "What your qualitative view says instead."},
                        "rationale": {"type": "string", "description": "Why the mechanical signal is misleading or outweighed."},
                    },
                    "required": ["signal", "mechanical_view", "claude_view", "rationale"],
                },
                "description": "Signals where your qualitative view differs from the mechanical direction.",
            },
            "chart_order": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Preferred order to surface charts as evidence for your thesis. "
                    "Available keys: price, relative_perf, revenue_eps, margins, comps, estimates, sentiment. "
                    "List all 7 in your preferred order."
                ),
            },
            "key_risks": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 2-4 risks, specific and quantitative where possible.",
            },
            "catalysts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Top 2-3 near-term catalysts.",
            },
        },
        "required": [
            "recommendation", "conviction", "headline", "investment_thesis",
            "valuation_commentary", "sentiment_interpretation",
            "signal_agreements", "signal_disagreements", "chart_order",
            "key_risks", "catalysts",
        ],
    },
}


# ── Data serialisers ──────────────────────────────────────────────────────────

def _ser_financials(financials: dict) -> dict:
    annual = financials.get("annual")
    if annual is None or annual.empty:
        return {"error": "No annual data available"}
    rows = []
    for _, row in annual.iterrows():
        sale   = row.get("sale")
        ebitda = row.get("ebitda")
        ni     = row.get("ni")
        gp     = row.get("gp")
        fcf    = row.get("fcf")
        nd     = row.get("net_debt")
        rows.append({
            "year": int(row["fyear"]) if pd.notna(row.get("fyear")) else None,
            "revenue_bn": round(float(sale) / 1000, 2) if pd.notna(sale) else None,
            "ebitda_bn": round(float(ebitda) / 1000, 2) if pd.notna(ebitda) else None,
            "net_income_bn": round(float(ni) / 1000, 2) if pd.notna(ni) else None,
            "eps": round(float(row["epspx"]), 2) if pd.notna(row.get("epspx")) else None,
            "gross_margin_pct": round(float(gp) / float(sale) * 100, 1) if pd.notna(gp) and pd.notna(sale) and float(sale) > 0 else None,
            "fcf_bn": round(float(fcf) / 1000, 2) if pd.notna(fcf) else None,
            "net_debt_bn": round(float(nd) / 1000, 2) if pd.notna(nd) else None,
        })

    # Latest-year balance sheet summary for quick reference
    latest = annual.iloc[-1]
    ebitda_latest = latest.get("ebitda")
    nd_latest     = latest.get("net_debt")
    nd_ebitda = None
    if pd.notna(nd_latest) and pd.notna(ebitda_latest) and float(ebitda_latest) > 0:
        nd_ebitda = round(float(nd_latest) / float(ebitda_latest), 2)

    return {
        "annual": rows,
        "currency": "USD millions, revenue/EBITDA/NI/FCF/net_debt in billions",
        "latest_net_debt_bn": round(float(nd_latest) / 1000, 2) if pd.notna(nd_latest) else None,
        "latest_net_debt_to_ebitda": nd_ebitda,
    }


def _ser_comps(comps: dict) -> dict:
    def _round(v):
        if v is None:
            return None
        try:
            return round(float(v), 1)
        except (TypeError, ValueError):
            return v

    _MULTIPLE_KEYS = {"ev_ebitda", "pe", "ps"}
    stats = comps.get("peer_stats", {})
    target = comps.get("target", {})
    return {
        "target": {k: _round(v) for k, v in target.items() if k in _MULTIPLE_KEYS or k == "ticker"},
        "peer_median": {m: _round(stats.get(m, {}).get("median")) for m in ["ev_ebitda", "pe", "ps"]},
        "peer_25th_75th": {
            m: {"p25": _round(stats.get(m, {}).get("p25")), "p75": _round(stats.get(m, {}).get("p75"))}
            for m in ["ev_ebitda", "pe", "ps"]
        },
        "peers": [
            {k: _round(v) for k, v in p.items() if k in _MULTIPLE_KEYS or k == "ticker"}
            for p in comps.get("peers", [])
        ],
    }


def _ser_sentiment(nlp_results: list) -> dict:
    if not nlp_results:
        return {"error": "No sentiment data available"}
    return {
        "periods": [
            {
                "period": r.get("period", ""),
                "overall_score": r["overall_sentiment"],
                "label": r["sentiment_label"],
                "categories": {
                    cat: {"score": round(data["score"], 3), "label": data["label"]}
                    for cat, data in r["categories"].items()
                },
            }
            for r in nlp_results
        ]
    }


def _ser_estimates(estimates: dict) -> dict:
    return {
        "mean_eps_ntm": estimates.get("mean_eps"),
        "mean_price_target": estimates.get("mean_pt"),
        "pct_buy": estimates.get("pct_buy"),
        "pct_hold": estimates.get("pct_hold"),
        "pct_sell": estimates.get("pct_sell"),
        "num_eps_analysts": estimates.get("num_analysts"),
        "num_raters": estimates.get("num_raters"),
    }


# ── Guidance extraction (Sonnet + prompt caching) ────────────────────────────

def extract_guidance(transcripts: list[dict]) -> list[dict]:
    """Extract forward-looking management guidance from the most recent transcript."""
    if not transcripts:
        return []
    latest = transcripts[-1]
    text = latest.get("text", "")
    if not text:
        return []

    key = hashlib.sha256(text[:12000].encode("utf-8", errors="ignore")).hexdigest()[:24]
    cache_path = os.path.join(_GUIDANCE_CACHE_DIR, f"{key}.json")
    if os.path.exists(cache_path):
        with open(cache_path, encoding="utf-8") as f:
            return json.load(f)

    system_text = (
        "You are analyzing an earnings call transcript. "
        "Extract all specific forward-looking statements where management gives a quantitative "
        "target, range, or clear directional guidance about future performance. "
        "Return only a JSON array with objects: "
        '{"metric","value","period","speaker","verbatim_quote" (max 150 chars)}. '
        "Return [] if none found."
    )

    try:
        message = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=2048,
            system=system_text,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": f"Transcript (period: {latest.get('period', 'unknown')}):\n\n{text[:12000]}",
                            "cache_control": {"type": "ephemeral"},
                        },
                        {
                            "type": "text",
                            "text": "Extract forward-looking guidance as JSON.",
                        },
                    ],
                }
            ],
        )
        items = _parse_guidance_json(message.content[0].text.strip())
        # Deduplicate by (metric, period): keep the entry with the longest quote
        seen: dict[tuple, dict] = {}
        for item in items:
            key = (str(item.get("metric", "")).lower().strip(), str(item.get("period", "")).strip())
            existing = seen.get(key)
            if existing is None or len(str(item.get("verbatim_quote", ""))) > len(str(existing.get("verbatim_quote", ""))):
                seen[key] = item
        os.makedirs(_GUIDANCE_CACHE_DIR, exist_ok=True)
        result = list(seen.values())
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(result, f)
        return result
    except Exception:
        return []


# ── Pass 1: Independent thesis (Sonnet) ───────────────────────────────────────

def form_initial_thesis(
    ticker: str,
    target_data: dict,
    comps: dict,
    nlp_results: list,
    guidance: list,
) -> dict:
    """
    Pass 1: Sonnet forms an independent thesis via agentic tool loop.
    No score in context — prevents anchoring.
    Returns InitialThesis dict, or {} on failure.
    """
    financials = target_data.get("financials", {})
    estimates = target_data.get("estimates", {})
    company_name = financials.get("company_name", ticker)
    sector = _SECTOR_LABELS.get(str(financials.get("gsector", "")), "")
    sector_note = _SECTOR_VALUATION_NOTES.get(str(financials.get("gsector", "")), "")

    tool_handlers = {
        "get_financials":       lambda _: _ser_financials(financials),
        "get_comps_table":      lambda _: _ser_comps(comps),
        "get_sentiment_scores": lambda _: _ser_sentiment(nlp_results),
        "get_estimates":        lambda _: _ser_estimates(estimates),
        "get_guidance":         lambda _: {"guidance_items": guidance[:6]},
    }

    system_prompt = (
        f"You are a senior sell-side equity research analyst covering {sector}. "
        "Use the available data tools to build your investment view, then submit it via submit_initial_thesis.\n\n"
        "Framework:\n"
        "1. Get financials and comps — assess valuation, growth trajectory, FCF generation, and balance sheet leverage.\n"
        "2. Get sentiment scores — understand management tone and market dynamics.\n"
        "3. Get guidance and estimates — cross-reference forward expectations.\n"
        "4. Submit your thesis. Be specific — reference actual figures.\n\n"
        "Balance sheet note: The financials tool returns FCF (free cash flow = operating cash flow minus capex) "
        "and net debt for each year. For capital-intensive businesses, FCF yield and net debt/EBITDA are often "
        "more informative than earnings-based multiples. Reference these where material.\n\n"
        + (f"{sector_note}\n\n" if sector_note else "")
        + "Important: Form your view from the data alone. "
        "Do not reference any pre-computed scores or external rankings. "
        "Do not use em-dashes (—) or semicolons (;) in any text output."
    )

    messages = [
        {
            "role": "user",
            "content": (
                f"Analyze {company_name} ({ticker}) using the available tools, "
                "then submit your initial investment thesis."
            ),
        }
    ]

    # Cache system prompt + tools across the up-to-8 loop iterations (and across
    # reports within the 5-min TTL). cache_control on the LAST tool caches the
    # whole tools array prefix.
    pass1_tools = [dict(t) for t in _DATA_TOOLS] + [dict(_SUBMIT_INITIAL_THESIS_TOOL)]
    pass1_tools[-1]["cache_control"] = {"type": "ephemeral"}
    system_blocks = [{"type": "text", "text": system_prompt, "cache_control": {"type": "ephemeral"}}]

    for _ in range(8):
        response = _client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4096,
            system=system_blocks,
            tools=pass1_tools,
            messages=messages,
        )

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_initial_thesis":
                return block.input

        if response.stop_reason == "end_turn":
            break

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if getattr(block, "type", None) == "tool_use" and block.name in tool_handlers:
                    result = tool_handlers[block.name](block.input)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": json.dumps(result, default=str),
                    })
            if tool_results:
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})
        else:
            break

    return {}


# ── Pass 2: Reconciliation (Opus) ─────────────────────────────────────────────

def reconcile_and_finalize(
    ticker: str,
    initial_thesis: dict,
    signal_feed: dict,
) -> dict:
    """
    Pass 2: Opus reconciles the initial thesis against mechanical signals.
    Single API call — no tool loop.
    Falls back to a FinalAnalysis derived from InitialThesis if the call fails.
    """
    # Format initial thesis block
    evidence_lines = "\n".join(
        f"  - {e['signal']} ({e['direction']}): {e['rationale']}"
        for e in initial_thesis.get("key_evidence", [])
    )
    risks_lines = "\n".join(f"  - {r}" for r in initial_thesis.get("key_risks", []))
    catalysts_lines = "\n".join(f"  - {c}" for c in initial_thesis.get("catalysts", []))

    thesis_block = (
        f"INITIAL VIEW (formed independently from company data)\n"
        f"Recommendation: {initial_thesis.get('recommendation', 'Hold')} "
        f"({initial_thesis.get('conviction', 'medium')} conviction)\n"
        f"Headline: {initial_thesis.get('headline', '')}\n"
        f"Thesis: {initial_thesis.get('thesis', '')}\n"
        f"Key Evidence:\n{evidence_lines}\n"
        f"Key Risks:\n{risks_lines}\n"
        f"Catalysts:\n{catalysts_lines}"
    )

    # Format signal feed block
    signal_lines = "\n".join(
        f"  {s['name']}: {s['value']} [{s['direction']}, {s['points']:+d} pts]"
        for s in signal_feed.get("signals", [])
    )
    signal_block = (
        f"QUANTITATIVE SIGNALS (Rules-Based System)\n"
        f"Mechanical Score: {signal_feed.get('mechanical_score', 0):+d} "
        f"→ {signal_feed.get('mechanical_recommendation', 'Hold')}\n\n"
        f"Signal breakdown:\n{signal_lines}"
    )

    user_message = (
        f"Ticker: {ticker}\n\n"
        f"{thesis_block}\n\n"
        f"{signal_block}\n\n"
        "Reconcile your initial view with these quantitative signals. "
        "For each signal, add it to signal_agreements if your qualitative view aligns, "
        "or signal_disagreements if you think the mechanical direction is misleading, "
        "an accounting artefact, or outweighed by other factors. "
        "Write investment_thesis as a clean institutional research note — state the final "
        "investment case directly using specific figures. Do not describe the reconciliation "
        "process or reference your initial view in the thesis text. "
        "Submit via submit_final_analysis."
    )

    system_prompt = (
        "You are a senior equity research analyst. You have formed an initial investment thesis "
        "from the company data. Now reconcile it against the quantitative signals from a rules-based system.\n\n"
        "Your job is genuine synthesis — not validation of the mechanical score. "
        "If the mechanical score is bullish but your qualitative read is cautious, say so explicitly. "
        "If the mechanical score penalises a premium multiple that is structurally justified "
        "(e.g. dominant market position, exceptional ROIC trend), challenge it with specific rationale.\n\n"
        "Price target tension: The signal feed includes analyst consensus data. "
        "If the consensus price target implies more than 10% upside from the current price but you are "
        "issuing a Hold (or if it implies more than 10% downside but you are issuing a Buy), "
        "your investment_thesis must explicitly explain why: e.g. the upside is insufficient compensation "
        "for execution risk, the target is stale or based on optimistic assumptions, or the risk-reward "
        "is asymmetric to the downside. Do not leave this tension unaddressed.\n\n"
        "The final recommendation reflects your considered view, not an average of the two inputs. "
        "Do not use em-dashes (—) or semicolons (;) in any text output."
    )

    try:
        response = _client.messages.create(
            model=CLAUDE_ANALYSIS_MODEL,
            max_tokens=4096,
            system=system_prompt,
            tools=[_SUBMIT_FINAL_ANALYSIS_TOOL],
            messages=[{"role": "user", "content": user_message}],
        )

        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_final_analysis":
                return block.input
    except Exception:
        pass

    # Fallback: derive FinalAnalysis from InitialThesis
    return {
        "recommendation": initial_thesis.get("recommendation", "Hold"),
        "conviction": initial_thesis.get("conviction", "medium"),
        "headline": initial_thesis.get("headline", ""),
        "investment_thesis": initial_thesis.get("thesis", ""),
        "valuation_commentary": "",
        "sentiment_interpretation": "",
        "signal_agreements": [],
        "signal_disagreements": [],
        "chart_order": _DEFAULT_CHART_ORDER,
        "key_risks": initial_thesis.get("key_risks", []),
        "catalysts": initial_thesis.get("catalysts", []),
    }
