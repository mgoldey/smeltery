"""AIMNet2 charge probe (plan: docs/benchmarks/aimnet2_anion_plan.md). Same species, geometries and DFT reference
as `ani2x_charge_probe.py`, plus benzoic acid / benzoate, isodesmic reactions, ordinary-error control,
relaxed geometries, finite-difference force agreement and the charge-used check.

Run (needs the `ml-potential` extra with aimnet, ferric, rdkit, xtb on PATH):
    OMP_NUM_THREADS=1 python examples/aimnet2_charge_probe.py --out aimnet2_charge_probe.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import ferric
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.model import Pose
from smeltery.providers import FORCE_FD_RELATIVE_BAR, check_forces, relax
from smeltery.providers.aimnet2 import Aimnet2

HA2KCAL = 627.5094740631
BAR_KCAL = 5.0  # plan item 6
SPECIES = {
    "acetic acid": ("CC(=O)O", 0),
    "acetate": ("CC(=O)[O-]", -1),
    "benzoic acid": ("OC(=O)c1ccccc1", 0),
    "benzoate": ("[O-]C(=O)c1ccccc1", -1),
    "methanol": ("CO", 0),
    "methoxide": ("C[O-]", -1),
    "methylamine": ("CN", 0),
    "methylammonium": ("C[NH3+]", 1),
    "ethanol": ("CCO", 0),
    "dimethyl ether": ("COC", 0),
}
PAIRS = [
    ("acetic acid", "acetate"),
    ("benzoic acid", "benzoate"),
    ("methanol", "methoxide"),
    ("methylamine", "methylammonium"),
]
CONTROL = ("ethanol", "dimethyl ether")
ISODESMIC = [  # acid + methoxide -> conjugate base + methanol, written as (reactants, products)
    (("acetate", "methanol"), ("acetic acid", "methoxide")),
    (("benzoate", "methanol"), ("benzoic acid", "methoxide")),
]


def xtb_geometry(smiles: str, charge: int):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=1)
    AllChem.MMFFOptimizeMolecule(mol)
    syms = [a.GetSymbol() for a in mol.GetAtoms()]
    rows = "\n".join(f"{s} {x} {y} {z}" for s, (x, y, z) in zip(syms, mol.GetConformer().GetPositions()))
    with tempfile.TemporaryDirectory() as d:
        Path(d, "in.xyz").write_text(f"{len(syms)}\n\n{rows}\n")
        subprocess.run(
            ["xtb", "in.xyz", "--opt", "tight", "--chrg", str(charge), "--gfn", "2"],
            cwd=d,
            check=True,
            capture_output=True,
        )
        out = Path(d, "xtbopt.xyz").read_text().splitlines()[2:]
    return tuple(syms), np.array([[float(v) for v in r.split()[1:4]] for r in out]), mol


def dft_energy(syms, xyz, charge: int) -> float:
    rows = "\n".join(f"{s} {x:.8f} {y:.8f} {z:.8f}" for s, (x, y, z) in zip(syms, xyz))
    mol = ferric.Molecule.from_xyz_string(f"{len(syms)}\n\n{rows}\n", charge, 1)
    return ferric.run_dft(mol, ferric.BasisSet.bundled("aug-cc-pvdz"), functional="B3LYP").total_energy * HA2KCAL


def bonds(mol):
    return [(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in mol.GetBonds()]


def max_bond_error(mol, xyz_a, xyz_b) -> float:
    return max(abs(np.linalg.norm(xyz_a[i] - xyz_a[j]) - np.linalg.norm(xyz_b[i] - xyz_b[j])) for i, j in bonds(mol))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="cpu")
    a = ap.parse_args()
    charges = frozenset({-1, 0, 1})  # widened for the measurement only; recorded in the output
    model = Aimnet2(device=a.device, supported_charge_states=charges)
    rec: dict = {"settings": model.settings(), "species": {}, "bar_kcal_mol": BAR_KCAL}
    geo = {}
    for name, (smiles, q) in SPECIES.items():
        syms, xyz, mol = xtb_geometry(smiles, q)
        geo[name] = (syms, xyz, mol, q)
        pose = Pose(syms, xyz)
        res = model.energy_and_forces(pose, charge=q)
        rec["species"][name] = {"charge": q, "dft": dft_energy(syms, xyz, q), "model": res.energy}
        print(name, rec["species"][name], flush=True)
    # 1: is the charge used?
    syms, xyz, _, _ = geo["acetate"]
    e_m1 = model.energy_and_forces(Pose(syms, xyz), charge=-1).energy
    e_0 = model.energy_and_forces(Pose(syms, xyz), charge=0).energy
    rec["charge_used"] = {"acetate_coords_charge_-1": e_m1, "acetate_coords_charge_0": e_0, "differs": e_m1 != e_0}
    # 2: pair errors, and the control
    E = rec["species"]
    rec["pairs"] = {}
    for n, o in [*PAIRS, CONTROL]:
        d_dft, d_model = E[o]["dft"] - E[n]["dft"], E[o]["model"] - E[n]["model"]
        rec["pairs"][f"{o} - {n}"] = {"dft": d_dft, "model": d_model, "error": d_model - d_dft}
    # 3: isodesmic reaction errors
    rec["isodesmic"] = {}
    for reac, prod in ISODESMIC:
        d = {k: sum(E[s][k] for s in prod) - sum(E[s][k] for s in reac) for k in ("dft", "model")}
        rec["isodesmic"][" + ".join(reac) + " -> " + " + ".join(prod)] = {**d, "error": d["model"] - d["dft"]}
    # 5: relaxed geometry error against B3LYP minimum
    rec["geometry"] = {}
    for name in ("acetate", "benzoate", "methoxide", "methylammonium", "acetic acid", "benzoic acid"):
        syms, xyz, mol, q = geo[name]
        m_min = relax(model, Pose(syms, xyz), charge=q, fmax=0.05).coords_ang
        rec["geometry"][name] = {"model_vs_xtb_max_bond_change_ang": max_bond_error(mol, m_min, xyz)}
    # 7: forces vs finite difference at a displaced geometry
    rng = np.random.default_rng(0)
    syms, xyz, _, q = geo["acetate"]
    disp = Pose(syms, xyz + rng.normal(scale=0.05, size=xyz.shape))
    rec["force_fd"] = {"bar": FORCE_FD_RELATIVE_BAR, "relative_error": {}}
    for step in (1e-4, 1e-3, 1e-2):
        rec["force_fd"]["relative_error"][str(step)] = check_forces(model, disp, charge=q, step_ang=step)
    a.out.write_text(json.dumps(rec, indent=1))
    print(json.dumps({k: rec[k] for k in ("charge_used", "pairs", "isodesmic", "force_fd")}, indent=1))


if __name__ == "__main__":
    main()
