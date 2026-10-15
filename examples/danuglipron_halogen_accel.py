"""AURORA and TRAH on the halogen-scan SCFs that DIIS did not converge (issue #6, ferric#418).

Plan, written before any run: docs/benchmarks/danuglipron_halogen_accel_plan.md. Needs a ferric build with
`run_rhf(aurora=, trah_trigger=)`, which the released rc7 lacks (see the plan for the build used).

Two steps, because the poses must be rebuilt with the RDKit the cached DIIS runs used (the project environment) while
the accelerators need the unreleased ferric build:
  1. project env:  python examples/danuglipron_halogen_accel.py prepare --inputs DIR
       rebuilds the 6 poses and the field, checks each has a cached DIIS failure, writes DIR/cases.json, field.json
  2. dev-wheel env: python examples/danuglipron_halogen_accel.py run --inputs DIR [--out FILE]
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import multiprocessing as mp
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("danuglipron_halogen", HERE / "danuglipron_halogen.py")
hal = importlib.util.module_from_spec(_spec)
sys.modules["danuglipron_halogen"] = hal
_spec.loader.exec_module(hal)

CACHE = HERE.parent / "benchmarks" / "danuglipron_halogen" / "results" / "halogen-scf-cache.json"
OUT = HERE.parent / "benchmarks" / "danuglipron_halogen" / "results" / "halogen_accel_results.json"
# Fixed in the plan, in this order (ascending DIIS CPU of the recorded failure). Post-skip pose index 0.
CASES = ["pyr-C28-F", "pyr-C28-Cl", "bzim-C9-F", "bnz-C34-Cl", "bnz-C36-F", "bnz-C34-F"]
POSE = 0
ENERGY_CONV = 1e-6
TRAH_TRIGGER = 1e-2
JOB_CPU_LIMIT_S = 5000
STUDY_CAP_CPU_S = 12 * 3600
WORKERS = 6
AGREEMENT_KCAL = 2e-3
HARTREE_TO_KCAL = 627.5094740631


def cache_keys(field, name: str, xyz: str) -> tuple[str, str]:
    """(1e-10 key, 1e-6 key) of one pose, built exactly as `danuglipron_halogen.run_field_tier` builds them."""
    prov = field.provenance
    fkey = json.dumps({k: prov.get(k) for k in ("input_sha256", "pdb2pqr_ff", "cutoff_ang")}, sort_keys=True)
    legacy = hashlib.sha256((fkey + "AMBER" + name + xyz).encode()).hexdigest()
    new = hashlib.sha256((fkey + "AMBER" + f"|energy_conv={ENERGY_CONV:g}" + name + xyz).encode()).hexdigest()
    return legacy, new


def _job(q, name, smiles, pose, field, variant):
    """Child: vacuum SCF (DIIS) then the field SCF with the accelerator. Killed by SIGXCPU at the CPU limit."""
    resource.setrlimit(resource.RLIMIT_CPU, (JOB_CPU_LIMIT_S, JOB_CPU_LIMIT_S + 60))
    import ferric

    from smeltery.tiers import _net_charge

    t0 = time.process_time()
    mol = ferric.Molecule.from_xyz_string(pose, charge=_net_charge(smiles))
    bs = ferric.BasisSet.bundled("sto-3g")
    kw = {"energy_conv": ENERGY_CONV, "density_conv": 1e-8}
    vac = ferric.run_rhf(mol, bs, **kw)
    acc = {"aurora": True} if variant == "aurora" else {"trah_trigger": TRAH_TRIGGER}
    charges = field
    try:
        fld = ferric.run_rhf(mol, bs, point_charges=charges, **kw, **acc)
    except Exception as e:  # reported, not hidden
        q.put({"error": f"{type(e).__name__}: {e}", "cpu_s": time.process_time() - t0})
        return
    q.put(
        {
            "converged": bool(vac.converged and fld.converged),
            "vac_converged": bool(vac.converged),
            "field_converged": bool(fld.converged),
            "field_iterations": int(fld.iterations),
            "vac_iterations": int(vac.iterations),
            "dE_int": (fld.energy - vac.energy) * HARTREE_TO_KCAL,
            "cpu_s": time.process_time() - t0,
        }
    )


def run_stage(jobs, field, spent_s, label):
    """Run (variant, case) jobs, WORKERS at a time, under the study cap. Returns (results, spent_s)."""
    results, live, pending = [], {}, list(reversed(jobs))
    while pending or live:
        while pending and len(live) < WORKERS and spent_s + JOB_CPU_LIMIT_S * (len(live) + 1) <= STUDY_CAP_CPU_S:
            variant, name, smiles, pose = pending.pop()
            q = mp.Queue()
            p = mp.Process(target=_job, args=(q, name, smiles, pose, field, variant))
            p.start()
            live[p] = (variant, name, q, time.time())
            print(f"  {label}: started {variant} {name}", flush=True)
        if not live:
            break
        time.sleep(5)
        for p in [p for p in live if not p.is_alive()]:
            variant, name, q, t_start = live.pop(p)
            p.join()
            try:
                rec = q.get(timeout=2)
            except Exception:
                rec = {"killed_at_cpu_limit": True, "exitcode": p.exitcode, "cpu_s": float(JOB_CPU_LIMIT_S)}
            rec.update(variant=variant, case=name, wall_s=time.time() - t_start)
            spent_s += rec["cpu_s"]
            results.append(rec)
            print(f"  {label}: {variant} {name}: {rec} (spent {spent_s / 3600:.2f} CPU-h)", flush=True)
    not_run = [(v, n) for v, n, *_ in reversed(pending)]
    return results, spent_s, not_run


def prepare(inputs: Path) -> None:
    data = hal.find_data_dir()
    field = hal.load_pocket(data / "7LCJ_pocket.pdb", ff="AMBER")
    parent = hal.Candidate("parent", hal.PARENT_SMILES)
    analogues = [hal.Candidate(n, s) for n, s in hal.analogue_smiles(hal.SITES).items()]
    cands = [parent, *analogues, hal.Candidate("parent-self", hal.PARENT_SMILES)]
    hal.make_pairing(hal.bound_pose_in_smiles_order(data / "conf_00_cryo_em.xyz"), 2 + 1).run(cands, {"parent": parent})
    for c in cands:
        c.poses = c.poses[1:]  # amendment A1
    by_name = {c.name: c for c in cands}
    cache = json.loads(CACHE.read_text())
    cases = []
    for name in CASES:
        c = by_name[name]
        xyz = c.poses[POSE].to_xyz(f"{name} pose {POSE}")
        legacy, new = cache_keys(field, name, c.poses[POSE].to_xyz())
        ent_old, ent_new = cache.get(legacy), cache.get(new)
        assert ent_old is not None or ent_new is not None, f"{name}: no cached DIIS record (wrong RDKit/field?)"
        rec = {
            "failed_at_1e-10": bool(ent_old and ent_old.get("error")),
            "diis_at_1e-6": None if ent_new is None else {"error": ent_new.get("error"), "dE": ent_new.get("dE")},
        }
        assert rec["failed_at_1e-10"] or (ent_new and ent_new.get("error")), f"{name}: DIIS did not fail"
        cases.append({"name": name, "smiles": c.smiles, "xyz": xyz, "diis": rec})
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "cases.json").write_text(json.dumps(cases, indent=1))
    (inputs / "field.json").write_text(
        json.dumps({"charges": [list(ch.as_ferric_bohr()) for ch in field], "provenance": field.provenance})
    )
    print(json.dumps({c["name"]: c["diis"] for c in cases}, indent=1))


def run(inputs: Path, out: Path) -> None:
    import ferric

    cases = {c["name"]: c for c in json.loads((inputs / "cases.json").read_text())}
    field_raw = (inputs / "field.json").read_bytes()
    field = [tuple(c) for c in json.loads(field_raw)["charges"]]
    jobs = lambda variant, names: [(variant, n, cases[n]["smiles"], cases[n]["xyz"]) for n in names]  # noqa: E731
    s1, spent, nr1 = run_stage(jobs("aurora", CASES), field, 0.0, "stage1")
    aurora_failed = [r["case"] for r in s1 if not r.get("converged")]
    s2, spent, nr2 = (
        run_stage(jobs("trah", [n for n in CASES if n in aurora_failed]), field, spent, "stage2")
        if aurora_failed
        else ([], spent, [])
    )
    commit = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=HERE).stdout.strip()
    record = {
        "plan": "docs/benchmarks/danuglipron_halogen_accel_plan.md",
        "smeltery_commit": commit,
        "ferric_version": getattr(ferric, "__version__", None),
        "ferric_build": "Wheels run 38630999922, ferric 773081a24984d5ca43153311dc4ccfcb369b095c, cp312 wheel sha256 "
        "9fd4605ead1a69e0d8b4778802d02a016610acfeeb25a1e4f5f68f58648ad064",
        "field_json_sha256": hashlib.sha256(field_raw).hexdigest(),
        "settings": {
            "energy_conv": ENERGY_CONV,
            "density_conv": 1e-8,
            "trah_trigger": TRAH_TRIGGER,
            "job_cpu_limit_s": JOB_CPU_LIMIT_S,
            "study_cap_cpu_s": STUDY_CAP_CPU_S,
            "agreement_kcal_mol": AGREEMENT_KCAL,
            "pose_index": POSE,
            "cases": CASES,
        },
        "diis_records": {n: c["diis"] for n, c in cases.items()},
        "results": s1 + s2,
        "not_run": nr1 + nr2,
        "total_cpu_s": spent,
    }
    out.write_text(json.dumps(record, indent=1))
    print(f"wrote {out}; spent {spent / 3600:.2f} CPU-h; not run {record['not_run']}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("step", choices=["prepare", "run"])
    ap.add_argument("--inputs", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args(argv)
    prepare(args.inputs) if args.step == "prepare" else run(args.inputs, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
