"""The forest plot: paired ddE mean +/- z*SEM per analogue, in cut order, with the cut's tie groups as bands.

It draws exactly what `funnel.cut` decided. A tie group is one band labelled "(no order)"; the +/-floor band sits
on each group boundary when the floor is non-zero; the keep boundary is drawn only when the cut placed one, and an
UNRANKED cut says so across the top instead.
"""

from __future__ import annotations

from ._style import BAND, BAND_ALT, INK, INK_MUTED, KEPT, NEUTRAL, NOT_KEPT, UNRANKED, layout


def group_label(index: int, size: int) -> str:
    """'tie group 2 (no order)' for a group of several, 'group 1' for one candidate. 1-based for people."""
    return f"tie group {index + 1} (no order)" if size > 1 else f"group {index + 1}"


def forest_figure(rows: list[dict], run, unit: str = "kcal/mol"):
    """`rows` is `results.ddE_table(run)`; `run` is the `results.Run`. Returns a plotly Figure."""
    import plotly.graph_objects as go

    n = len(rows)
    y = list(range(n))
    fig = go.Figure()
    status = {True: ("kept by the cut", KEPT), False: ("dropped by the cut", NOT_KEPT), None: ("not cut", NEUTRAL)}
    for flag, (name, colour) in status.items():
        idx = [i for i, r in enumerate(rows) if r["survivor"] is flag]
        if not idx:
            continue
        fig.add_trace(
            go.Scatter(
                x=[rows[i]["mean"] for i in idx],
                y=[y[i] for i in idx],
                mode="markers",
                name=name,
                marker={"size": 10, "color": colour, "line": {"width": 2, "color": "white"}},
                error_x={
                    "type": "data",
                    "symmetric": False,
                    "array": [rows[i]["hi"] - rows[i]["mean"] for i in idx],
                    "arrayminus": [rows[i]["mean"] - rows[i]["lo"] for i in idx],
                    "color": colour,
                    "thickness": 2,
                    "width": 6,
                },
                customdata=[
                    [
                        rows[i]["candidate"],
                        rows[i]["sem"],
                        rows[i]["n"],
                        rows[i]["lo"],
                        rows[i]["hi"],
                        rows[i]["unpaired_sem"],
                        group_label(rows[i]["group"], sum(r["group"] == rows[i]["group"] for r in rows)),
                    ]
                    for i in idx
                ],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>ddE %{x:.3f} "
                    + unit
                    + " (SEM %{customdata[1]:.3f}, n=%{customdata[2]})"
                    f"<br>mean -/+ z*SEM (z={run.z:g}): [%{{customdata[3]:.3f}}, %{{customdata[4]:.3f}}]"
                    "<br>unpaired SEM %{customdata[5]:.3f}<br>%{customdata[6]}<extra></extra>"
                ),
            )
        )
    # tie groups: one band each, alternating, labelled on the right
    starts = {}
    for i, r in enumerate(rows):
        starts.setdefault(r["group"], []).append(i)
    for k, (g, idx) in enumerate(sorted(starts.items())):
        fig.add_shape(
            type="rect",
            name=f"tie-group-{g}",
            xref="paper",
            x0=0,
            x1=1,
            y0=min(idx) - 0.5,
            y1=max(idx) + 0.5,
            fillcolor=BAND if k % 2 == 0 else BAND_ALT,
            line={"width": 0},
            layer="below",
        )
        fig.add_annotation(
            name=f"tie-group-{g}",
            xref="paper",
            x=1.0,
            xanchor="left",
            y=(min(idx) + max(idx)) / 2,
            text=group_label(g, len(idx)),
            showarrow=False,
            font={"color": INK_MUTED, "size": 11},
        )
    # +/- floor around each boundary between neighbouring groups
    if run.floor > 0:
        for g, idx in sorted(starts.items())[:-1]:
            last, first = rows[max(idx)], rows[max(idx) + 1]
            mid = (last["mean"] + first["mean"]) / 2
            fig.add_shape(
                type="rect",
                name=f"floor-band-{g}",
                x0=mid - run.floor,
                x1=mid + run.floor,
                y0=max(idx) - 0.5,
                y1=max(idx) + 1.5,
                fillcolor=INK_MUTED,
                opacity=0.15,
                line={"width": 0},
                layer="below",
            )
    fig.add_vline(x=0, line={"color": INK_MUTED, "width": 1}, name="parent")
    survivors = run.survivors
    if run.unranked:
        fig.add_annotation(
            name="unranked",
            xref="paper",
            yref="paper",
            x=0.5,
            y=1.0,
            yanchor="bottom",
            text=(
                f"<b>UNRANKED</b>: keep={run.keep} falls inside a tie group. "
                "The cut refused to choose and dropped nothing."
            ),
            showarrow=False,
            font={"color": "white", "size": 13},
            bgcolor=UNRANKED,
            borderpad=6,
        )
    elif survivors is not None and 0 < len(survivors) < n:
        b = len(survivors) - 0.5
        fig.add_shape(
            type="line",
            name="keep-boundary",
            xref="paper",
            x0=0,
            x1=1,
            y0=b,
            y1=b,
            line={"color": INK, "width": 2, "dash": "dash"},
        )
        fig.add_annotation(
            name="keep-boundary",
            xref="paper",
            x=0,
            xanchor="left",
            y=b,
            yanchor="bottom",
            text=f"keep boundary (keep={run.keep})",
            showarrow=False,
            font={"color": INK, "size": 11},
        )
    floor_txt = f"floor {run.floor:g} {unit}" if run.floor > 0 else "floor 0"
    layout(
        fig,
        title=f"Paired ddE vs {run.parent}: mean -/+ {run.z:g}*SEM, {floor_txt}",
        height=max(260, 70 * n + 140),
    )
    fig.update_layout(margin={"r": 170, "t": 90 if run.unranked else 60})
    fig.update_yaxes(
        tickmode="array",
        tickvals=y,
        ticktext=[r["candidate"] for r in rows],
        autorange="reversed",
        showgrid=False,
        zeroline=False,
        title=None,
    )
    fig.update_xaxes(title=f"paired ddE ({unit}), lower is better", zeroline=False)
    return fig
