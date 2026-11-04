"""Shared colours and plotly layout for the views. Colours carry a job, never a rank: blue is "kept by the cut",
grey "dropped", ink "not cut"; status colours (the UNRANKED banner) are not reused for series."""

from __future__ import annotations

KEPT = "#2a78d6"
NOT_KEPT = "#898781"
NEUTRAL = "#52514e"
PARENT = "#898781"
UNRANKED = "#c2410c"
INK = "#0b0b0b"
INK_MUTED = "#6b6a66"
BAND = "#f1f0ec"
BAND_ALT = "#ffffff"
SURFACE = "#fcfcfb"
GRID = "#e4e3de"
#: The decision statuses, each with a label as well as a colour.
DECISION = {"keep": "#1baf7a", "reject": "#e34948", "hold": "#eda100", None: "#c3c2b7"}


def layout(fig, *, title: str, height: int = 360) -> None:
    fig.update_layout(
        title={"text": title, "x": 0, "xanchor": "left", "font": {"size": 15, "color": INK}},
        height=height,
        template="plotly_white",
        paper_bgcolor=SURFACE,
        plot_bgcolor=SURFACE,
        font={"family": "system-ui, -apple-system, Segoe UI, sans-serif", "color": INK, "size": 12},
        legend={"orientation": "h", "y": -0.18, "x": 0, "font": {"color": INK_MUTED}},
        hoverlabel={"bgcolor": "white", "font": {"color": INK}},
        margin={"l": 80, "r": 30, "t": 60, "b": 70},
    )
    fig.update_xaxes(gridcolor=GRID, linecolor=GRID, tickfont={"color": INK_MUTED})
    fig.update_yaxes(gridcolor=GRID, linecolor=GRID, tickfont={"color": INK_MUTED})


#: Each light colour above that is chrome (ink, bands, grid, surfaces) mapped to its dark-mode counterpart, matching
#: Bench's dark tokens (ui/src/styles/tokens.css). Data colours (KEPT, NOT_KEPT, UNRANKED, DECISION) keep their
#: meaning in both themes and are not remapped. "white" and #2a3f5f are what template="plotly_white" sets.
DARK = {
    INK: "#eef0f1",
    INK_MUTED: "#a3abb0",
    NEUTRAL: "#c3c9cc",
    BAND: "#262a2d",
    BAND_ALT: "#1b1e20",
    SURFACE: "#1b1e20",
    GRID: "#2f3437",
    "white": "#1b1e20",
    "#2a3f5f": "#eef0f1",
    # plotly_white's own greys: its zero lines (#EBF0F8) and the grids of plot types the views do not draw yet
    "#ebf0f8": "#2f3437",
    "#dfe8f3": "#2f3437",
    "#c8d4e3": "#2f3437",
    "#a2b1c6": "#5b6368",
}
THEMES = ("light", "dark")


def themed(figure: dict, theme: str) -> dict:
    """The same figure, its chrome colours swapped for `theme`'s. Nothing but colour strings changes."""
    if theme not in THEMES:
        raise ValueError(f"unknown theme {theme!r}; themes are {', '.join(THEMES)}")
    if theme == "light":
        return figure
    swap = {k.lower(): v for k, v in DARK.items()}

    def walk(x):
        if isinstance(x, dict):
            return {k: walk(v) for k, v in x.items()}
        if isinstance(x, list):
            return [walk(v) for v in x]
        if isinstance(x, str):
            return swap.get(x.lower(), x)
        return x

    return walk(figure)
