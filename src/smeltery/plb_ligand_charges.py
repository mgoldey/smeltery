"""Ligand-side partial charge models on PLB poses (issue #29, ligand side): pure functions, no quantum chemistry.

WHAT THIS IS NOT. smeltery's `FieldInteraction` (the PLB ddE) is a ligand wavefunction in a fixed protein point-charge
field; it uses NO ligand partial charge model. Ligand partial charges exist in the pocket package only as diagnostics
(`pocket.energy.compute_charges`, returned by `compute_binding_energy`) and never enter an energy. The quantity compared
here is therefore a SURROGATE with its own definition, not the tier's ddE:

    E_es(model, pose) = sum_i q_i(model) * phi(r_i),   phi(r) = sum_j Q_j / |r - R_j|   (kcal/mol)

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
    """E is (n_ligands, n_poses), rows in `names` order. Per analogue: pose mean and SEM of E(L,k)-E(parent,k)."""
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
    """Two models (model -> name -> value) through `gates.charge_sensitivity`, plus pair-order flips."""
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


#: Verdict thresholds fixed in the plan (docs/benchmarks/plb_ligand_charges_plan.md, section 3); n = 12, 66 pairs.
AGREE = {"spearman_min": 0.90, "sign_flips_max": 1, "pair_flips_max": 6}
DISAGREE = {"spearman_below": 0.70, "sign_flips_min": 3, "pair_flips_min": 17}
N_BOOT = 10_000
BOOT_SEED = 20261011


def verdict(pc: dict[str, Any]) -> str:
    """'agree' / 'disagree' / 'between' for one `pair_comparison`, by the plan's pre-declared thresholds."""
    if (
        pc["spearman"] >= AGREE["spearman_min"]
        and pc["n_sign_flips"] <= AGREE["sign_flips_max"]
        and pc["n_pair_order_flips"] <= AGREE["pair_flips_max"]
    ):
        return "agree"
    if (
        pc["spearman"] < DISAGREE["spearman_below"]
        or pc["n_sign_flips"] >= DISAGREE["sign_flips_min"]
        or pc["n_pair_order_flips"] >= DISAGREE["pair_flips_min"]
    ):
        return "disagree"
    return "between"


def es_energies(rec: dict) -> dict[str, np.ndarray]:
    """model -> array (n_ligands, n_poses) of sum_i q_i phi_i, recomputed from the stored charges and potentials."""
    names = rec["ligands"]
    return {
        m: np.array(
            [
                [es_energy(rec["charges"][m][n][k], rec["phi_kcal_mol_per_e"][n][k]) for k in range(rec["n_poses"])]
                for n in names
            ]
        )
        for m in rec["models"]
    }


def analyse_record(rec: dict, stored: dict) -> dict[str, Any]:
    """Every statistic of the plan, from the per-atom charges and potentials in `rec` and the stored QM run `stored`."""
    from . import plb_measure as pm

    names, parent, npose, models = rec["ligands"], rec["reference"], rec["n_poses"], rec["models"]
    E = es_energies(rec)
    dde = {m: paired_ddE_es(E[m], names, parent) for m in models}
    means = {m: {n: v["mean"] for n, v in dde[m].items()} for m in models}
    qm_E = np.array([stored["results"]["dE_int_kcal_mol"]["amber14"][n][:npose] for n in names])
    qm = {n: v["mean"] for n, v in paired_ddE_es(qm_E, names, parent).items()}
    pairs = {}
    for a, b in itertools.combinations(models, 2):
        pc = pair_comparison(means, a, b)
        x = [means[a][n] for n in sorted(means[a])]
        y = [means[b][n] for n in sorted(means[b])]
        pc["spearman_ci95"] = pm.bootstrap_ci(x, y, "spearman", n_boot=N_BOOT, seed=BOOT_SEED)
        pc["kendall"] = pm.kendall_tau_b(x, y)
        d = E[b] - E[a]
        pc["raw_E_es_shift_mean_kcal_mol"] = float(d.mean())
        pc["raw_E_es_shift_sd_kcal_mol"] = float(d.std(ddof=1))
        pc["verdict"] = verdict(pc)
        pairs[f"{a}|{b}"] = pc
    carb = {}
    for m in models:
        ok = tot = 0
        for n in names:
            for c, o in rec["carbonyl_atom_pairs"][n]:
                for k in range(npose):
                    q = rec["charges"][m][n][k]
                    tot += 1
                    ok += int(q[c] > 0 and q[o] < 0)
        carb[m] = {"n_carbonyl_ligand_poses": tot, "n_polarity_correct": ok}
    sums = {
        m: max(abs(sum(rec["charges"][m][n][k]) - rec["inputs"]["net_charge"][n]) for n in names for k in range(npose))
        for m in models
    }
    return {
        "schema": "smeltery-plb-ligand-charges-analysis/1",
        "n_analogues": len(names) - 1,
        "n_poses": npose,
        "models": models,
        "ddE_es_kcal_mol": dde,
        "pairs": pairs,
        "qm_ddE_stored_amber_kcal_mol": qm,
        "vs_qm": {
            m: {
                "spearman": spearman_vs(means[m], qm),
                "n_same_sign": sum(1 for n in qm if np.sign(means[m][n]) == np.sign(qm[n])),
            }
            for m in models
        },
        "mean_paired_sem_kcal_mol": {m: float(np.mean([v["sem"] for v in dde[m].values()])) for m in models},
        "carbonyl_polarity": carb,
        "max_abs_charge_sum_error_e": sums,
        "thresholds": {"agree": AGREE, "disagree": DISAGREE},
    }
