"""Per-pose values: one marker per (ligand, pose) at that pose's own value, the parent included. Where `per_pose`
draws the paired difference, this draws what was measured, so a click on a point can open that pose and put every
ligand's value for it side by side."""

from __future__ import annotations

from ._style import INK, KEPT, NEUTRAL, NOT_KEPT, layout

#: One symbol per ligand, in order, so two ligands the same colour still tell apart without the legend.
SYMBOLS = ("circle", "square", "diamond", "triangle-up", "x", "cross", "star", "hexagon")


def per_pose_values_figure(table: list[dict], run, quantity: str = "dE_int"):
    """`table` is `results.per_pose_table(run, quantity)`, whose analogue order (the cut's) is the legend's. Every
    y is a number from the record's `results.per_pose`: the parent's directly, each analogue's from `table`.
    `customdata` of each point is `[candidate, pose, value, paired]` (paired is None for the parent), which is
    what the browser's click handler reads."""
    import plotly.graph_objects as go

    results = run.record["results"]
    unit = results.get("units", {}).get(quantity, "")
    kept = set(run.survivors) if run.survivors is not None else None
    parent_values = results["per_pose"][run.parent][quantity]
    ligands: list[tuple[str, list[float], list[float | None], str]] = [
        (run.parent, list(parent_values), [None] * len(parent_values), INK)
    ]
    for name in dict.fromkeys(t["candidate"] for t in table):
        rows = sorted((t for t in table if t["candidate"] == name), key=lambda t: t["pose"])
        colour = NEUTRAL if kept is None else KEPT if name in kept else NOT_KEPT
        ligands.append((name, [t["value"] for t in rows], [t["paired"] for t in rows], colour))
    fig = go.Figure()
    for k, (name, values, paired, colour) in enumerate(ligands):
        fig.add_scatter(
            x=list(range(len(values))),
            y=values,
            mode="markers",
            name=name if name != run.parent else f"{name} (parent)",
            marker={"size": 10, "color": colour, "symbol": SYMBOLS[k % len(SYMBOLS)]},
            customdata=[[name, i, v, p] for i, (v, p) in enumerate(zip(values, paired, strict=True))],
            hovertemplate=f"{name} pose %{{x}}<br>{quantity} %{{y:.3f}}<extra></extra>",
        )
    label = f"{quantity} ({unit})" if unit else quantity
    layout(fig, title=f"Per pose: {label} of every ligand", height=420)
    fig.update_xaxes(title_text="pose", dtick=1)
    fig.update_yaxes(title_text=label)
    fig.update_layout(clickmode="event", legend={"orientation": "v", "y": 1, "x": 1.02})
    return fig
