"""Pocket field loader: PQR (or PDB via pdb2pqr30) to point charges.

Positions stay in Angstrom here; `PointCharge.as_ferric_bohr()` is the single
place they become Bohr. There is NO distance cutoff unless the caller passes one:
a default radius once produced a spurious -5.16 self-anchor artifact, and the
funnel driver once never passed charges at all (ferric#324), so the field's
provenance (input digest, pdb2pqr30 version, cutoff) rides along with it and is
written into the tier settings.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np

from ..model import PointCharge

PDB2PQR = "pdb2pqr30"


class Pdb2PqrUnavailableError(RuntimeError):
    """Raised when a PDB must be converted but `pdb2pqr30` is not on PATH."""


class PocketField(list):
    """`list[PointCharge]` (Angstrom) plus the provenance needed to reproduce it."""

    def __init__(self, charges=(), provenance: dict | None = None):
        super().__init__(charges)
        self.provenance: dict = dict(provenance or {})


def file_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def pdb2pqr_version() -> str | None:
    """Version string of the installed `pdb2pqr30`, or None if it is not usable.

    "On PATH" is not "installed": a pyenv shim is on PATH for every interpreter
    and exits 127 when the active one lacks the package. Any failure to run
    `--version` (non-zero exit, missing or non-executable file, empty output)
    therefore means not installed, and never raises.
    """
    exe = shutil.which(PDB2PQR)
    if exe is None:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, PermissionError, OSError):
        return None
    return (out.stdout or out.stderr).strip() or None


#: Residue names treated as solvent water by `drop_waters`.
WATER_RESIDUES = frozenset({"HOH", "WAT", "TIP3", "TIP3P", "SOL", "DOD"})


def _residue_name(line: str) -> str:
    """Residue name of a PQR ATOM/HETATM record.

    Read from the fixed PDB column range first, and fall back to whitespace splitting for writers
    that produce short lines. Charges are read from the END of the line precisely because the chain
    ID is optional, so a field index counted from the front cannot be trusted here either.

    The range is columns 18-21, not the canonical 18-20: a four-character residue name such as
    `TIP3` overflows the three-column field, and truncating it to `TIP` silently stops it matching
    `WATER_RESIDUES`. Column 21 is defined as blank in the PDB spec, so reading it costs nothing
    for a three-character name and rescues the four-character one.
    """
    if len(line) >= 21:
        name = line[17:21].strip()
        if name:
            return name
    f = line.split()
    return f[3] if len(f) > 3 else ""


def _is_water(line: str) -> bool:
    """Is this PQR record a solvent water?

    The name is matched both as read and with a trailing character removed. Column 21 is blank in
    the PDB spec, so reading 18-21 is what lets a four-character `TIP3` through -- but a writer that
    puts the chain ID there instead would hand us `GLYA` for a three-character name. Trying both
    spellings keeps a real four-character water matching without letting a chain ID defeat the test,
    and `WATER_RESIDUES` holds no name that is another name plus one character, so this cannot
    broaden into a false positive.
    """
    name = _residue_name(line)
    return name in WATER_RESIDUES or (len(name) == 4 and name[:3] in WATER_RESIDUES)


def parse_pqr(text: str, *, drop_waters: bool = False) -> list[PointCharge]:
    """Parse ATOM/HETATM records. Charge, radius are the last two columns, x y z the three before.

    Counted from the end of the line so that records with or without a chain ID
    (PQR is whitespace-delimited and writers differ) read identically.

    `drop_waters` skips solvent records (`WATER_RESIDUES`). It is off by default, so what a caller
    gets is still every charge in the file unless it asks otherwise. pdb2pqr30 writes crystal
    waters as **HETATM**, which this parser counts: for the 7LCJ pocket that is 6407 ATOM + 51
    HETATM = 6458 charges, and 17 waters supply all 51. A hand-rolled parser that reads only ATOM
    lines drops them silently, which is how they went unnoticed.
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.startswith(("ATOM", "HETATM")):
            continue
        if drop_waters and _is_water(line):
            continue
        f = line.split()
        try:
            x, y, z, q, _radius = (float(v) for v in f[-5:])
        except ValueError as e:
            raise ValueError(f"PQR line {lineno} is not a valid record: {line!r}") from e
        out.append(PointCharge(q, (x, y, z)))
    return out


def min_distance_to_field_ang(coords_ang, field) -> float:
    """Closest approach, in Angstrom, between a pose's atoms and any charge in `field`."""
    xyz = np.asarray(coords_ang, dtype=float)
    fc = np.asarray([c.xyz_ang for c in field], dtype=float)
    if xyz.size == 0 or fc.size == 0:
        raise ValueError("need both atoms and charges to measure a distance")
    return float(np.linalg.norm(xyz[:, None] - fc[None], axis=-1).min())


def check_reference_pose(coords_ang, field, *, min_ang: float = 1.0, name: str = "reference pose") -> float:
    """Assert an EXPERIMENTAL pose is not inside `field`, and return its closest approach.

    The control every pocket field should carry. A crystallographic ligand is a measured structure, so
    if *it* sits inside a charge the field is wrong -- not the pose. Because a point-charge field has
    no Pauli repulsion, nothing downstream will reveal that: the SCF converges cleanly and returns a
    large negative interaction energy.

    Two real defects this catches, both measured on the 7LCJ pocket on 2026-10-27:

    * **Crystal waters left in the field.** The parent read 1.905 A, against 2.126 A once waters were
      dropped, and 184 of 324 generated molecules "clashed" -- into water, not protein. One scored
      -1122.94 kcal/mol from a converged SCF; on the water-free field it is -19.39.
    * **A mis-prepared pocket.** An otherwise defensible OpenMM/pdbfixer prep put a guessed crystal-water
      hydrogen 1.441 A from the parent, because the prep had no knowledge that a ligand occupies the site.

    `min_ang` defaults to 1.0 A: below that an atom is inside a charge rather than in contact with it.
    Raises `ValueError` naming the distance, since a field that fails this is not worth computing on.
    """
    got = min_distance_to_field_ang(coords_ang, field)
    if got < min_ang:
        raise ValueError(
            f"{name} is {got:.3f} A from a pocket charge, below {min_ang} A: the FIELD is wrong, not the "
            "pose, because this structure is experimental. Check for crystal waters left in the field "
            "(load_pocket(drop_waters=True)) or a preparation that placed hydrogens into the ligand site."
        )
    return got


def _apply_cutoff(charges, cutoff_ang, center_ang):
    if cutoff_ang is None:
        return charges
    if center_ang is None:
        raise ValueError("a cutoff needs center_ang (e.g. the ligand centroid, in Angstrom)")
    c = np.asarray(center_ang, dtype=float)
    return [q for q in charges if float(np.linalg.norm(np.asarray(q.xyz_ang) - c)) <= cutoff_ang]


def load_pocket(
    path: str | Path,
    *,
    cutoff_ang: float | None = None,
    center_ang: tuple[float, float, float] | None = None,
    ff: str = "AMBER",
    drop_waters: bool = False,
) -> PocketField:
    """Load a pocket field from a .pqr, or from a .pdb through `pdb2pqr30 --ff=<ff>`.

    `cutoff_ang` defaults to None: every charge in the file is kept. Passing one
    is an explicit choice (it also needs `center_ang`) and is recorded in
    `provenance`, hence in `FieldInteraction.settings()`. `input_sha256` is the
    digest of the file given; `n_total` counts charges before any cutoff.

    `drop_waters` removes crystal-water charges (`WATER_RESIDUES`) and records
    `n_waters_dropped`. It is **off by default**, so this function's behaviour is unchanged unless
    a caller asks; but for a ligand-binding field it is usually the right choice, for two reasons
    measured on the 7LCJ pocket:

    * A ligand is expected to DISPLACE a crystal water in the site it occupies, so scoring it
      against one double-counts an occupant. 184 of 324 generated molecules here came within 1.0 A
      of a water charge while only **10** came that close to protein.
    * A crystal water's HYDROGEN positions are not experimental; they are whatever the preparation
      tool guessed, with no knowledge that a ligand occupies the site. Two defensible preps of this
      pocket differed by up to 13 kcal/mol in dE_int on the same pose, concentrated in water.

    The water OXYGEN is experimental, so dropping it is a modelling choice, not a correction -- it
    asserts the water is displaceable. `n_waters_dropped` keeps that choice visible in the record.
    """
    path = Path(path)
    prov: dict = {"input": path.name, "input_sha256": file_digest(path), "pdb2pqr_version": None}
    suffix = path.suffix.lower()
    if suffix == ".pqr":
        text = path.read_text()
    elif suffix == ".pdb":
        version = pdb2pqr_version()
        if version is None:
            found = shutil.which(PDB2PQR)
            why = (
                f"`{PDB2PQR}` is not on PATH"
                if found is None
                else f"`{found}` is on PATH but `--version` fails (a broken shim?)"
            )
            raise Pdb2PqrUnavailableError(
                f"{path.name} is a PDB and needs a working `{PDB2PQR}` to assign charges: {why}; "
                "install it (pip install pdb2pqr) or pass a pre-made .pqr"
            )
        exe = shutil.which(PDB2PQR)
        prov["pdb2pqr_version"] = version
        prov["pdb2pqr_ff"] = ff
        with tempfile.TemporaryDirectory() as tmp:
            pqr = Path(tmp) / "out.pqr"
            subprocess.run([exe, f"--ff={ff}", str(path), str(pqr)], capture_output=True, text=True, check=True)
            text = pqr.read_text()
    else:
        raise ValueError(f"unsupported pocket file {path.name!r}: expected .pqr or .pdb")
    charges = parse_pqr(text)
    if not charges:
        raise ValueError(f"{path.name}: no ATOM/HETATM charges found")
    prov["n_total"] = len(charges)
    prov["drop_waters"] = drop_waters
    if drop_waters:
        charges = parse_pqr(text, drop_waters=True)
        prov["n_waters_dropped"] = prov["n_total"] - len(charges)
        prov["water_residues"] = sorted(WATER_RESIDUES)
    prov["cutoff_ang"] = cutoff_ang
    prov["center_ang"] = None if center_ang is None else tuple(center_ang)
    kept = _apply_cutoff(charges, cutoff_ang, center_ang)
    prov["n_charges"] = len(kept)
    return PocketField(kept, prov)
