"""Cheap classical electrostatic pre-screen for ligand poses, ahead of QM.

No SCF: Coulomb's law over an embedded pose's own (overlap-filtered) pocket
charges, evaluated at the ligand's atom positions. For ranking/filtering many
poses down to the few worth `compute_binding_energy`. Load the pocket ONCE and
rank a whole ensemble with `batch_prescreen`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..model import ANGSTROM_TO_BOHR, PointCharge
from .embedding import EmbeddedLigand, embed_ligand
from .loader import PocketField


def pocket_field_at_atoms(
    charges: list[PointCharge],
    site_coords_angstrom: list[tuple[float, float, float]],
) -> np.ndarray:
    """Classical Coulomb potential and field at each site: an (N, 4) array of
    `[phi, Ex, Ey, Ez]` in Hartree atomic units. Charges and sites are in Angstrom."""
    sites = np.asarray(site_coords_angstrom, dtype=np.float64) * ANGSTROM_TO_BOHR
    q = np.array([c.q for c in charges], dtype=np.float64)
    src = np.array([c.xyz_ang for c in charges], dtype=np.float64) * ANGSTROM_TO_BOHR
    out = np.zeros((len(sites), 4), dtype=np.float64)
    for i, site in enumerate(sites):
        d = site[None, :] - src
        r = np.linalg.norm(d, axis=1)
        if np.any(r == 0.0):
            raise ValueError(
                f"site {i} coincides exactly with a pocket point charge -- "
                "check overlap filtering upstream (embed_ligand's overlap_cutoff_angstrom)."
            )
        out[i, 0] = np.sum(q / r)
        out[i, 1:4] = np.sum((q / r**3)[:, None] * d, axis=0)
    return out


@dataclass
class PrescreenResult:
    """Per-atom classical field plus one scalar `score` for ranking.

    `score` is sum_atom q_atom * phi_pocket(atom), in Hartree: the pocket's
    classical potential on the caller's atomic charges. NOT the QM binding energy
    (no polarization, exchange or dispersion); a proxy for ordering poses before QM.
    """

    field_at_atoms: np.ndarray  # (N, 4): [phi, Ex, Ey, Ez] per ligand atom, a.u.
    formal_charges: np.ndarray  # (N,) atomic charges used for `score`
    score: float  # Hartree; more negative = more electrostatically favorable
    n_pocket_charges: int  # overlap-filtered pocket charges that contributed


def prescreen_pose(embedded: EmbeddedLigand, atom_charges: list[float] | np.ndarray) -> PrescreenResult:
    """Classical field-based pre-screen for one embedded pose.

    `atom_charges` has one entry per ligand atom, in `embedded`'s atom order. It is
    required: a silent all-zero default would make every score 0. Raises ValueError
    if no pocket charges survive embedding or the charge count does not match.
    """
    if not embedded.charges:
        raise ValueError(
            "prescreen_pose requires embedded.charges (embed_ligand must be called with a pocket, "
            "and at least one pocket charge must survive overlap filtering) -- nothing to score against."
        )
    charges = np.asarray(atom_charges, dtype=np.float64)
    if len(charges) != len(embedded.coords_angstrom):
        raise ValueError(
            f"atom_charges has {len(charges)} entries but the ligand has "
            f"{len(embedded.coords_angstrom)} atoms -- must match 1:1."
        )
    # The SAME overlap-filtered charges the QM embedding would see, so the proxy and the
    # energy it ranks against agree on which pocket charges are in play.
    fld = pocket_field_at_atoms(embedded.charges, embedded.coords_angstrom)
    return PrescreenResult(
        field_at_atoms=fld,
        formal_charges=charges,
        score=float(np.dot(charges, fld[:, 0])),
        n_pocket_charges=len(embedded.charges),
    )


ChargeSource = Callable[[EmbeddedLigand], "list[float] | np.ndarray"]


@dataclass
class BatchPrescreenEntry:
    """One conformer's outcome. `result` is None and `error` set if it failed embedding or
    scoring (a batch over real conformers always has a few bad ones). `rank` is 1-based on
    the sorted list `batch_prescreen` returns; failed entries sort last with `rank=None`."""

    ligand_xyz: Path
    result: PrescreenResult | None
    error: str | None
    rank: int | None = field(default=None)


def batch_prescreen(
    pocket: PocketField,
    ligand_xyz_paths: list[str | Path],
    charge_source: ChargeSource,
    basis: str = "def2-svp",
    overlap_cutoff_angstrom: float = 1.5,
) -> list[BatchPrescreenEntry]:
    """Rank a pose ensemble against one pocket by prescreen score, most favorable first.

    `charge_source(embedded) -> atom_charges` is called per embedded pose; there is
    deliberately no default. Returns one entry per input path in RANKED order
    (failed poses last, in input order). Never raises for one bad conformer; check `.error`.
    """
    entries: list[BatchPrescreenEntry] = []
    for p in ligand_xyz_paths:
        p = Path(p)
        try:
            emb = embed_ligand(p, pocket=pocket, basis=basis, overlap_cutoff_angstrom=overlap_cutoff_angstrom)
            entries.append(BatchPrescreenEntry(p, prescreen_pose(emb, charge_source(emb)), None))
        except Exception as e:  # noqa: BLE001 -- one bad conformer must not abort the ranking
            entries.append(BatchPrescreenEntry(p, None, str(e)))
    ok = sorted((e for e in entries if e.error is None), key=lambda e: e.result.score)
    failed = [e for e in entries if e.error is not None]
    for i, e in enumerate(ok, start=1):
        e.rank = i
    return ok + failed
