"""Search boxes without hand-typed coordinates.

`Box` is what Vina needs; these build one from what a campaign actually has: the
bound ligand in a crystal structure, or a list of pocket residues. Pure PDB text
parsing, so they need neither Vina nor Meeko.

A box is a hypothesis about where the site is. `padding` is the margin around the
reference atoms (Angstrom); the box is never smaller than `min_size`, because a
box hugging a small ligand cannot hold a larger analogue.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .base import Box

_WATER = {"HOH", "WAT", "DOD", "TIP", "TIP3"}


def _atoms(pdb_text: str):
    """(record, atom name, resname, chain, resnum, element, xyz) per ATOM/HETATM line."""
    for line in pdb_text.splitlines():
        if not line.startswith(("ATOM", "HETATM")):
            continue
        try:
            xyz = (float(line[30:38]), float(line[38:46]), float(line[46:54]))
        except ValueError as e:
            raise ValueError(f"bad coordinates in PDB line: {line!r}") from e
        el = (line[76:78].strip() or line[12:16].strip().lstrip("0123456789")[:1]).upper()
        yield (
            line[:6].strip(),
            line[12:16].strip(),
            line[17:20].strip(),
            line[21].strip(),
            line[22:26].strip(),
            el,
            xyz,
        )


def box_from_coords(coords, padding: float = 8.0, min_size: float = 20.0) -> Box:
    """Box around `coords` ((n,3), Angstrom): their bounding box plus `padding` per side."""
    a = np.asarray(coords, dtype=float)
    if a.ndim != 2 or a.shape[1] != 3 or len(a) == 0:
        raise ValueError("need a non-empty (n, 3) coordinate array")
    lo, hi = a.min(axis=0), a.max(axis=0)
    size = np.maximum(hi - lo + 2 * padding, min_size)
    center = (lo + hi) / 2
    return Box(tuple(float(v) for v in center), tuple(float(v) for v in size))


def box_from_ligand(
    pdb_path: str | Path,
    resname: str | None = None,
    padding: float = 8.0,
    min_size: float = 20.0,
) -> Box:
    """Box around a co-crystallised ligand (HETATM, not water, heavy atoms).

    With several HETATM residues, `resname` must say which one: choosing for the
    caller is how a box ends up on a crystallisation additive.
    """
    het: dict[str, list] = {}
    for rec, _n, res, _c, _num, el, xyz in _atoms(Path(pdb_path).read_text()):
        if rec == "HETATM" and res not in _WATER and el != "H":
            het.setdefault(res, []).append(xyz)
    if not het:
        raise ValueError(f"{pdb_path}: no HETATM ligand found")
    if resname is None:
        if len(het) > 1:
            raise ValueError(f"{pdb_path}: several HETATM residues {sorted(het)}; pass resname=")
        resname = next(iter(het))
    if resname not in het:
        raise ValueError(f"{pdb_path}: no HETATM residue {resname!r}; found {sorted(het)}")
    return box_from_coords(het[resname], padding, min_size)


def box_from_residues(
    pdb_path: str | Path,
    residues: list[str] | tuple[str, ...],
    padding: float = 6.0,
    min_size: float = 20.0,
) -> Box:
    """Box around pocket residues given as 'A:42' (chain:resnum), heavy atoms only.

    The same selector syntax `prepare_receptor(flex_residues=...)` takes, so one
    list can both place the box and choose the flexible sidechains.
    """
    want = set()
    for r in residues:
        if ":" not in r:
            raise ValueError(f"residue {r!r} must look like 'A:42' (chain:resnum)")
        want.add(tuple(x.strip() for x in r.split(":", 1)))
    found, pts = set(), []
    for rec, _n, _res, chain, num, el, xyz in _atoms(Path(pdb_path).read_text()):
        if rec == "ATOM" and el != "H" and (chain, num) in want:
            found.add((chain, num))
            pts.append(xyz)
    missing = sorted(want - found)
    if missing:
        raise ValueError(f"{pdb_path}: residues not found: {[':'.join(m) for m in missing]}")
    return box_from_coords(pts, padding, min_size)
