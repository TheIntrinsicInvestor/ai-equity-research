# Two-Pass Claude Architecture Design

*Date: 2026-05-13*
*Status: Approved — pending implementation*

## Problem

The current pipeline computes a rules-based score first, then has Claude write a thesis and apply a small overlay adjustment. This means:
- Claude's narrative is formed somewhat independently of the score
- The score, thesis, and charts are three outputs that happen to appear in the same document but don't reinforce each other
- Claude cannot genuinely challenge the mechanical score — it only nudges it by ±3

## Goal

Claude is the main brain. It forms an independent thesis from raw data, then reconciles against mechanical signals. The final report tells a unified story where the thesis leads, charts serve as evidence, and the mechanical score is visible as supporting data — not the headline verdict.

## Architecture

```
orchestrator.py
    │
    ├── 1. Data fetch (unchanged)
    │
    ├── 2. scoring_engine.score_company()
    │       └── format_signal_feed() → SignalFeed
    │           (structured signals + mechanical score — not the verdict)
    │
    ├── 3. Pass 1 — form_initial_thesis() [Sonnet 4.6]
    │       └── Agentic tool loop: get_financials, get_comps,
    │           get_sentiment, get_estimates, get_guidance
    │       └── submit_initial_thesis → InitialThesis
    │           (no score in context — independent view)
    │
    ├── 4. Pass 2 — reconcile_and_finalize() [Opus 4.7]
    │       └── No data tools — single API call
    │       └── Context: InitialThesis + SignalFeed
    │       └── submit_final_analysis → FinalAnalysis
    │
    └── 5. render_report(FinalAnalysis, SignalFeed, charts in chart_order)
```

## Data Schemas

### SignalFeed
Output of `format_signal_feed(score_result)`. Passed to Pass 2 and the renderer.

```python
{
    "signals": [
        {
            "name": str,           # e.g. "EV/EBITDA vs peers"
            "value": str,          # e.g. "2.1x above median"
            "direction": "positive" | "negative" | "neutral",
            "points": int          # raw points this signal contributed
        }
    ],
    "mechanical_score": int,
    "mechanical_recommendation": str   # e.g. "Hold"
}
```

### InitialThesis
Output of Pass 1 via `submit_initial_thesis` tool.

```python
{
    "recommendation": "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell",
    "conviction": "high" | "medium" | "low",
    "headline": str,          # one sentence — the thesis in plain English
    "thesis": str,            # 3–4 sentences, evidence-based
    "key_evidence": [         # 2–4 signals Claude found most compelling
        {
            "signal": str,
            "direction": "positive" | "negative" | "neutral",
            "rationale": str  # one sentence
        }
    ],
    "key_risks": [str],       # 2–4 items
    "catalysts": [str]        # 2–4 items
}
```

### FinalAnalysis
Output of Pass 2 via `submit_final_analysis` tool.

```python
{
    "recommendation": "Strong Buy" | "Buy" | "Hold" | "Sell" | "Strong Sell",
    "conviction": "high" | "medium" | "low",
    "headline": str,
    "investment_thesis": str,        # full narrative, 4–6 sentences
    "valuation_commentary": str,     # one paragraph on multiple/price
    "sentiment_interpretation": str,
    "signal_agreements": [           # mechanical signals Claude accepts
        {
            "signal": str,           # must match a name in SignalFeed
            "rationale": str
        }
    ],
    "signal_disagreements": [        # where Claude's view differs from mechanical
        {
            "signal": str,
            "mechanical_view": str,  # what the score said
            "claude_view": str,      # what Claude thinks instead
            "rationale": str
        }
    ],
    "chart_order": [str],            # ordered list of chart keys
    "key_risks": [str],
    "catalysts": [str]
}
```

## Component Changes

### `scoring_engine.py`
- Add `format_signal_feed(score_result) -> dict`
- Extracts per-signal breakdown already computed in `score_company()`
- Returns as `SignalFeed` dict
- `score_company()` itself unchanged

### `claude_analyzer.py`
- `extract_guidance()` — unchanged
- `analyze_company()` — deleted
- `synthesize_narrative()` — deleted (was already a legacy shim)
- **New** `form_initial_thesis(ticker, target_data, comps, nlp_results, estimates, guidance) -> dict`
  - Model: Sonnet 4.6
  - Agentic tool loop, max 8 iterations
  - Tools: `get_financials`, `get_comps_table`, `get_sentiment_scores`, `get_estimates`, `get_guidance`
  - Submit tool: `submit_initial_thesis`
  - System prompt: form an independent view from the data; no score or mechanical signals in context
- **New** `reconcile_and_finalize(ticker, initial_thesis, signal_feed) -> dict`
  - Model: Opus 4.7
  - Single API call, no tool loop
  - Context: Pass 1 `InitialThesis` + `SignalFeed` as structured text
  - Submit tool: `submit_final_analysis`
  - System prompt: you have formed an initial view and here are the mechanical signals — where do you agree, where do you disagree and why; final recommendation must explicitly engage with Pass 1 thesis

### `orchestrator.py`
- After scoring: `signal_feed = format_signal_feed(score_result)`
- Replace `analyze_company()` with sequential calls:
  1. `initial_thesis = form_initial_thesis(...)`
  2. `final_analysis = reconcile_and_finalize(ticker, initial_thesis, signal_feed)`
- Pass `final_analysis` and `signal_feed` to renderer
- Remove `qualitative_score_adjustment` overlay logic — Claude's recommendation is now the direct verdict

### `report_renderer.py`
Report structure reordered:
1. Headline + recommendation badge + conviction
2. Investment thesis
3. Signal reconciliation table — agreements (green) / disagreements (amber with Claude's rationale)
4. Charts in `chart_order` sequence (fallback to default order if key missing)
5. Valuation commentary + sentiment interpretation
6. Full mechanical signal table (supporting data)
7. Risks / catalysts

## Error Handling

| Failure point | Behaviour |
|---|---|
| Pass 1 API error | Raise — no point running Pass 2 without a thesis |
| Pass 1 hits 8-iteration cap | Submit current thesis, log warning |
| Pass 2 API error | Fall back to rendering InitialThesis directly — shallower but complete |
| `chart_order` key missing | Renderer drops missing key, falls back to default sequence |
| `signal_agreements/disagreements` name mismatch | Render text as-is — no crash, no colour-coding |
| `format_signal_feed()` gets unexpected score structure | Return empty signals list, `mechanical_score: 0` |

## Testing Plan

Run in order after implementation:

1. **Schema integrity** — CCL end-to-end; assert all required keys present with correct types in both `InitialThesis` and `FinalAnalysis`
2. **Anchoring check** — AVGO; inspect whether Pass 1 recommendation differs from mechanical score direction; verify `signal_disagreements` contains at least one entry explaining valuation premium
3. **Sector spread** — CAT, JPM, NEE; confirm sector-specific signal suppression flows correctly through `format_signal_feed()` and doesn't confuse Pass 2

## Known Risks

- **Latency**: two Claude calls with an agentic loop may approach the 300s gunicorn timeout on complex tickers — mitigated by 8-iteration cap on Pass 1
- **Pass 1 quality ceiling**: Sonnet's thesis is the anchor for Opus — if it's shallow, Pass 2 reconciles against a weak starting point
- **Pass 2 score anchoring**: if signal feed is strongly directional, Opus may ignore Pass 1 — Pass 2 system prompt must require explicit engagement with Pass 1 thesis
- **`chart_order` coupling**: chart keys must be stable; renderer fallback prevents crashes
