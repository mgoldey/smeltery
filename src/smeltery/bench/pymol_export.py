"""The PyMOL session script for a run's "Open in PyMOL" bundle: a pure function from names and the cut to .pml text.

The bundle is `session.pml`, `receptor.pdb` and `ligands/<candidate>.sdf`; the script first cds to its own folder
(`cmd._pymol.__script__`, which PyMOL sets for `pymol session.pml`, `@`, and File > Open), then loads them by relative
path. Candidate names are only ever used through `ligand_stems`, never raw, in file names and commands."""

from __future__ import annotations

import re

PARENT_COLOR = "magenta"
DROPPED_COLOR = "grey70"
#: Tie-group colours, best group first (cycled if a run has more groups than colours).
GROUP_COLORS = ("marine", "green", "orange", "cyan", "purpleblue", "teal", "salmon", "olive")
POCKET_CUTOFF_ANG = 5.0


def ligand_stems(candidates: list[str]) -> dict[str, str]:
    """Candidate -> a PyMOL-safe, filesystem-safe stem: the name with anything but [A-Za-z0-9_] as `_`. Stems that
    clash (compared case-insensitively, since unzip on macOS/Windows folds case) get `_2`, `_3`, ... suffixes."""
    out: dict[str, str] = {}
    taken: set[str] = set()
    for n in candidates:
        base = re.sub(r"[^A-Za-z0-9_]", "_", n) or "_"
        stem, k = base, 1
        while stem.lower() in taken:
            k += 1
            stem = f"{base}_{k}"
        taken.add(stem.lower())
        out[n] = stem
    return out


def ligand_filename(stem: str) -> str:
    return f"ligands/{stem}.sdf"


def _label(name: str) -> str:
    """A name safe inside a `#` comment line: control characters (newlines would start a new command) dropped."""
    return "".join(ch if ch.isprintable() else " " for ch in name)


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
    stems = ligand_stems(candidates)
    objs = {n: "lig_" + stems[n] for n in candidates}
    parent_label, labels = _label(parent), {n: _label(n) for n in candidates}
    lines = [
        "# smeltery run -> PyMOL. The script cds to its own folder, then loads the files next to it.",
        "# `reinitialize` below wipes whatever session is open: run this in a fresh PyMOL.",
        "# Legend (ligand carbons; heteroatoms keep element colours):",
        f"#   {PARENT_COLOR:<10} parent {parent_label}",
    ]
    for i, g in enumerate(groups):
        c = GROUP_COLORS[i % len(GROUP_COLORS)]
        who = ", ".join(labels.get(n) or _label(n) for n in g if n != parent)
        if survivors is None:
            note = "tie group (cut UNRANKED at the boundary: no survivor set)"
        else:
            kept = [n for n in g if n in survivors]
            note = "tie group, survivors" if len(kept) == len(g) else "tie group, dropped members grey"
        lines.append(f"#   {c:<10} {note} {i + 1}: {who}")
    lines.append(f"#   {DROPPED_COLOR:<10} dropped by the cut or in no tie group")
    if unranked:
        lines.append("# The cut is UNRANKED: groups are ties, there is no order within or at the boundary.")
    lines += [
        "",
        "reinitialize",
        "python",
        "import os; from pymol import cmd",
        'cmd.cd(os.path.dirname(os.path.abspath(cmd._pymol.__script__)) or ".")',
        "python end",
        "load receptor.pdb, receptor",
    ]
    lines += [f"load {ligand_filename(stems[n])}, {objs[n]}" for n in candidates]
    lines += ["", "hide everything", "show cartoon, receptor", "color grey80, receptor", "bg_color white"]
    lines.append(f"group ligands, {' '.join(objs.values())}")
    lines.append(f"select pocket, byres (receptor within {POCKET_CUTOFF_ANG:g} of ligands)")
    lines += ["show sticks, pocket", "color grey60, pocket and elem C", "show sticks, ligands"]
    lines += [f"color {colors[n]}, {objs[n]} and elem C" for n in candidates]
    lines += ["set stick_radius, 0.15, ligands", "disable pocket", "zoom ligands", ""]
    return "\n".join(lines)
