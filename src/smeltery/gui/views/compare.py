"""The comparison forest: one per-key difference per row, with the systematic floor drawn as a band.

The floor band is the point of the plot. A difference whose marker sits inside it is not resolved, and
drawing it makes that visible at a glance instead of leaving it in a sentence under the figure. The
aggregate (the median of the per-key differences) is a separate line, so a reader sees both whether
most keys agree and whether the typical difference clears the floor.

Unpaired keys -- where one arm produced nothing -- are drawn on their own axis at the bottom as a
count, never as a difference of zero and never dropped: an arm producing nothing for a key is a
result about that arm, and it is the fact that pooling hides.
"""

from __future__ import annotations

from ._style import BAND, GRID, INK, INK_MUTED, KEPT, NOT_KEPT, UNRANKED, layout


def compare_figure(comparison, height: int = 420):
    """`comparison` is a `smeltery.gui.compare.Comparison`. Returns a plotly Figure."""
    import plotly.graph_objects as go

    rows = comparison.rows()
    paired = [r for r in rows if not r.unpaired]
    s = comparison.summary()
    unit = comparison.unit
    fig = go.Figure()

    if not paired:
        fig.add_annotation(
            text=f"No key has both arms: {comparison.label_a} and {comparison.label_b} cannot be compared.",
            x=0.5,
            y=0.5,
            xref="paper",
            yref="paper",
            showarrow=False,
            font={"size": 14, "color": UNRANKED},
        )
        layout(fig, title=f"{comparison.label_a} vs {comparison.label_b}: nothing to compare", height=240)
        return fig

    y = list(range(len(paired)))

    # The floor band first, so markers draw on top of it.
    if comparison.floor is not None and comparison.floor > 0:
        fig.add_shape(
            type="rect",
            x0=-comparison.floor,
            x1=comparison.floor,
            y0=-0.6,
            y1=len(paired) - 0.4,
            fillcolor=BAND,
            line={"width": 0},
            layer="below",
        )
        fig.add_annotation(
            x=0,
            y=len(paired) - 0.5,
            text=f"± {comparison.floor:.2f} {unit} systematic floor: inside is NOT resolved",
            showarrow=False,
            font={"size": 11, "color": INK_MUTED},
            yanchor="bottom",
        )

    fig.add_vline(x=0, line={"color": GRID, "width": 1})

    # One marker per key. Colour says which arm leads; a key inside the floor is hollow, because the
    # lead is not resolvable and a filled marker would overstate it.
    for lead, colour, name in (
        ("a", KEPT, comparison.label_a),
        ("b", NOT_KEPT, comparison.label_b),
    ):
        idx = [i for i, r in enumerate(paired) if r.leader == lead]
        if not idx:
            continue
        fig.add_trace(
            go.Scatter(
                x=[paired[i].diff for i in idx],
                y=[y[i] for i in idx],
                mode="markers",
                name=f"favours {name}",
                marker={
                    "size": 11,
                    "color": [colour if paired[i].resolved else "rgba(0,0,0,0)" for i in idx],
                    "line": {"width": 2, "color": colour},
                    "symbol": "circle",
                },
                customdata=[
                    [
                        paired[i].key,
                        paired[i].a,
                        paired[i].b,
                        paired[i].n_a,
                        paired[i].n_b,
                        "resolved"
                        if paired[i].resolved
                        else ("within the floor" if paired[i].resolved is False else "no floor measured"),
                    ]
                    for i in idx
                ],
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    f"{comparison.label_a} median %{{customdata[1]:.2f}} {unit} (n=%{{customdata[3]}})<br>"
                    f"{comparison.label_b} median %{{customdata[2]:.2f}} {unit} (n=%{{customdata[4]}})<br>"
                    f"difference %{{x:+.2f}} {unit} — %{{customdata[5]}}<extra></extra>"
                ),
            )
        )

    md = s["median_per_key_diff"]
    if md is not None:
        inside = s["aggregate_resolved"] is False
        fig.add_vline(
            x=md,
            line={"color": UNRANKED if inside else INK, "width": 2, "dash": "dash"},
            annotation={
                "text": f"median {md:+.2f}" + (" (within floor)" if inside else ""),
                "font": {"color": UNRANKED if inside else INK, "size": 11},
            },
        )

    title = f"{comparison.label_a} − {comparison.label_b}: {comparison.quantity} per key"
    if s["n_unpaired"]:
        title += f"  ({s['n_unpaired']} key(s) unpaired, listed below)"
    layout(fig, title=title, height=height)
    fig.update_yaxes(
        tickmode="array",
        tickvals=y,
        ticktext=[r.key for r in paired],
        autorange="reversed",
        title=None,
    )
    fig.update_xaxes(
        title=f"difference in median {comparison.quantity} ({unit}) — negative favours {comparison.label_a}"
    )
    return fig


def unpaired_table(comparison) -> list[dict]:
    """The keys only one arm produced for: a result about the other arm, not a missing value."""
    return [
        {
            "key": r.key,
            f"n {comparison.label_a}": r.n_a,
            f"n {comparison.label_b}": r.n_b,
            "which arm is empty": comparison.label_b if r.n_a else comparison.label_a,
        }
        for r in comparison.rows()
        if r.unpaired
    ]


def comparison_caption(comparison) -> str:
    """The sentence that must sit under the figure: the verdict, then the pooled number as a warning."""
    s = comparison.summary()
    lines = [comparison.verdict()]
    if s["pooled_diff_CONFOUNDED"] is not None:
        lines.append(
            f"Pooled over all molecules the difference is {s['pooled_diff_CONFOUNDED']:+.2f} {comparison.unit} "
            f"({comparison.label_a} n={s['n_molecules_a']}, {comparison.label_b} n={s['n_molecules_b']}). "
            "That number is CONFOUNDED by which keys each arm produced molecules for and is shown only "
            "for contrast with the per-key figure above."
        )
    return "  ".join(lines)
