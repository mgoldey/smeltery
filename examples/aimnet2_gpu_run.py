"""AIMNet2 on the danuglipron anion, CPU vs GPU (plan: docs/benchmarks/aimnet2_anion_plan.md, "GPU run").

Run:  python examples/aimnet2_gpu_run.py --device cuda:0 --out FILE
One run, one machine, no statistics. The charge states are widened to {-1, 0} for this call only: the run
measures agreement of two devices, it does not assert the anion is supported (see docs/benchmarks/aimnet2_anion.md).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.model import Pose
from smeltery.providers.aimnet2 import Aimnet2

PARENT_SMILES = "C1CO[C@@H]1CN2C3=C(C=CC(=C3)C(=O)O)N=C2CN4CCC(CC4)C5=NC(=CC=C5)OCC6=C(C=C(C=C6)C#N)F"


def deprotonated(smiles: str) -> str:
    return smiles.replace("C(=O)O)", "C(=O)[O-])")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--device", default="cuda:0")
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    smi = deprotonated(PARENT_SMILES)
    mol = Chem.AddHs(Chem.MolFromSmiles(smi))
    AllChem.EmbedMolecule(mol, randomSeed=20261005)
    AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    pose = Pose(tuple(x.GetSymbol() for x in mol.GetAtoms()), mol.GetConformer().GetPositions())
    rec = {"smiles": smi, "n_atoms": len(pose.symbols), "net_charge": -1}
    out = {}
    for dev in ("cpu", a.device):
        p = Aimnet2(device=dev, supported_charge_states=frozenset({-1, 0}))
        p.energy_and_forces(pose, charge=-1)  # warm-up (kernel build / first-call overhead)
        t = time.perf_counter()
        r = p.energy_and_forces(pose, charge=-1)
        out[dev] = {"energy": r.energy, "forces": r.forces, "wall_s": time.perf_counter() - t, "settings": p.settings()}
    c, g = out["cpu"], out[a.device]
    rec["devices"] = {
        k: {"energy_kcal_mol": v["energy"], "wall_s": v["wall_s"], "torch": v["settings"]["torch"]}
        for k, v in out.items()
    }
    rec["energy_difference_kcal_mol"] = g["energy"] - c["energy"]
    rec["max_force_difference_kcal_mol_ang"] = float(np.abs(g["forces"] - c["forces"]).max())
    rec["max_force_component_kcal_mol_ang"] = float(np.abs(c["forces"]).max())
    a.out.write_text(json.dumps(rec, indent=1))
    print(json.dumps(rec, indent=1))


if __name__ == "__main__":
    main()
