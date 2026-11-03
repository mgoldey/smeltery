"""Per-pose paired lines: for each analogue, one line per pose from the parent's value to the analogue's. Lines that
run parallel are what pairing cancels: the pose-to-pose spread the unpaired SEM carries."""

from __future__ import annotations

from ._style import INK_MUTED, KEPT, layout


def per_pose_figure(
    table: list[dict], run, *, quantity: str = "dE_int", unit: str = "", rows: list[dict] | None = None
):
    """`table` is `results.per_pose_table(run, quantity)`. One subplot per analogue (in `rows` order when given,
    i.e. cut order), one trace per (analogue, pose)."""
    from plotly.subplots import make_subplots

    order = [r["candidate"] for r in rows] if rows else list(dict.fromkeys(t["candidate"] for t in table))
    order = [c for c in order if any(t["candidate"] == c for t in table)]
    by = {r["candidate"]: r for r in rows or []}
    titles = []
    for c in order:
        r = by.get(c)
        titles.append(f"{c}<br>paired SEM {r['sem']:.2f}, unpaired {r['unpaired_sem']:.2f}" if r else c)
    fig = make_subplots(rows=1, cols=max(1, len(order)), shared_yaxes=True, subplot_titles=titles)
    for k, c in enumerate(order):
        for t in (t for t in table if t["candidate"] == c):
            fig.add_scatter(
                x=[run.parent, c],
                y=[t["parent_value"], t["value"]],
                mode="lines+markers",
                name=f"{c} pose {t['pose']}",
                legendgroup=c,
                showlegend=False,
                line={"color": KEPT, "width": 2},
                marker={"size": 8, "color": [INK_MUTED, KEPT]},
                hovertemplate=(
                    f"{c} pose {t['pose']}<br>parent %{{customdata[0]:.3f}}, analogue %{{customdata[1]:.3f}}"
                    "<br>paired %{customdata[2]:.3f}<extra></extra>"
                ),
                customdata=[[t["parent_value"], t["value"], t["paired"]]] * 2,
                row=1,
                col=k + 1,
            )
    label = f"{quantity} ({unit})" if unit else quantity
    layout(fig, title=f"Per pose: {run.parent} to analogue, {label}", height=380)
    fig.update_yaxes(title_text=label, row=1, col=1)
    fig.update_annotations(font={"size": 11, "color": INK_MUTED})
    fig.update_layout(margin={"t": 100})
    return fig
