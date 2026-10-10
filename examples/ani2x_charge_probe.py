"""Probe: does ANI-2x (via OpenMM-ML) know about net charge? Reproduces the pair table in docs/potentials.md.

Run (needs the `ml-potential` extra, ferric, rdkit, and an `xtb` on PATH; set LD_LIBRARY_PATH for libxtb if needed):
    OMP_NUM_THREADS=1 python examples/ani2x_charge_probe.py

Per species: GFN2-xtb geometry (with the right charge), B3LYP/aug-cc-pVDZ single point (ferric), ANI-2x single
point on the SAME geometry. For each charged/neutral pair it prints the energy difference both ways. With
E(H+) = 0 the DFT difference is a proton-removal (or addition) energy; the ANI difference has no such meaning
because the model has no charge input. A neutral isomer pair is the control for ANI's ordinary error.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import ferric
import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.model import Pose
from smeltery.providers.openmm_ml import OpenMMMLAni2x

HA2KCAL = 627.5094740631
SPECIES = {
    "acetic acid": ("CC(=O)O", 0),
    "acetate": ("CC(=O)[O-]", -1),
    "methanol": ("CO", 0),
    "methoxide": ("C[O-]", -1),
    "methylamine": ("CN", 0),
    "methylammonium": ("C[NH3+]", 1),
    "ethanol": ("CCO", 0),
    "dimethyl ether": ("COC", 0),
}
PAIRS = [("acetic acid", "acetate"), ("methanol", "methoxide"), ("methylamine", "methylammonium")]


def xtb_geometry(smiles: str, charge: int):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    AllChem.EmbedMolecule(mol, randomSeed=1)
    AllChem.MMFFOptimizeMolecule(mol)
    syms = [a.GetSymbol() for a in mol.GetAtoms()]
    rows = "\n".join(f"{s} {x} {y} {z}" for s, (x, y, z) in zip(syms, mol.GetConformer().GetPositions()))
    with tempfile.TemporaryDirectory() as d:
        Path(d, "in.xyz").write_text(f"{len(syms)}\n\n{rows}\n")
        cmd = ["xtb", "in.xyz", "--opt", "tight", "--chrg", str(charge), "--gfn", "2"]
        subprocess.run(cmd, cwd=d, check=True, capture_output=True)
        out = Path(d, "xtbopt.xyz").read_text().splitlines()[2:]
    return tuple(syms), np.array([[float(v) for v in r.split()[1:4]] for r in out])


def dft_energy(syms, xyz, charge: int) -> float:
    rows = "\n".join(f"{s} {x:.8f} {y:.8f} {z:.8f}" for s, (x, y, z) in zip(syms, xyz))
    mol = ferric.Molecule.from_xyz_string(f"{len(syms)}\n\n{rows}\n", charge, 1)
    return ferric.run_dft(mol, ferric.BasisSet.bundled("aug-cc-pvdz"), functional="B3LYP").total_energy * HA2KCAL


def main() -> None:
    ani = OpenMMMLAni2x()
    e = {}
    for name, (smiles, q) in SPECIES.items():
        syms, xyz = xtb_geometry(smiles, q)
        # The charge is deliberately NOT passed: the provider refuses q != 0, and the unguarded stack ignores it.
        e[name] = (dft_energy(syms, xyz, q), ani.energy_and_forces(Pose(syms, xyz)).energy)
    for neutral, other in PAIRS + [("ethanol", "dimethyl ether")]:
        dft = e[other][0] - e[neutral][0]
        model = e[other][1] - e[neutral][1]
        print(f"{other} - {neutral}: DFT {dft:8.1f}  ANI-2x {model:8.1f}  (ANI - DFT {model - dft:+7.1f}) kcal/mol")


if __name__ == "__main__":
    main()
