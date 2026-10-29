"""Where candidates were lost: one bar per stage, in run order, at the count that stage passed on. Gates pass
everyone or stop the run, so most steps are flat; the cut counts analogues only (the parent is the reference), and
an UNRANKED cut dropped nobody."""

from __future__ import annotations

from ._style import INK_MUTED, KEPT, UNRANKED, layout


def funnel_figure(rows: list[dict]):
    """`rows` is `results.funnel_rows(run)`."""
    import plotly.graph_objects as go

    stages = [r["stage"] for r in rows]
    out, text, colours = [], [], []
    for r in rows:
        n_in, n_out = r.get("candidates_in"), r.get("candidates_out")
        if n_out is None and r.get("unranked"):
            out.append(n_in or 0)
            text.append(f"{n_in} in, UNRANKED: none dropped")
            colours.append(UNRANKED)
        else:
            out.append(n_out if n_out is not None else (n_in or 0))
            text.append(f"{n_in} in, {n_out} out" if n_in is not None else "")
            colours.append(KEPT)
    fig = go.Figure(
        go.Bar(
            x=out,
            y=stages,
            orientation="h",
            name="candidates out",
            marker={"color": colours, "cornerradius": 4},
            text=text,
            textposition="outside",
            textfont={"color": INK_MUTED},
            hovertemplate="%{y}: %{text}<extra></extra>",
        )
    )
    layout(fig, title="Funnel: candidates passed on per stage", height=max(240, 44 * len(rows) + 120))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    fig.update_xaxes(title="candidates", rangemode="tozero", range=[0, max(out + [1]) * 1.6], dtick=1)
    fig.update_layout(bargap=0.35, showlegend=False)
    return fig
