"""The "resolved?" matrix: for every pair of analogues, `funnel.resolved(a, b, z, floor)` as the cut computed it.
Tie groups show as unresolved blocks on the diagonal. No new statistics: the view only colours what it is given."""

from __future__ import annotations

from ._style import BAND, INK, INK_MUTED, KEPT, layout


def matrix_figure(names: list[str], matrix: list[list[bool]], rows: list[dict] | None = None):
    """`names, matrix = results.resolved_matrix(run)`; `rows` (`results.ddE_table(run)`, same order) outlines the
    tie groups."""
    import plotly.graph_objects as go

    n = len(names)
    z = [[1 if matrix[i][j] else 0 for j in range(n)] for i in range(n)]
    text = [["" if i == j else ("resolved" if matrix[i][j] else "not resolved") for j in range(n)] for i in range(n)]
    fig = go.Figure(
        go.Heatmap(
            z=z,
            x=names,
            y=names,
            zmin=0,
            zmax=1,
            colorscale=[[0, BAND], [0.5, BAND], [0.5, KEPT], [1, KEPT]],
            showscale=False,
            text=text,
            texttemplate="%{text}",
            textfont={"size": 10},
            xgap=2,
            ygap=2,
            hovertemplate="%{y} vs %{x}: %{text}<extra></extra>",
            name="resolved",
        )
    )
    if rows:
        idx: dict[int, list[int]] = {}
        for i, r in enumerate(rows):
            idx.setdefault(r["group"], []).append(i)
        for g, members in sorted(idx.items()):
            if len(members) > 1:
                lo, hi = min(members) - 0.5, max(members) + 0.5
                fig.add_shape(
                    type="rect",
                    name=f"tie-group-{g}",
                    x0=lo,
                    x1=hi,
                    y0=lo,
                    y1=hi,
                    line={"color": INK, "width": 2},
                    fillcolor="rgba(0,0,0,0)",
                )
    layout(fig, title="Resolved? (the cut's own test, |d| > z*SEM and > floor)", height=max(280, 60 * n + 140))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(showgrid=False, side="top", tickfont={"color": INK_MUTED})
    fig.update_layout(margin={"t": 110})
    return fig
