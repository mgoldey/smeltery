"""Ligand-side partial charge models on PLB poses (issue #29, ligand side): pure functions, no quantum chemistry.

WHAT THIS IS NOT. smeltery's `FieldInteraction` (the PLB ddE) is a ligand wavefunction in a fixed protein point-charge
field; it uses NO ligand partial charge model. Ligand partial charges exist in the pocket package only as diagnostics
(`pocket.energy.compute_charges`, returned by `compute_binding_energy`) and never enter an energy. The quantity compared
here is therefore a SURROGATE with its own definition, not the tier's ddE:

    E_es(model, pose) = sum_i q_i(model) * phi(r_i)          phi(r) = sum_j Q_j / |r - R_j|   (kcal/mol, pocket field Q_j)

the first-order electrostatic energy of the ligand's atom-centred charges in the same pocket field the tier uses, and
ddE_es(ligand) = mean over poses of [E_es(ligand, k) - E_es(parent, k)]. The plan is
docs/benchmarks/plb_ligand_charges_plan.md.
"""

from __future__ import annotations

import itertools
import math
from typing import Any

import numpy as np

from .gates import charge_sensitivity, spearman
from .model import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL, Candidate

#: kcal/mol * Angstrom / e^2  (Hartree*bohr / e^2 converted)
COULOMB_KCAL_ANG = HARTREE_TO_KCAL / ANGSTROM_TO_BOHR


def field_potential(coords_ang, field_q, field_xyz_ang) -> np.ndarray:
    """Potential of the pocket point charges at each ligand atom, kcal/mol per unit charge, shape (n_atoms,)."""
    x = np.asarray(coords_ang, dtype=float)[:, None, :]
    f = np.asarray(field_xyz_ang, dtype=float)[None, :, :]
    d = np.sqrt(((x - f) ** 2).sum(-1))
    if not np.all(d > 0):
        raise ValueError("a ligand atom sits exactly on a pocket charge")
    return COULOMB_KCAL_ANG * (np.asarray(field_q, dtype=float)[None, :] / d).sum(-1)


def es_energy(charges, phi) -> float:
    """sum_i q_i phi_i, kcal/mol."""
    q, p = np.asarray(charges, dtype=float), np.asarray(phi, dtype=float)
    if q.shape != p.shape:
        raise ValueError(f"{q.shape} charges but {p.shape} potentials")
    return float((q * p).sum())


def paired_ddE_es(E: np.ndarray, names: list[str], parent: str) -> dict[str, dict[str, float]]:
    """E has shape (n_ligands, n_poses), rows in `names` order. Per analogue: pose-mean and SEM of E(lig,k) - E(parent,k)."""
    E = np.asarray(E, dtype=float)
    i0 = names.index(parent)
    out = {}
    for i, n in enumerate(names):
        if n == parent:
            continue
        d = E[i] - E[i0]
        sem = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("nan")
        out[n] = {"mean": float(d.mean()), "sem": sem}
    return out


def pair_comparison(values: dict[str, dict[str, float]], a: str, b: str) -> dict[str, Any]:
    """Two models' per-ligand values (model -> name -> value) through `gates.charge_sensitivity`, plus pair-order flips."""
    names = sorted(values[a])
    if sorted(values[b]) != names:
        raise ValueError("the two models do not score the same ligands")
    rep = charge_sensitivity(
        [Candidate(n, "C") for n in names], "ddE_es", lambda c, q: values[a][c.name], lambda c, q: values[b][c.name]
    )
    va, vb = np.array(rep.values_a), np.array(rep.values_b)
    flips = sum(1 for i, j in itertools.combinations(range(len(names)), 2) if (va[i] - va[j]) * (vb[i] - vb[j]) < 0)
    return {
        "models": [a, b],
        "n": rep.n,
        "n_sign_flips": rep.n_sign_flips,
        "n_pairs": rep.n * (rep.n - 1) // 2,
        "n_pair_order_flips": flips,
        "spearman": rep.spearman,
        "floor_kcal_mol": rep.floor,
        "shift_b_minus_a": rep.deltas,
        "sd_of_shift_kcal_mol": float(np.std(list(rep.deltas.values()), ddof=1)),
    }


def spearman_vs(values: dict[str, float], other: dict[str, float]) -> float:
    names = sorted(values)
    return spearman([values[n] for n in names], [other[n] for n in names])
