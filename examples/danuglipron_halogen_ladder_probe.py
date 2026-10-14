"""One DIIS-failed halogen case through single rungs of ferric's convergence ladder (plan:
docs/benchmarks/danuglipron_halogen_ladder_probe_plan.md). Needs the `prepare` output of danuglipron_halogen_accel.py.

Run (dev-wheel env):  python examples/danuglipron_halogen_ladder_probe.py --inputs DIR --out FILE
"""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import resource
import time
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

CASE = "bnz-C34-Cl"
CPU_LIMIT_S = 2500
HARTREE_TO_KCAL = 627.5094740631
PROBES = {
    "A_rung1": {"diis": "adiis", "max_iter": 60},
    "B_rung2": {"diis": "adiis", "level_shift": 0.5, "max_iter": 60},
    "C_rung3": {"diis": "adiis", "level_shift": 1.0, "soscf": True, "max_iter": 80},
}


def _probe(q, smiles, xyz, charges, kw_probe):
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_S, CPU_LIMIT_S + 60))
    import ferric

    t0 = time.process_time()
    mol = ferric.Molecule.from_xyz_string(xyz, charge=_net_charge(smiles))
    bs = ferric.BasisSet.bundled("sto-3g")
    kw = {"energy_conv": 1e-6, "density_conv": 1e-8}
    fld = ferric.run_rhf(mol, bs, point_charges=charges, **kw, **kw_probe)
    rec = {
        "field_converged": bool(fld.converged),
        "field_iterations": int(fld.iterations),
        "exit": str(fld.exit_reason),
    }
    if fld.converged:
        vac = ferric.run_rhf(mol, bs, **kw)
        rec.update(vac_converged=bool(vac.converged), dE_int=(fld.energy - vac.energy) * HARTREE_TO_KCAL)
    rec["cpu_s"] = time.process_time() - t0
    q.put(rec)


def _net_charge(smiles: str) -> int:
    from rdkit import Chem

    return sum(a.GetFormalCharge() for a in Chem.MolFromSmiles(smiles).GetAtoms())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    case = next(c for c in json.loads((a.inputs / "cases.json").read_text()) if c["name"] == CASE)
    charges = [tuple(c) for c in json.loads((a.inputs / "field.json").read_text())["charges"]]
    procs = {}
    for name, kw in PROBES.items():
        q = mp.Queue()
        p = mp.Process(target=_probe, args=(q, case["smiles"], case["xyz"], charges, kw))
        p.start()
        procs[name] = (p, q, time.time())
    results = {}
    for name, (p, q, t0) in procs.items():
        p.join()
        try:
            rec = q.get(timeout=2)
        except Exception:
            rec = {"killed_at_cpu_limit": True, "exitcode": p.exitcode, "cpu_s": float(CPU_LIMIT_S)}
        rec.update(probe=PROBES[name], wall_s=time.time() - t0)
        results[name] = rec
        print(name, rec, flush=True)
    a.out.write_text(json.dumps({"case": CASE, "cpu_limit_s": CPU_LIMIT_S, "results": results}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
