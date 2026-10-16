"""Diagnose the two rebuilt-pose CH3 SCF failures of the halogen scan (plan:
docs/benchmarks/danuglipron_halogen_ch3_diag_plan.md).

Stages (run in order):
  poses  rebuild pose 0 of parent, pyr-C28-CH3, bzim-C9-CH3, bnz-C34-CH3 with the run-4 settings, check they are the
         poses of run 4's cache, write xyz files and geometry statistics. No SCF.
  scf    whole-molecule SCFs: vacuum and nearest-N-charge fields, each in its own process with a CPU limit.

Run (project .venv):  python examples/danuglipron_halogen_ch3_diag.py poses --out DIR
                      python examples/danuglipron_halogen_ch3_diag.py scf --out DIR
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import resource
import sys
import time
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
from rdkit import Chem

sys.path.insert(0, str(Path(__file__).parent))
import danuglipron_halogen as dh  # noqa: E402

from smeltery import Candidate, load_pocket  # noqa: E402

NAMES = ["parent", "pyr-C28-CH3", "bzim-C9-CH3", "bnz-C34-CH3"]
FAILED = ["pyr-C28-CH3", "bzim-C9-CH3"]
CONTROL = "bnz-C34-CH3"
CACHE = Path("benchmarks/danuglipron_halogen/results/halogen-scf-cache.json")
CPU_LIMIT_S = 1500
HARTREE_TO_KCAL = 627.5094740631


def build(out: Path):
    data = dh.find_data_dir()
    field = load_pocket(data / "7LCJ_pocket.pdb", ff="AMBER")
    smi = dh.analogue_smiles()
    cands = [Candidate("parent", dh.PARENT_SMILES)] + [Candidate(n, smi[n]) for n in NAMES[1:]]
    pairing = dh.make_pairing(dh.bound_pose_in_smiles_order(data / "conf_00_cryo_em.xyz"), 3, relax_unmapped=True)
    pairing.run(cands, {"parent": cands[0]})
    for c in cands:
        c.poses = c.poses[1:]  # plan A1: pose 0 of the scan is index 1 of the build
    return field, cands


def cache_key(field, name, pose, tag="AMBER"):
    prov = field.provenance
    fkey = json.dumps({k: prov.get(k) for k in ("input_sha256", "pdb2pqr_ff", "cutoff_ang")}, sort_keys=True)
    return hashlib.sha256((fkey + tag + name + pose.to_xyz()).encode()).hexdigest()


def geometry_stats(smiles, pose, charges_xyz_ang):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    xyz = np.asarray(pose.coords_ang)
    syms = list(pose.symbols)
    heavy = [i for i, s in enumerate(syms) if s != "H"]
    pt = Chem.GetPeriodicTable()
    bonded = {
        (min(b.GetBeginAtomIdx(), b.GetEndAtomIdx()), max(b.GetBeginAtomIdx(), b.GetEndAtomIdx()))
        for b in mol.GetBonds()
    }
    worst, worst_ratio = None, 0.0
    if mol.GetNumAtoms() == len(syms):  # atom order is AddHs order when the counts agree (checked by the gate)
        for i, j in bonded:
            d = float(np.linalg.norm(xyz[i] - xyz[j]))
            r = d / (pt.GetRcovalent(syms[i]) + pt.GetRcovalent(syms[j]))
            if r > worst_ratio:
                worst, worst_ratio = (i, j, round(d, 3)), r
    nb = []
    for a in heavy:
        for b in heavy:
            if a < b and (a, b) not in bonded:
                nb.append((float(np.linalg.norm(xyz[a] - xyz[b])), a, b))
    nb.sort()
    # keep only pairs that are not 1-3 neighbours
    adj = {i: set() for i in range(len(syms))}
    for i, j in bonded:
        adj[i].add(j)
        adj[j].add(i)
    far = [t for t in nb if t[2] not in {k for n in adj[t[1]] for k in adj[n]}]
    d_ch = np.linalg.norm(xyz[:, None, :] - charges_xyz_ang[None, :, :], axis=2)
    return {
        "longest_bond_over_covalent_sum": {"atoms": worst, "ratio": round(worst_ratio, 3)},
        "shortest_nonbonded_heavy_ang": [round(far[0][0], 3), far[0][1], far[0][2]] if far else None,
        "shortest_ligand_charge_ang": round(float(d_ch.min()), 3),
        "ligand_atoms_within_2A_of_a_charge": int((d_ch.min(axis=1) < 2.0).sum()),
        "ligand_atoms_within_2p5A_of_a_charge": int((d_ch.min(axis=1) < 2.5).sum()),
    }


def stage_poses(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    field, cands = build(out)
    ch = np.array([c.xyz_ang for c in field])  # Angstrom
    cache = json.loads(CACHE.read_text())
    rec = {}
    for c in cands:
        p = c.poses[0]
        key = cache_key(field, c.name, p)
        hit = cache.get(key)
        (out / f"{c.name}_pose0.xyz").write_text(p.to_xyz())
        rec[c.name] = {
            "in_run4_cache": hit is not None,
            "run4_status": None if hit is None else ("failed" if hit.get("error") else "converged"),
            "run4_cpu_s": None if hit is None else hit.get("cpu_s"),
            **geometry_stats(c.smiles, p, ch),
        }
        print(c.name, json.dumps(rec[c.name]), flush=True)
    (out / "geometry_detail.json").write_text(json.dumps(rec, indent=1))


def _scf(q, name, xyz_path, charge, n_charges, charges_path):
    resource.setrlimit(resource.RLIMIT_CPU, (CPU_LIMIT_S, CPU_LIMIT_S + 60))
    import ferric

    t0 = time.process_time()
    mol = ferric.Molecule.from_xyz_string(Path(xyz_path).read_text(), charge=charge)
    bs = ferric.BasisSet.bundled("sto-3g")
    pcs = None
    if n_charges:
        ch = np.array(json.loads(Path(charges_path).read_text()))  # q, x, y, z(bohr) rows, nearest-first
        pcs = [tuple(r) for r in ch[:n_charges]]
    r = ferric.run_rhf(mol, bs, energy_conv=1e-10, density_conv=1e-8, max_iter=200, point_charges=pcs)
    q.put(
        {
            "name": name,
            "n_charges": n_charges,
            "converged": bool(r.converged),
            "iterations": int(r.iterations),
            "exit": str(getattr(r, "exit_reason", None)),  # rc7 has no exit_reason
            "energy": r.energy,
            "cpu_s": time.process_time() - t0,
        }
    )


def _run(args):
    q = mp.Queue()
    p = mp.Process(target=_scf, args=(q, *args))
    p.start()
    p.join()
    try:
        return q.get(timeout=1)
    except Exception:
        return {"name": args[0], "n_charges": args[3], "converged": False, "exit": f"killed (exit code {p.exitcode})"}


def stage_scf(out: Path, workers: int, names=None, ns=None, tag="scf_diag") -> None:
    data = dh.find_data_dir()
    field = load_pocket(data / "7LCJ_pocket.pdb", ff="AMBER")
    jobs = []
    for name in names or [*FAILED, CONTROL]:
        xyz = out / f"{name}_pose0.xyz"
        xs = xyz.read_text().split("\n")[2:]
        coords = np.array([[float(v) for v in ln.split()[1:4]] for ln in xs if ln.strip()])
        d = np.linalg.norm(np.array([c.xyz_ang for c in field])[:, None, :] - coords[None], axis=2).min(axis=1)
        order = np.argsort(d)
        rows = np.array([c.as_ferric_bohr() for c in field])[order]
        cpath = out / f"{name}_charges_nearest_first.json"
        cpath.write_text(json.dumps(rows.tolist()))
        smi = dh.analogue_smiles()[name]
        z = sum(a.GetFormalCharge() for a in Chem.MolFromSmiles(smi).GetAtoms())
        for n in ns or ((0, 6458) if name == CONTROL else (0, 50, 1000)):  # control: vacuum + run 4's full field
            jobs.append((name, str(xyz), z, n, str(cpath)))
    results = []
    from multiprocessing.pool import ThreadPool  # threads only wait; each SCF is its own process with a CPU limit

    with ThreadPool(workers) as pool:
        for r in pool.imap_unordered(_run, jobs):
            results.append(r)
            print(json.dumps(r), flush=True)
            (out / f"{tag}.json").write_text(json.dumps(results, indent=1))


def _order_job(q, label, max_iter, order, xyz_path, charges_pqr, charges_nearest):
    resource.setrlimit(resource.RLIMIT_CPU, (2500, 2560))
    import ferric

    t0 = time.process_time()
    mol = ferric.Molecule.from_xyz_string(Path(xyz_path).read_text(), charge=0)
    bs = ferric.BasisSet.bundled("sto-3g")
    rows = json.loads(Path(charges_pqr if order == "pqr" else charges_nearest).read_text())
    kw = {} if max_iter is None else {"max_iter": max_iter}
    r = ferric.run_rhf(mol, bs, energy_conv=1e-10, density_conv=1e-8, point_charges=[tuple(x) for x in rows], **kw)
    q.put(
        {
            "run": label,
            "max_iter": max_iter,
            "order": order,
            "converged": bool(r.converged),
            "iterations": int(r.iterations),
            "energy": r.energy,
            "cpu_s": time.process_time() - t0,
        }
    )


def _order_run(args):
    q = mp.Queue()
    p = mp.Process(target=_order_job, args=(q, *args))
    p.start()
    p.join()
    try:
        return q.get(timeout=1)
    except Exception:
        return {"run": args[0], "converged": False, "exit": f"killed at the CPU limit (exit code {p.exitcode})"}


def stage_order(out: Path) -> None:
    """Addendum B: the same field SCF with the tier's call (R1), max_iter 200 (R2), and nearest-first order (R3)."""
    from multiprocessing.pool import ThreadPool

    data = dh.find_data_dir()
    field = load_pocket(data / "7LCJ_pocket.pdb", ff="AMBER")
    pqr = out / "all_charges_pqr_order.json"
    pqr.write_text(json.dumps([list(c.as_ferric_bohr()) for c in field]))
    xyz = str(out / "pyr-C28-CH3_pose0.xyz")
    near = str(out / "pyr-C28-CH3_charges_nearest_first.json")
    jobs = [
        ("R1", None, "pqr", xyz, str(pqr), near),
        ("R2", 200, "pqr", xyz, str(pqr), near),
        ("R3", None, "nearest", xyz, str(pqr), near),
    ]
    results = []
    with ThreadPool(3) as pool:
        for r in pool.imap_unordered(_order_run, jobs):
            results.append(r)
            print(json.dumps(r), flush=True)
            (out / "order_diag.json").write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("stage", choices=["poses", "scf", "order"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--names", default=None, help="comma-separated subset of the stage-2 molecules (addendum A)")
    ap.add_argument("--ns", default=None, help="comma-separated charge counts")
    ap.add_argument("--tag", default="scf_diag")
    a = ap.parse_args()
    if a.stage == "poses":
        stage_poses(a.out)
    elif a.stage == "order":
        stage_order(a.out)
    else:
        stage_scf(
            a.out,
            a.workers,
            a.names.split(",") if a.names else None,
            [int(v) for v in a.ns.split(",")] if a.ns else None,
            a.tag,
        )
