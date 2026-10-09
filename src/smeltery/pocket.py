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

from .model import PointCharge

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


def parse_pqr(text: str) -> list[PointCharge]:
    """Parse ATOM/HETATM records. Charge, radius are the last two columns, x y z the three before.

    Counted from the end of the line so that records with or without a chain ID
    (PQR is whitespace-delimited and writers differ) read identically.
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.startswith(("ATOM", "HETATM")):
            continue
        f = line.split()
        try:
            x, y, z, q, _radius = (float(v) for v in f[-5:])
        except ValueError as e:
            raise ValueError(f"PQR line {lineno} is not a valid record: {line!r}") from e
        out.append(PointCharge(q, (x, y, z)))
    return out


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
) -> PocketField:
    """Load a pocket field from a .pqr, or from a .pdb through `pdb2pqr30 --ff=<ff>`.

    `cutoff_ang` defaults to None: every charge in the file is kept. Passing one
    is an explicit choice (it also needs `center_ang`) and is recorded in
    `provenance`, hence in `FieldInteraction.settings()`. `input_sha256` is the
    digest of the file given; `n_total` counts charges before any cutoff.
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
    prov["cutoff_ang"] = cutoff_ang
    prov["center_ang"] = None if center_ang is None else tuple(center_ang)
    kept = _apply_cutoff(charges, cutoff_ang, center_ang)
    prov["n_charges"] = len(kept)
    return PocketField(kept, prov)
