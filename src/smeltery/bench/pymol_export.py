"""The PyMOL session script for a run's "Open in PyMOL" bundle: a pure function from names and the cut to .pml text.

The bundle is `session.pml`, `receptor.pdb` and `ligands/<candidate>.sdf`; the script loads them by paths relative
to itself, so run it from its own folder (`pymol session.pml`) or with `@session.pml`."""

from __future__ import annotations

import re

PARENT_COLOR = "magenta"
DROPPED_COLOR = "grey70"
#: Tie-group colours, best group first (cycled if a run has more groups than colours).
GROUP_COLORS = ("marine", "green", "orange", "cyan", "purpleblue", "teal", "salmon", "olive")
POCKET_CUTOFF_ANG = 5.0


def object_name(candidate: str) -> str:
    """A PyMOL-safe object name for a candidate: `lig_` plus the name with anything but [A-Za-z0-9_] as `_`."""
    return "lig_" + re.sub(r"[^A-Za-z0-9_]", "_", candidate)


def ligand_filename(candidate: str) -> str:
    return object_name(candidate)[len("lig_") :] + ".sdf"


def candidate_colors(
    candidates: list[str], parent: str, groups: list[list[str]], survivors: list[str] | None
) -> dict[str, str]:
    """Candidate -> PyMOL colour. The parent has its own colour; a survivor takes its tie group's colour (best group
    first); a candidate the cut dropped is grey. With no survivor set (an unranked cut) every analogue takes its
    group's colour, since none was dropped. A candidate in no group (e.g. rejected before the cut) is grey."""
    in_group = {n: i for i, g in enumerate(groups) for n in g}
    out = {}
    for n in candidates:
        if n == parent:
            out[n] = PARENT_COLOR
        elif n in in_group and (survivors is None or n in survivors):
            out[n] = GROUP_COLORS[in_group[n] % len(GROUP_COLORS)]
        else:
            out[n] = DROPPED_COLOR
    return out


def pymol_session(
    candidates: list[str], parent: str, groups: list[list[str]], survivors: list[str] | None, unranked: bool
) -> str:
    """The .pml text. `candidates` are all names with poses, parent included; their files are
    `ligands/<ligand_filename>` and the receptor `receptor.pdb`, next to the script."""
    colors = candidate_colors(candidates, parent, groups, survivors)
    objs = {n: object_name(n) for n in candidates}
    if len(set(objs.values())) != len(objs):
        raise ValueError("two candidate names map to one PyMOL object name; rename one")
    lines = [
        "# smeltery run -> PyMOL. Run from this folder (`pymol session.pml`) or with `@session.pml`.",
        "# Legend (ligand carbons; heteroatoms keep element colours):",
        f"#   {PARENT_COLOR:<10} parent {parent}",
    ]
    for i, g in enumerate(groups):
        c = GROUP_COLORS[i % len(GROUP_COLORS)]
        who = ", ".join(n for n in g if n != parent)
        if survivors is None:
            note = "tie group (cut UNRANKED at the boundary: no survivor set)"
        else:
            kept = [n for n in g if n in survivors]
            note = "tie group, survivors" if len(kept) == len(g) else "tie group, dropped members grey"
        lines.append(f"#   {c:<10} {note} {i + 1}: {who}")
    lines.append(f"#   {DROPPED_COLOR:<10} dropped by the cut or in no tie group")
    if unranked:
        lines.append("# The cut is UNRANKED: groups are ties, there is no order within or at the boundary.")
    lines += ["", "reinitialize", "load receptor.pdb, receptor"]
    lines += [f"load ligands/{ligand_filename(n)}, {objs[n]}" for n in candidates]
    lines += ["", "hide everything", "show cartoon, receptor", "color grey80, receptor", "bg_color white"]
    lines.append(f"group ligands, {' '.join(objs.values())}")
    lines.append(f"select pocket, byres (receptor within {POCKET_CUTOFF_ANG:g} of ligands)")
    lines += ["show sticks, pocket", "color grey60, pocket and elem C", "show sticks, ligands"]
    lines += [f"color {colors[n]}, {objs[n]} and elem C" for n in candidates]
    lines += ["set stick_radius, 0.15, ligands", "disable pocket", "zoom ligands", ""]
    return "\n".join(lines)
