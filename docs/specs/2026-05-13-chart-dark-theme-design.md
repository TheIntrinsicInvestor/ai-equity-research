# Chart Dark Theme & Interaction Polish

*Date: 2026-05-13*

## Problem

All 7 Plotly charts render with white backgrounds (`#ffffff`) and dark-navy text/lines (`#1a1a2e`). The report page background is `#060a12`. The charts appear as bright white boxes embedded in a dark Bloomberg-style terminal — visually inconsistent and jarring.

Additionally:
- The ticker price line and target bars use `#1a1a2e` (dark navy), which is invisible on a dark background.
- Positive/negative color tokens (`#22c55e` / `#ef4444`) don't match the page CSS equivalents (`#4ade80` / `#f87171`).
- No range selectors on price/performance charts.
- Hover tooltips use Plotly's default light-colored box, inconsistent with the dark UI.

## Scope

One file: `pipeline/output/chart_generator.py`. No changes to `templates/report.html` or any other file.

## Color Changes

| Token | Old | New | Rationale |
|---|---|---|---|
| `primary` | `#1a1a2e` | `#e2e8f0` | Was dark navy — invisible on dark bg; now matches page primary text |
| `bg` | `#ffffff` | `#0f172a` | Matches `.section` background in report.html |
| `plot_bg` | _(new)_ | `#080e1a` | Slightly darker than section bg; subtle depth for plot area |
| `grid` | `#f1f5f9` | `#1e293b` | Matches page border/surface color |
| `positive` | `#22c55e` | `#4ade80` | Align with `.pts-pos` / `.bar-pos` in page CSS |
| `negative` | `#ef4444` | `#f87171` | Align with `.pts-neg` / `.bar-neg` in page CSS |
| `accent` | `#3b82f6` | unchanged | Already correct |
| `neutral` | `#94a3b8` | unchanged | Already correct |
| `spy` | `#f59e0b` | unchanged | Already correct |
| `etf` | `#8b5cf6` | unchanged | Already correct |

## Layout Changes (`_LAYOUT`)

```python
_LAYOUT = dict(
    paper_bgcolor=COLORS["bg"],       # #0f172a
    plot_bgcolor=COLORS["plot_bg"],   # #080e1a
    font=dict(
        family="IBM Plex Mono, -apple-system, monospace",
        size=11,
        color=COLORS["primary"],      # #e2e8f0
    ),
    margin=dict(l=48, r=24, t=36, b=48),
    legend=dict(
        orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
        font_size=11,
        bgcolor="rgba(0,0,0,0)",      # transparent legend bg
        bordercolor="rgba(0,0,0,0)",
    ),
    xaxis=dict(
        gridcolor=COLORS["grid"],
        linecolor=COLORS["grid"],
        tickfont=dict(color="#94a3b8"),
    ),
    yaxis=dict(
        gridcolor=COLORS["grid"],
        linecolor=COLORS["grid"],
        tickfont=dict(color="#94a3b8"),
    ),
    hovermode="x unified",
    hoverlabel=dict(
        bgcolor="#1e293b",
        bordercolor="#3b82f6",
        font=dict(color="#e2e8f0", family="IBM Plex Mono, monospace", size=11),
    ),
)
```

## Interaction Changes

### Range Selectors (price + relative perf charts only)

A `_RANGESELECTOR` constant is defined once and applied via `fig.update_xaxes()` in `chart_price` and `chart_relative_perf`:

```python
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
```

### Unified Hover

`hovermode="x unified"` in `_LAYOUT` applies to all 7 charts. For the two `make_subplots` charts (`chart_price`, `chart_revenue_eps`), the secondary y-axis layout must also receive dark tick/grid colors via `fig.update_yaxes(tickfont=dict(color="#94a3b8"), gridcolor=COLORS["grid"])`.

### modebar

No change — `displayModeBar: false` stays in report.html. Zoom/pan not in scope.

## Charts Affected

| Chart | Dark theme | Range selector | Unified hover |
|---|---|---|---|
| `chart_price` | yes | yes | yes |
| `chart_revenue_eps` | yes | no (categorical x) | yes |
| `chart_margins` | yes | no (categorical x) | yes |
| `chart_comps` | yes | no (categorical x) | yes |
| `chart_estimates` | yes | no (too few points) | yes |
| `chart_relative_perf` | yes | yes | yes |
| `chart_sentiment` | yes | no (categorical x) | yes |

## Out of Scope

- P/FFO for REITs
- FinBERT re-enable on Railway
- Interactive drill-down or cross-chart linking
- modebar / zoom controls
