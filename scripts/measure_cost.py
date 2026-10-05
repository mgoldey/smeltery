"""Record wall-clock measurements of the FieldInteraction tier (one pose = vacuum + field RHF).

Writes src/smeltery/data/cost_measurements.json. Run on a quiet box:

    RAYON_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run python scripts/measure_cost.py

Single-threaded on purpose: ferric's default is multithreaded, so its wall time
depends on how many cores are free (measured: 1.2 s wall / 11 s CPU on a loaded
12-core box vs 7.1 s wall single-threaded, stable to <2%). The prediction
is for RAYON_NUM_THREADS=1.

Samples are tagged `calibration` (the cost model's exponent is fit on these only)
or `validation` (held out; never used in the fit, only to check the prediction).
Nothing here is tuned: change the molecule lists, not the numbers.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import time
from pathlib import Path

import ferric

from smeltery import Candidate, PointCharge, ferric_identity
from smeltery.cost import sto3g_basis_functions
from smeltery.tiers import FieldInteraction, PairedPoses

REPEATS = 5
FIELD = [PointCharge(1.0, (3.0, 0.0, 0.0)), PointCharge(-0.5, (0.0, 3.5, 0.0))]
SAMPLES = [
    ("calibration", "ethanol", "CCO"),
    ("calibration", "benzoic acid", "OC(=O)c1ccccc1"),
    ("calibration", "ibuprofen", "CC(C)Cc1ccc(cc1)C(C)C(=O)O"),
    ("validation", "aspirin", "CC(=O)Oc1ccccc1C(=O)O"),
    ("validation", "4-fluorobenzoic acid", "OC(=O)c1ccc(F)cc1"),
]


def main() -> None:
    if os.environ.get("RAYON_NUM_THREADS") != "1":
        raise SystemExit("run with RAYON_NUM_THREADS=1 (see module docstring)")
    tier = FieldInteraction()
    bs = ferric.BasisSet.bundled(tier.basis)
    charges = [c.as_ferric_bohr() for c in FIELD]
    kw = {"energy_conv": tier.energy_conv, "density_conv": tier.density_conv}
    out = []
    for role, name, smiles in SAMPLES:
        cand = Candidate(name, smiles)
        PairedPoses(n_poses=1).run([cand], {"parent": cand})
        pose = cand.poses[0]
        mol = ferric.Molecule.from_xyz_string(pose.to_xyz(name))
        secs, iters = [], 0
        for _ in range(REPEATS):
            t = time.perf_counter()
            vac = ferric.run_rhf(mol, bs, **kw)
            fld = ferric.run_rhf(mol, bs, point_charges=charges, **kw)
            secs.append(time.perf_counter() - t)
            iters = vac.iterations + fld.iterations
        out.append({
            "role": role, "name": name, "smiles": smiles, "n_atoms": len(pose.symbols),
            "n_basis_functions": sto3g_basis_functions(list(pose.symbols)),
            "scf_iterations": iters, "seconds_per_pose": statistics.median(secs),
            "seconds_all_repeats": secs,
        })
        print(out[-1])
    record = {
        "quantity": "FieldInteraction wall time per pose (vacuum + point-charge-field RHF)",
        "basis": tier.basis, "settings": tier.settings(), "ferric": ferric_identity(),
        "machine": f"{platform.processor() or platform.machine()}, RAYON_NUM_THREADS=1, OPENBLAS_NUM_THREADS=1",
        "repeats": REPEATS, "statistic": "median", "samples": out,
    }
    path = Path(__file__).resolve().parents[1] / "src/smeltery/data/cost_measurements.json"
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(record, indent=2) + "\n")


if __name__ == "__main__":
    main()
