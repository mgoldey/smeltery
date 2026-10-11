"""Ligand-side partial charges (Lowdin, Hirshfeld, Gasteiger) on the PLB tyk2 poses (issue #29, ligand side).

    OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=1 nice -n 10 python scripts/plb_ligand_charges.py run \\
        --plb-dir DIR/tyk2 --out OUT --poses 8 --workers 4 --max-cpu-hours 5
    python scripts/plb_ligand_charges.py assemble --plb-dir DIR/tyk2 --pqr tyk2_amber14.pqr --out OUT \\
        --stored benchmarks/plb/results/tyk2_sto3g.json --result benchmarks/plb/results/tyk2_sto3g_ligand_charges.json
    python scripts/plb_ligand_charges.py analyse --result ... --stored ... --analysis ...

Design and verdict rules: docs/benchmarks/plb_ligand_charges_plan.md (committed before any charge was computed).
One task = ONE vacuum RHF/STO-3G SCF of a (ligand, pose) plus its Lowdin and Hirshfeld (ferric, proatom "scf") charges.
The poses are the stored tyk2 run's, regenerated with the same seeded transform and checked by sha256. Gasteiger is
RDKit's and needs no SCF. The log prints timings only, never charges.
"""

from __future__ import annotations

import os

os.environ.setdefault("RAYON_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import multiprocessing  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_plb_ddE as rp  # noqa: E402

from smeltery import plb_ligand_charges as lc  # noqa: E402
from smeltery.pocket import load_pocket  # noqa: E402
from smeltery.record import ferric_identity  # noqa: E402

SCHEMA = "smeltery-plb-ligand-charges/1"
MODELS = ("lowdin", "hirshfeld", "gasteiger")


def run_task(spec: dict) -> dict:
    import ferric

    t0, c0 = time.perf_counter(), time.process_time()
    out = {k: spec[k] for k in ("ligand", "pose")}
    try:
        mol = ferric.Molecule.from_xyz_string(
            rp.xyz_text(spec["symbols"], spec["coords"], f"{spec['ligand']} pose {spec['pose']}"),
            charge=spec["net_charge"],
        )
        bs = ferric.BasisSet.bundled("sto-3g")
        r = ferric.run_rhf(mol, bs, energy_conv=1e-10, density_conv=1e-8)
        out["converged"] = bool(r.converged)
        out["scf_cpu_s"] = time.process_time() - c0
        if not out["converged"]:
            out["error"] = "SCF did not converge"
        else:
            out["lowdin"] = [float(v) for v in ferric.lowdin_charges(mol, bs, r)]
            out["hirshfeld"] = [float(v) for v in ferric.hirshfeld_charges(mol, bs, r)]  # proatom="scf", the default
            out["error"] = None
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["wall_s"], out["cpu_s"] = time.perf_counter() - t0, time.process_time() - c0
    return out


def build(plb_dir: Path, target: str, n_poses: int, seed: int, jdeg: float, jang: float):
    _, tgt, ligs = rp.load_series(plb_dir, target)
    ref = tgt.reference
    pivot = ligs[ref]["coords"].mean(axis=0)
    poses = rp.build_poses({n: d["coords"] for n, d in ligs.items()}, pivot, n_poses, seed, jdeg, jang)
    return tgt, ligs, poses, ref


def cmd_run(a) -> int:
    tgt, ligs, poses, ref = build(a.plb_dir, "tyk2", a.poses, a.seed, a.jitter_deg, a.jitter_ang)
    names = sorted(ligs, key=lambda n: (n != ref, n))
    a.out.mkdir(parents=True, exist_ok=True)
    path = a.out / "tasks.jsonl"
    prior = rp.read_tasks(path)
    done = {(r["ligand"], r["pose"]) for r in prior if r.get("error") is None}
    cpu_s = sum(r["cpu_s"] for r in prior)
    only = a.only_ligands
    todo = [
        (n, k)
        for k in range(a.poses)
        for n in names
        if (n, k) not in done and (only is None or n in only) and (a.only_poses is None or k in a.only_poses)
    ]
    print(f"{len(done)} done ({cpu_s / 3600:.2f} CPU-h), {len(todo)} to run, cap {a.max_cpu_hours} CPU-h", flush=True)
    cap = a.max_cpu_hours * 3600.0
    ctx = multiprocessing.get_context("spawn")
    stopped = False
    with ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx) as ex:
        pending, queue = {}, iter(todo)

        def more():
            nonlocal stopped
            while len(pending) < a.workers and not stopped:
                if cpu_s >= cap:
                    stopped = True
                    print("CPU CAP REACHED: no new SCFs", flush=True)
                    return
                key = next(queue, None)
                if key is None:
                    return
                n, k = key
                spec = {
                    "ligand": n,
                    "pose": k,
                    "symbols": ligs[n]["symbols"],
                    "coords": poses[n][k],
                    "net_charge": ligs[n]["net_charge"],
                }
                pending[ex.submit(run_task, spec)] = key

        more()
        while pending:
            fin, _ = wait(pending, return_when=FIRST_COMPLETED)
            for f in fin:
                pending.pop(f)
                r = f.result()
                cpu_s += r["cpu_s"]
                with path.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(
                    f"{r['ligand']} pose {r['pose']}: {r['error'] or 'ok'} scf {r.get('scf_cpu_s', 0):.0f}s "
                    f"total {r['cpu_s']:.0f}s (sum {cpu_s / 3600:.2f} CPU-h)",
                    flush=True,
                )
            more()
    return 0


def gasteiger(plb_dir: Path) -> dict[str, list[float]]:
    from rdkit import Chem
    from rdkit.Chem import rdPartialCharges

    out = {}
    for m in Chem.SDMolSupplier(str(plb_dir / "ligands.sdf"), removeHs=False):
        rdPartialCharges.ComputeGasteigerCharges(m)
        q = [float(a.GetDoubleProp("_GasteigerCharge")) for a in m.GetAtoms()]
        if not all(np.isfinite(q)):
            raise SystemExit(f"{m.GetProp('_Name')}: Gasteiger returned a non-finite charge")
        out[m.GetProp("_Name")] = q
    return out


def carbonyls(plb_dir: Path) -> dict[str, list[list[int]]]:
    from rdkit import Chem

    patt = Chem.MolFromSmarts("[CX3]=[OX1]")
    return {
        m.GetProp("_Name"): [list(t) for t in m.GetSubstructMatches(patt)]
        for m in Chem.SDMolSupplier(str(plb_dir / "ligands.sdf"), removeHs=False)
    }


def cmd_assemble(a) -> int:
    stored = json.loads(a.stored.read_text())
    si = stored["inputs"]
    npose = a.poses
    tgt, ligs, poses, ref = build(a.plb_dir, "tyk2", si["n_poses"], si["seed"], si["jitter_deg"], si["jitter_ang"])
    for n in ligs:  # the same geometries as the stored run, bit for bit
        sha = hashlib.sha256(b"".join(np.asarray(x).tobytes() for x in poses[n])).hexdigest()
        if sha != si["pose_geometry_sha256"][n]:
            raise SystemExit(f"{n}: pose geometry differs from the stored run's")
    pqr = Path(a.pqr)
    pqr_sha = hashlib.sha256(pqr.read_bytes()).hexdigest()
    if pqr_sha != si["prep_provenance"]["amber14"]["output_sha256"]:
        raise SystemExit("PQR is not the stored run's AMBER PQR")
    center = tuple(si["field_provenance"]["amber14"]["center_ang"])
    field = load_pocket(pqr, cutoff_ang=si["cutoff_ang"], center_ang=center)
    fq = np.array([c.charge for c in field]) if hasattr(field[0], "charge") else None
    fxyz = np.array([c.xyz_ang for c in field])
    if fq is None:
        fq = np.array([c.q for c in field])
    tasks = {(r["ligand"], r["pose"]): r for r in rp.read_tasks(a.out / "tasks.jsonl") if r.get("error") is None}
    names = [ref, *sorted(n for n in ligs if n != ref)]
    missing = [(n, k) for n in names for k in range(npose) if (n, k) not in tasks]
    if missing:
        raise SystemExit(f"{len(missing)} (ligand, pose) tasks missing, e.g. {missing[:3]}")
    gas = gasteiger(a.plb_dir)
    rec = {
        "schema": SCHEMA,
        "target": "tyk2",
        "reference": ref,
        "ligands": names,
        "n_poses": npose,
        "models": list(MODELS),
        "inputs": {
            "basis": "sto-3g",
            "method": "RHF",
            "hirshfeld_proatom": "scf",
            "ferric": ferric_identity(),
            "stored_run": str(a.stored.name),
            "stored_run_sha256": hashlib.sha256(a.stored.read_bytes()).hexdigest(),
            "pqr_sha256": pqr_sha,
            "n_field_charges": len(fq),
            "field_net_charge_e": float(fq.sum()),
            "seed": si["seed"],
            "pose_geometry_sha256": {n: si["pose_geometry_sha256"][n] for n in names},
            "net_charge": {n: ligs[n]["net_charge"] for n in names},
            "coulomb_kcal_ang_per_e2": lc.COULOMB_KCAL_ANG,
            "cpu_s_total": float(sum(r["cpu_s"] for r in tasks.values())),
        },
        "carbonyl_atom_pairs": carbonyls(a.plb_dir),
        "charges": {m: {} for m in MODELS},
        "phi_kcal_mol_per_e": {},
    }
    for n in names:
        rec["phi_kcal_mol_per_e"][n] = [
            [float(v) for v in lc.field_potential(poses[n][k], fq, fxyz)] for k in range(npose)
        ]
        rec["charges"]["lowdin"][n] = [tasks[(n, k)]["lowdin"] for k in range(npose)]
        rec["charges"]["hirshfeld"][n] = [tasks[(n, k)]["hirshfeld"] for k in range(npose)]
        rec["charges"]["gasteiger"][n] = [gas[n] for _ in range(npose)]  # geometry-independent
        for m in MODELS:
            if any(len(c) != len(ligs[n]["symbols"]) for c in rec["charges"][m][n]):
                raise SystemExit(f"{n}/{m}: charge count differs from the atom count")
    a.result.write_text(json.dumps(rec, indent=1, sort_keys=True))
    print(f"wrote {a.result}")
    return 0


def cmd_analyse(a) -> int:
    rec = json.loads(a.result.read_text())
    an = lc.analyse_record(rec, json.loads(a.stored.read_text()))
    a.analysis.write_text(json.dumps(an, indent=1, sort_keys=True))
    print(f"wrote {a.analysis}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plb-dir", type=Path, required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--poses", type=int, default=8)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--jitter-deg", type=float, default=15.0)
    r.add_argument("--jitter-ang", type=float, default=0.5)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--max-cpu-hours", type=float, required=True)
    r.add_argument("--only-ligands", nargs="+", default=None)
    r.add_argument("--only-poses", type=int, nargs="+", default=None)
    s = sub.add_parser("assemble")
    s.add_argument("--plb-dir", type=Path, required=True)
    s.add_argument("--pqr", type=Path, required=True)
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--stored", type=Path, required=True)
    s.add_argument("--poses", type=int, required=True)
    s.add_argument("--result", type=Path, required=True)
    n = sub.add_parser("analyse")
    n.add_argument("--result", type=Path, required=True)
    n.add_argument("--stored", type=Path, required=True)
    n.add_argument("--analysis", type=Path, required=True)
    a = ap.parse_args(argv)
    return {"run": cmd_run, "assemble": cmd_assemble, "analyse": cmd_analyse}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())
