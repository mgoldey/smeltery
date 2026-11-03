"""The candidate grid: mols2grid cells with each candidate's paired ddE, tie group, survivor flag and current
decision. Selecting cells picks the candidates a decision applies to (`selected_candidates`)."""

from __future__ import annotations

import ast
import json
from html import escape

from .forest import group_label

COLUMNS = ("name", "ddE", "group", "cut", "decision")


def grid_records(run, rows: list[dict], decisions: dict[str, dict] | None = None) -> list[dict]:
    """One record per candidate: the parent first, then the analogues in cut order. `rows` is
    `results.ddE_table(run)`; `decisions` maps a candidate to its decision (`status`, `reason`, `note`)."""
    decisions = decisions or {}
    sizes: dict[int, int] = {}
    for r in rows:
        sizes[r["group"]] = sizes.get(r["group"], 0) + 1

    def dec(name: str) -> str:
        d = decisions.get(name)
        return f"{d['status']} ({d['reason']})" if d else "undecided"

    out = [
        {
            "name": run.parent,
            "SMILES": run.candidates[run.parent],
            "ddE": "parent: the reference",
            "group": "",
            "cut": "",
            "decision": dec(run.parent),
            "status": (decisions.get(run.parent) or {}).get("status", ""),
        }
    ]
    cut_label = {True: "kept by the cut", False: "dropped by the cut", None: "UNRANKED: not cut"}
    for r in rows:
        out.append(
            {
                "name": r["candidate"],
                "SMILES": r["smiles"],
                "ddE": f"{r['mean']:+.2f} ± {r['sem']:.2f}",
                "group": group_label(r["group"], sizes[r["group"]]),
                "cut": cut_label[r["survivor"]],
                "decision": dec(r["candidate"]),
                "status": (decisions.get(r["candidate"]) or {}).get("status", ""),
            }
        )
    return out


def molgrid(records: list[dict], *, name: str = "smeltery"):
    """A `mols2grid.MolGrid` over `grid_records(...)`. Its `.widget` carries the selection."""
    import mols2grid

    return mols2grid.MolGrid(records, smiles_col="SMILES", name=name, size=(170, 120))


def _decision_style(value: str) -> str:
    from ._style import DECISION, INK_MUTED

    status = value.split(" ", 1)[0]
    if status not in DECISION:
        return f"color: {INK_MUTED}"
    return f"font-weight: 600; border-left: 4px solid {DECISION[status]}; padding-left: 4px"


def _render_kwargs() -> dict:
    return {
        "subset": ["img", "name", "ddE", "group", "cut", "decision"],
        "tooltip": ["name", "SMILES", "ddE", "group", "cut", "decision"],
        "style": {"decision": _decision_style},
        "fontfamily": "system-ui, -apple-system, Segoe UI, sans-serif",
        "n_items_per_page": 24,
        "truncate": False,
    }


def grid_html(grid) -> str:
    """The grid's full interactive HTML document (what the iframe shows)."""
    return grid.render(**_render_kwargs())


def grid_iframe(grid, *, height: int | None = None) -> str:
    """The grid as an iframe, as mols2grid shows it in a notebook: the iframe reaches the selection widget through
    `window.parent`, so the widget must be displayed on the same page (`mo.ui.anywidget(grid.widget)`)."""
    if height is None:  # about six cells a row at full width, one page of 24 at most
        height = 120 + 250 * -(-min(len(grid.dataframe), 24) // 6)
    doc = grid.render(use_iframe=True, **_render_kwargs())
    return (
        f'<iframe class="mols2grid-iframe" frameborder="0" width="100%" height="{height}" '
        'allow="clipboard-write" sandbox="allow-scripts allow-same-origin allow-downloads allow-popups allow-modals" '
        f'srcdoc="{escape(doc)}"></iframe>'
    )


def selected_candidates(selection, records: list[dict]) -> list[str]:
    """Candidate names for a mols2grid selection: the widget's `selection` trait (a dict literal string of
    {id: smiles}), a dict, or the anywidget value dict holding it. Ids are positions in `records`."""
    if isinstance(selection, dict) and "selection" in selection:
        selection = selection["selection"]
    if isinstance(selection, str):
        try:
            selection = ast.literal_eval(selection or "{}")
        except (ValueError, SyntaxError):
            selection = json.loads(selection)
    ids = sorted(int(i) for i in (selection or {}))
    return [records[i]["name"] for i in ids if 0 <= i < len(records)]
