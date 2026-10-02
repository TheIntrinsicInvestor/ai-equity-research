"""
Generates all 7 report charts as Plotly figure JSON strings for client-side rendering.
"""

import json
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

COLORS = {
    "primary":  "#e2e8f0",
    "accent":   "#3b82f6",
    "positive": "#4ade80",
    "negative": "#f87171",
    "neutral":  "#94a3b8",
    "spy":      "#f59e0b",
    "etf":      "#8b5cf6",
    "bg":       "#0f172a",
    "plot_bg":  "#080e1a",
    "grid":     "#1e293b",
}

_AXIS = dict(gridcolor=COLORS["grid"], linecolor=COLORS["grid"], tickfont=dict(color="#94a3b8"))

_LAYOUT = dict(
    paper_bgcolor=COLORS["bg"],
    plot_bgcolor=COLORS["plot_bg"],
    font=dict(family="IBM Plex Mono, -apple-system, monospace", size=11, color=COLORS["primary"]),
    margin=dict(l=48, r=24, t=36, b=48),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1, font_size=11,
        bgcolor="rgba(0,0,0,0)", bordercolor="rgba(0,0,0,0)",
    ),
    xaxis=_AXIS,
    yaxis=_AXIS,
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1e293b",
        bordercolor="#3b82f6",
        font=dict(color="#e2e8f0", family="IBM Plex Mono, monospace", size=11),
    ),
)

_RANGESELECTOR = dict(
    buttons=[
        dict(count=1,  label="1M", step="month", stepmode="backward"),
        dict(count=3,  label="3M", step="month", stepmode="backward"),
        dict(count=1,  label="1Y", step="year",  stepmode="backward"),
        dict(step="all", label="5Y"),
    ],
    bgcolor="#0f172a",
    activecolor="#1e40af",
    bordercolor="#1e293b",
    font=dict(color="#94a3b8", size=10, family="IBM Plex Mono, monospace"),
    x=0, y=1.08,
)


def _empty(msg: str) -> str:
    fig = go.Figure()
    fig.add_annotation(text=msg, xref="paper", yref="paper", x=0.5, y=0.5,
                       showarrow=False, font=dict(size=13, color=COLORS["neutral"]))
    fig.update_layout(**_LAYOUT, height=300)
    return fig.to_json()


def _to_json(fig: go.Figure) -> str:
    return fig.to_json()


# ── Chart 1: Price history + volume ──────────────────────────────────────────

def chart_price(prices: pd.DataFrame) -> str:
    if prices is None or prices.empty:
        return _empty("Price data unavailable")

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        x=prices.index, y=prices["vol"],
        name="Volume", marker_color=COLORS["neutral"], opacity=0.35,
        hovertemplate="%{x}<br>Volume: %{y:,.0f}<extra></extra>",
    ), secondary_y=True)

    fig.add_trace(go.Scatter(
        x=prices.index, y=prices["adj_price"],
        name="Adj. Price", line=dict(color=COLORS["primary"], width=2),
        hovertemplate="%{x}<br>$%{y:,.2f}<extra></extra>",
    ), secondary_y=False)

    fig.update_layout(**{**_LAYOUT, "margin": dict(l=48, r=24, t=52, b=48)}, height=320)
    fig.update_yaxes(title_text="Price ($)", tickprefix="$", secondary_y=False,
                     gridcolor=COLORS["grid"], tickfont=dict(color="#94a3b8"))
    fig.update_yaxes(title_text="Volume", showgrid=False, secondary_y=True,
                     tickfont=dict(color="#94a3b8"))
    fig.update_xaxes(rangeselector=_RANGESELECTOR, type="date",
                     gridcolor=COLORS["grid"], tickfont=dict(color="#94a3b8"))
    return _to_json(fig)


# ── Chart 2: Revenue & EPS trend ─────────────────────────────────────────────

def chart_revenue_eps(annual: pd.DataFrame) -> str:
    if annual is None or annual.empty or "sale" not in annual.columns:
        return _empty("Revenue/EPS data unavailable")

    df = annual.dropna(subset=["sale"]).copy()
    df["fyear"] = df["fyear"].astype(int).astype(str)

    fig = make_subplots(specs=[[{"secondary_y": True}]])

    fig.add_trace(go.Bar(
        x=df["fyear"], y=df["sale"] / 1000,
        name="Revenue ($B)", marker_color=COLORS["accent"], opacity=0.85,
        hovertemplate="FY%{x}<br>Revenue: $%{y:.1f}B<extra></extra>",
    ), secondary_y=False)

    if "epspx" in df.columns:
        fig.add_trace(go.Scatter(
            x=df["fyear"], y=df["epspx"],
            name="EPS ($)", line=dict(color=COLORS["positive"], width=2.5),
            mode="lines+markers", marker_size=7,
            hovertemplate="FY%{x}<br>EPS: $%{y:.2f}<extra></extra>",
        ), secondary_y=True)

    fig.update_layout(**_LAYOUT, height=320)
    fig.update_yaxes(title_text="Revenue ($B)", tickprefix="$", secondary_y=False,
                     gridcolor=COLORS["grid"], tickfont=dict(color="#94a3b8"))
    fig.update_yaxes(title_text="EPS ($)", tickprefix="$", showgrid=False, secondary_y=True,
                     tickfont=dict(color="#94a3b8"))
    return _to_json(fig)


# ── Chart 3: Margin trends ────────────────────────────────────────────────────

def chart_margins(annual: pd.DataFrame) -> str:
    if annual is None or annual.empty or "sale" not in annual.columns:
        return _empty("Margin data unavailable")

    df = annual.copy()
    df["fyear"] = df["fyear"].astype(int).astype(str)
    df = df[df["sale"] > 0].copy()

    fig = go.Figure()
    for col, label, color in [
        ("gp",     "Gross Margin",  COLORS["accent"]),
        ("ebitda", "EBITDA Margin", COLORS["positive"]),
        ("ni",     "Net Margin",    COLORS["primary"]),
    ]:
        if col in df.columns:
            margin = df[col] / df["sale"] * 100
            fig.add_trace(go.Scatter(
                x=df["fyear"], y=margin,
                name=label, line=dict(color=color, width=2.5),
                mode="lines+markers", marker_size=7,
                hovertemplate=f"FY%{{x}}<br>{label}: %{{y:.1f}}%<extra></extra>",
            ))

    fig.update_layout(**_LAYOUT, height=320)
    fig.update_yaxes(ticksuffix="%", gridcolor=COLORS["grid"])
    return _to_json(fig)


# ── Chart 4: Comps multiples ──────────────────────────────────────────────────

def chart_comps(comps: dict, gsector: str = "") -> str:
    first = ("pb", "P/B") if gsector == "40" else ("ev_ebitda", "EV/EBITDA")
    metrics = [first, ("pe", "P/E"), ("ps", "P/S")]
    labels  = [lbl for _, lbl in metrics]
    target_vals = [comps["target"].get(m) for m, _ in metrics]
    medians     = [comps["peer_stats"][m]["median"] for m, _ in metrics]
    p25s        = [comps["peer_stats"][m]["p25"] for m, _ in metrics]
    p75s        = [comps["peer_stats"][m]["p75"] for m, _ in metrics]

    fig = go.Figure()

    fig.add_trace(go.Bar(
        name="Target", x=labels,
        y=[v or 0 for v in target_vals],
        marker_color=COLORS["primary"],
        hovertemplate="%{x}<br>Target: %{y:.1f}x<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        name="Peer Median", x=labels,
        y=[v or 0 for v in medians],
        marker_color=COLORS["accent"],
        hovertemplate="%{x}<br>Peer Median: %{y:.1f}x<extra></extra>",
    ))

    # Peer range whiskers
    for i, (lbl, p25, p75) in enumerate(zip(labels, p25s, p75s)):
        if p25 is not None and p75 is not None:
            fig.add_shape(type="line", x0=i + 0.2, x1=i + 0.2,
                          y0=p25, y1=p75, xref="x", yref="y",
                          line=dict(color=COLORS["accent"], width=3))

    fig.update_layout(**_LAYOUT, barmode="group", height=320)
    fig.update_yaxes(ticksuffix="x", gridcolor=COLORS["grid"])
    return _to_json(fig)


# ── Chart 5: Analyst EPS estimate revisions ───────────────────────────────────

def chart_estimates(estimates: dict) -> str:
    eps_history = estimates.get("eps_history")
    if eps_history is None or eps_history.empty:
        return _empty("Estimate revision data unavailable")

    df = eps_history.dropna(subset=["meanest"]).copy()
    df["statpers"] = pd.to_datetime(df["statpers"])
    df = df.sort_values("statpers").tail(20)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df["statpers"], y=df["meanest"],
        name="Consensus EPS",
        line=dict(color=COLORS["accent"], width=2.5),
        fill="tozeroy", fillcolor="rgba(59,130,246,0.08)",
        mode="lines+markers", marker_size=6,
        hovertemplate="%{x|%b %Y}<br>EPS: $%{y:.2f}<extra></extra>",
    ))

    fig.update_layout(**_LAYOUT, height=300)
    fig.update_yaxes(tickprefix="$", gridcolor=COLORS["grid"])
    return _to_json(fig)


# ── Chart 6: Relative performance ────────────────────────────────────────────

def chart_relative_perf(target_prices: pd.DataFrame, benchmarks: dict, ticker: str) -> str:
    if target_prices is None or target_prices.empty:
        return _empty("Price data unavailable")

    def _index(series: pd.Series) -> pd.Series:
        s = series.dropna()
        return s / s.iloc[0] * 100 if not s.empty else s

    fig = go.Figure()
    indexed_target = _index(target_prices["adj_price"])
    fig.add_trace(go.Scatter(
        x=indexed_target.index, y=indexed_target.values,
        name=ticker, line=dict(color=COLORS["primary"], width=2.5),
        hovertemplate="%{x}<br>" + ticker + ": %{y:.1f}<extra></extra>",
    ))

    bench_colors = [COLORS["spy"], COLORS["etf"]]
    for (sym, series), color in zip((benchmarks or {}).items(), bench_colors):
        indexed = _index(series)
        fig.add_trace(go.Scatter(
            x=indexed.index, y=indexed.values,
            name=sym, line=dict(color=color, width=1.8, dash="dash"),
            hovertemplate="%{x}<br>" + sym + ": %{y:.1f}<extra></extra>",
        ))

    fig.update_layout(**{**_LAYOUT, "margin": dict(l=48, r=24, t=52, b=48)}, height=320)
    fig.update_yaxes(gridcolor=COLORS["grid"], tickfont=dict(color="#94a3b8"))
    fig.update_xaxes(rangeselector=_RANGESELECTOR, type="date",
                     gridcolor=COLORS["grid"], tickfont=dict(color="#94a3b8"))
    return _to_json(fig)


# ── Chart 7: Sentiment trend ──────────────────────────────────────────────────

def chart_sentiment(nlp_results: list[dict]) -> str:
    if not nlp_results:
        return _empty("Sentiment data unavailable")

    periods = [r.get("period") or r.get("filing_date", "") for r in nlp_results]
    scores  = [r["overall_sentiment"] for r in nlp_results]
    colors  = [
        COLORS["positive"] if s > 0.1 else COLORS["negative"] if s < -0.1 else COLORS["neutral"]
        for s in scores
    ]

    fig = go.Figure()
    fig.add_hline(y=0, line_dash="dot", line_color=COLORS["primary"], line_width=1)
    fig.add_trace(go.Bar(
        x=periods, y=scores,
        marker_color=colors,
        hovertemplate="%{x}<br>Sentiment: %{y:.3f}<extra></extra>",
    ))

    fig.update_layout(**_LAYOUT, showlegend=False, height=280)
    fig.update_yaxes(range=[-1.1, 1.1], gridcolor=COLORS["grid"])
    return _to_json(fig)


# ── Chart 8: FCF & Net Debt ───────────────────────────────────────────────────

def chart_fcf(annual: pd.DataFrame) -> str | None:
    if annual is None or annual.empty or "fcf" not in annual.columns:
        return None
    df = annual.dropna(subset=["fcf"]).tail(5).copy()
    if len(df) < 2:
        return None

    df["fyear"] = df["fyear"].astype(int).astype(str)
    fcf_vals = df["fcf"] / 1000  # convert to billions
    bar_colors = [COLORS["positive"] if v >= 0 else COLORS["negative"] for v in fcf_vals]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df["fyear"], y=fcf_vals,
        name="FCF ($B)",
        marker_color=bar_colors,
        hovertemplate="FY%{x}<br>FCF: $%{y:.1f}B<extra></extra>",
    ))

    if "net_debt" in df.columns:
        nd_vals = df["net_debt"].fillna(0) / 1000
        fig.add_trace(go.Scatter(
            x=df["fyear"], y=nd_vals,
            name="Net Debt ($B)", line=dict(color=COLORS["neutral"], width=2, dash="dot"),
            mode="lines+markers", marker_size=6, yaxis="y2",
            hovertemplate="FY%{x}<br>Net Debt: $%{y:.1f}B<extra></extra>",
        ))
        fig.update_layout(
            **_LAYOUT,
            height=320,
            yaxis2=dict(
                overlaying="y", side="right", showgrid=False,
                tickprefix="$", ticksuffix="B",
                tickfont=dict(color="#94a3b8"),
                title=dict(text="Net Debt ($B)", font=dict(color="#94a3b8")),
            ),
        )
    else:
        fig.update_layout(**_LAYOUT, height=320)

    fig.update_yaxes(tickprefix="$", ticksuffix="B", gridcolor=COLORS["grid"])
    fig.add_hline(y=0, line_dash="dot", line_color=COLORS["grid"], line_width=1)
    return _to_json(fig)


# ── Aggregator ────────────────────────────────────────────────────────────────

def generate_all_charts(
    target_data: dict,
    peer_data: list[dict],
    comps: dict,
    nlp_results: list[dict],
    benchmarks: dict,
) -> dict:
    ticker  = target_data["financials"]["ticker"]
    annual  = target_data["financials"].get("annual", pd.DataFrame())
    prices  = target_data.get("prices", pd.DataFrame())
    estimates = target_data.get("estimates", {})
    gsector = str(target_data["financials"].get("gsector", ""))

    charts = {
        "price":         chart_price(prices),
        "revenue_eps":   chart_revenue_eps(annual),
        "margins":       chart_margins(annual),
        "comps":         chart_comps(comps, gsector),
        "estimates":     chart_estimates(estimates),
        "relative_perf": chart_relative_perf(prices, benchmarks, ticker),
        "sentiment":     chart_sentiment(nlp_results),
    }
    fcf_chart = chart_fcf(annual)
    if fcf_chart is not None:
        charts["fcf"] = fcf_chart
    return charts
