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
