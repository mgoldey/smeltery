"""The forest plot: paired ddE mean +/- z*SEM per analogue, in cut order, with the cut's tie groups as bands.

It draws exactly what `funnel.cut` decided. A tie group is one band labelled "(no order)"; the +/-floor band sits
on each group boundary when the floor is non-zero; the keep boundary is drawn only when the cut placed one, and an
UNRANKED cut says so across the top instead.

Every row also shows the per-pose values behind its mean, and its n. A mean with a wide interval can come from a
genuinely uncertain measurement or from one pose disagreeing with the others, and those call for different work
(more poses against a look at that pose's geometry). The points tell them apart; the interval alone cannot. An
UNRANKED verdict names the analogues whose own SEM is at least their mean, because that is usually the reason.
"""

from __future__ import annotations

from ._style import BAND, BAND_ALT, INK, INK_MUTED, KEPT, NEUTRAL, NOT_KEPT, UNRANKED, layout

#: The second channel for the cut's status, so it survives colour-vision loss and greyscale print; the legend shows it.
STATUS_SYMBOL = {True: "circle", False: "x", None: "diamond"}


def group_label(index: int, size: int) -> str:
    """'tie group 2 (no order)' for a group of several, 'group 1' for one candidate. 1-based for people."""
    return f"tie group {index + 1} (no order)" if size > 1 else f"group {index + 1}"


def wide_rows(rows: list[dict]) -> list[dict]:
    """The rows whose own SEM is at least as large as the size of their mean: each is a reason not to rank."""
    return [r for r in rows if r.get("sem") is not None and r["sem"] >= abs(r["mean"])]


def _unranked_text(rows: list[dict], run) -> str:
    """Why the cut refused: the keep boundary inside a tie group, and which analogues are too uncertain to place."""
    head = f"<b>UNRANKED</b>: keep={run.keep} falls inside a tie group. The cut refused to choose and dropped nothing."
    wide = wide_rows(rows)
    if not wide:
        return head
    names = ", ".join(f"{r['candidate']} (SEM {r['sem']:.2f} vs mean {r['mean']:.2f})" for r in wide[:3])
    more = f" and {len(wide) - 3} more" if len(wide) > 3 else ""
    return f"{head}<br>{len(wide)} of {len(rows)} have SEM at or above their own mean: {names}{more}"


def forest_figure(rows: list[dict], run, unit: str = "kcal/mol"):
    """`rows` is `results.ddE_table(run)`; `run` is the `results.Run`. Returns a plotly Figure."""
    import plotly.graph_objects as go

    n = len(rows)
    y = list(range(n))
    fig = go.Figure()
    # The per-pose values first, so the summary marker sits on top of them.
    pose_x = [v for r in rows for v in (r.get("per_pose") or [])]
    pose_y = [y[i] for i, r in enumerate(rows) for _ in (r.get("per_pose") or [])]
    pose_i = [j for r in rows for j, _ in enumerate(r.get("per_pose") or [])]
    if pose_x:
        fig.add_trace(
            go.Scatter(
                x=pose_x,
                y=pose_y,
                mode="markers",
                name="one pose",
                # As dark as the summary and a little taller: a pose that contradicts the mean must not be the
                # faintest thing on its row. Open ticks, so a cluster still reads as a cluster.
                marker={"size": 11, "color": INK, "opacity": 0.75, "symbol": "line-ns-open", "line": {"width": 2}},
                customdata=pose_i,
                hovertemplate="pose %{customdata}: %{x:.3f} " + unit + "<extra></extra>",
            )
        )
    # colour is never the only channel: each status also has its own marker shape (see STATUS_SYMBOL)
    status = {
        True: ("kept by the cut", KEPT, STATUS_SYMBOL[True]),
        False: ("dropped by the cut", NOT_KEPT, STATUS_SYMBOL[False]),
        None: ("not cut", NEUTRAL, STATUS_SYMBOL[None]),
    }
    for flag, (name, colour, symbol) in status.items():
        idx = [i for i, r in enumerate(rows) if r["survivor"] is flag]
        if not idx:
            continue
        fig.add_trace(
            go.Scatter(
                x=[rows[i]["mean"] for i in idx],
                y=[y[i] for i in idx],
                mode="markers",
                name=name,
                marker={
                    "size": 12 if symbol == "x" else 10,  # a thin x is easily lost under the error bar
                    "symbol": symbol,
                    "color": colour,
                    "line": {"width": 1 if symbol == "x" else 2, "color": "white"},
                },
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
            text=_unranked_text(rows, run),
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
    # The shared legend sits at a fixed y of -0.18, which on this plot's variable height lands on top of the
    # x-axis title. Put the legend below the title and give the bottom margin room for both.
    # The shared legend sits below the plot; the x-axis title would land on the same line (legend y is a fraction
    # of plot height, so no single y clears it at every height). Put the legend first and stand the title off
    # below it, with bottom margin for both.
    fig.update_layout(
        margin={"r": 170, "t": 90 if run.unranked else 60, "b": 120},
        legend={"orientation": "h", "yref": "paper", "y": -0.12, "yanchor": "top", "x": 0},
    )
    fig.update_yaxes(
        tickmode="array",
        tickvals=y,
        ticktext=[f"{r['candidate']}<br><sup>n={r['n']}</sup>" if r.get("n") else r["candidate"] for r in rows],
        autorange="reversed",
        showgrid=False,
        zeroline=False,
        title=None,
    )
    fig.update_xaxes(
        title={"text": f"paired ddE ({unit}), lower is better; ticks are single poses", "standoff": 45},
        zeroline=False,
    )
    return fig
