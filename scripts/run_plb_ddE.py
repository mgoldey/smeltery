"""Resumable, parallel production run: smeltery's paired field-interaction ddE on a PLB target's ligand series.

    OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=1 nice -n 10 python scripts/run_plb_ddE.py run \\
        --plb-dir DIR/cdk2 --target cdk2 --field amber14=A.pqr --field charmm36=B.pqr \\
        --out OUT --poses 6 --seed 1 --workers 4 --run vac amber14 --max-cpu-hours 9.5
    ... run --run charmm36 --max-cpu-hours 12.6        (a second invocation, same --out: model B's field SCFs)
    python scripts/run_plb_ddE.py assemble --out OUT --result benchmarks/plb/results/cdk2_sto3g.json

The design, the statistics and the success criteria are fixed in docs/benchmarks/plb_plan.md BEFORE this runs.

What one task is: ONE SCF of one (ligand, pose): the vacuum RHF (`vac`) or the RHF in one model's point-charge
field. Per model, dE_int = E_RHF(in field) - E_RHF(vacuum) is exactly what `smeltery.tiers.FieldInteraction`
computes (tests/test_plb_measure.py checks the two agree on a small molecule); the vacuum energy does not depend
on the charge model, so it is computed once and shared by both models.

Poses: pose 0 is the PLB pose as shipped. Pose k >= 1 applies the SAME seeded rigid transform (rotation of at most
`--jitter-deg` about a random axis, translation of at most `--jitter-ang` per axis) to EVERY ligand, about ONE
shared pivot (the reference ligand's centroid), the transform for pose k being drawn from
numpy.random.default_rng([seed, k]). "Paired" therefore means: pose k of every ligand is the PLB pose of that
ligand moved by the same rigid motion, so the paired difference q(ligand, k) - q(parent, k) cancels what the
ligands share. It is not docking and it relaxes nothing.

Tasks run pose-major (all ligands at pose 0, then pose 1, ...), so whenever the CPU cap stops the run every ligand has
the same poses; which poses finished is decided by the cap, never by a result. Results are appended to
OUT/tasks.jsonl as each SCF finishes (a kill loses only the SCFs in flight); a restart skips finished SCFs. An SCF
that does not converge is recorded as an error and never silently dropped. The log prints timings only, not
energies: the analysis is fixed and committed before anyone looks at the ddE values (docs/benchmarks/plb_plan.md).
"""

from __future__ import annotations

import os

os.environ.setdefault("RAYON_NUM_THREADS", "1")  # parallelism is across tasks (processes), not inside ferric
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import subprocess  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from smeltery.benchmark import heavy_canonical, load_benchmark  # noqa: E402
from smeltery.model import HARTREE_TO_KCAL  # noqa: E402
from smeltery.pocket import load_pocket  # noqa: E402
from smeltery.record import RunRecord, ferric_identity  # noqa: E402

SCHEMA = "smeltery-plb-run/1"


def shared_transform(seed: int, k: int, max_deg: float, max_shift: float) -> tuple[np.ndarray, np.ndarray]:
    """(R, t) of pose k >= 1: a rotation matrix and a translation, drawn from default_rng([seed, k])."""
    rng = np.random.default_rng([seed, k])
    axis = rng.normal(size=3)
    axis /= np.linalg.norm(axis)
    theta = np.deg2rad(rng.uniform(-max_deg, max_deg))
    kx = np.array([[0, -axis[2], axis[1]], [axis[2], 0, -axis[0]], [-axis[1], axis[0], 0]])
    rot = np.eye(3) + np.sin(theta) * kx + (1 - np.cos(theta)) * (kx @ kx)
    return rot, rng.uniform(-max_shift, max_shift, size=3)


def build_poses(
    coords_by_ligand: dict[str, np.ndarray], pivot_ang, n_poses: int, seed: int, max_deg: float, max_shift: float
) -> dict[str, list[np.ndarray]]:
    """Pose 0 = as given; pose k = the same rigid motion of every ligand about `pivot_ang`."""
    pivot = np.asarray(pivot_ang, dtype=float)
    out: dict[str, list[np.ndarray]] = {n: [np.array(x, dtype=float)] for n, x in coords_by_ligand.items()}
    for k in range(1, n_poses):
        rot, t = shared_transform(seed, k, max_deg, max_shift)
        for n, x in coords_by_ligand.items():
            out[n].append((np.asarray(x, dtype=float) - pivot) @ rot.T + pivot + t)
    return out


def xyz_text(symbols, coords, comment: str) -> str:
    lines = [str(len(symbols)), comment]
    lines += [f"{s} {x:.10f} {y:.10f} {z:.10f}" for s, (x, y, z) in zip(symbols, coords, strict=True)]
    return "\n".join(lines) + "\n"


def min_distance_to_charges(coords, charges_xyz) -> float:
    c = np.asarray(coords, dtype=float)[:, None, :]
    q = np.asarray(charges_xyz, dtype=float)[None, :, :]
    return float(np.sqrt(((c - q) ** 2).sum(-1)).min())


# ------------------------------------------------------------------------------------------------ worker

_FIELDS: dict[str, list] = {}  # model -> ferric point charges (q, x, y, z in Bohr); set once per process


def _init_worker(fields: dict[str, list]) -> None:
    _FIELDS.update(fields)


def run_task(spec: dict) -> dict:
    """One SCF: the vacuum RHF of a (ligand, pose) (`kind` "vac") or its RHF in one model's field (`kind` "field").

    dE_int is assembled later as (E_field - E_vac) * HARTREE_TO_KCAL, exactly `FieldInteraction`'s arithmetic. The
    two SCFs are separate tasks because the vacuum energy does not depend on the charge model (computed once, shared)
    and because a task of about 4 minutes keeps the resume granularity and the CPU cap tight.
    Never raises on an SCF failure: it is recorded.
    """
    import ferric

    t0, c0 = time.perf_counter(), time.process_time()
    out = {k: spec[k] for k in ("kind", "ligand", "pose", "basis", "net_charge")}
    if spec["kind"] == "field":
        out["model"] = spec["model"]
    try:
        mol = ferric.Molecule.from_xyz_string(
            xyz_text(spec["symbols"], spec["coords"], f"{spec['ligand']} pose {spec['pose']}"),
            charge=spec["net_charge"],
        )
        bs = ferric.BasisSet.bundled(spec["basis"])
        kw = {"energy_conv": spec["energy_conv"], "density_conv": spec["density_conv"]}
        if spec["kind"] == "vac":
            r = ferric.run_rhf(mol, bs, **kw)
        else:
            r = ferric.run_rhf(mol, bs, point_charges=_FIELDS[spec["model"]], **kw)
        out["energy_hartree"] = float(r.energy)
        out["converged"] = bool(r.converged)
        out["error"] = None if out["converged"] else "SCF did not converge"
    except Exception as exc:  # recorded, not raised: one bad pose must not kill a many-hour run
        out["error"] = f"{type(exc).__name__}: {exc}"
    out["wall_s"], out["cpu_s"] = time.perf_counter() - t0, time.process_time() - c0
    return out


def basis_gaps(symbols, basis: str) -> list[str]:
    """Elements of `symbols` for which ferric's bundled `basis` has no shells (checked with a tiny hydride of each)."""
    import ferric
    from rdkit import Chem

    bs = ferric.BasisSet.bundled(basis)
    pt = Chem.GetPeriodicTable()
    gaps = []
    for el in sorted(set(symbols) - {"H"}):
        n_h = 1 if pt.GetAtomicNumber(el) % 2 else 2  # an even electron count: ferric refuses an odd one
        xyz = f"{1 + n_h}\n\n{el} 0 0 0\n" + "".join(f"H {x} 0 {z}\n" for x, z in ((0.0, 1.3), (1.1, -0.7))[:n_h])
        try:
            ferric.run_rhf(ferric.Molecule.from_xyz_string(xyz, charge=0), bs, max_iter=1)
        except Exception as exc:  # only the basis-coverage failure counts as a gap
            if "basis" in str(exc).lower():
                gaps.append(el)
    return gaps


# ------------------------------------------------------------------------------------------------ driver


def sha256_file(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def read_sdf(path: Path) -> dict[str, dict]:
    from rdkit import Chem

    out = {}
    for m in Chem.SDMolSupplier(str(path), removeHs=False):
        name = m.GetProp("_Name")
        out[name] = {
            "symbols": tuple(a.GetSymbol() for a in m.GetAtoms()),
            "coords": np.array(m.GetConformer().GetPositions(), dtype=float),
            "heavy_smiles": Chem.MolToSmiles(Chem.RemoveHs(m)),
            "net_charge": int(Chem.GetFormalCharge(m)),
        }
    return out


def load_series(plb_dir: Path, target: str):
    """The target's ligands from the SDF, checked against the committed manifest (sha256, names, SMILES, charge)."""
    bench = load_benchmark()
    tgt = bench.targets[target]
    files = {f.kind: f for f in tgt.files}
    sdf = plb_dir / "ligands.sdf"
    if sha256_file(sdf) != files["ligands_sdf"].sha256:
        raise SystemExit(f"{sdf}: sha256 differs from the manifest's; refusing to use it")
    pdb = plb_dir / "protein.pdb"
    if sha256_file(pdb) != files["protein_pdb"].sha256:
        raise SystemExit(f"{pdb}: sha256 differs from the manifest's; refusing to use it")
    ligs = read_sdf(sdf)
    names = [lg.name for lg in tgt.ligands]
    if sorted(ligs) != sorted(names):
        raise SystemExit(f"SDF ligands {sorted(ligs)} differ from the manifest's {sorted(names)}")
    for lg in tgt.ligands:
        if heavy_canonical(ligs[lg.name]["heavy_smiles"]) != heavy_canonical(lg.smiles):
            raise SystemExit(f"{lg.name}: SDF structure differs from the manifest's SMILES (tautomer/protonation?)")
    return bench, tgt, ligs


def git_commit() -> str | None:
    try:
        r = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent), "rev-parse", "HEAD"], capture_output=True, text=True
        )
        return r.stdout.strip() or None
    except OSError:
        return None


def task_key(r: dict) -> tuple:
    return (r["kind"], r.get("model"), r["ligand"], r["pose"])


def read_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def cmd_run(a: argparse.Namespace) -> int:
    bench, tgt, ligs = load_series(a.plb_dir, a.target)
    ref = tgt.reference
    pivot = ligs[ref]["coords"].mean(axis=0)  # the reference's centroid, whatever is excluded
    excluded = {}
    for spec in a.exclude:
        name, reason = spec.split("=", 1)
        if name == ref or name not in ligs:
            raise SystemExit(f"--exclude {name!r}: the reference cannot be excluded, and the ligand must exist")
        excluded[name] = reason
        del ligs[name]
    for name, d in ligs.items():
        gaps = basis_gaps(d["symbols"], a.basis)
        if gaps:
            raise SystemExit(
                f"{name} has elements {gaps} with no {a.basis} shells in this ferric; refusing to drop it silently: "
                f"pass --exclude {name}=<reason> (and say so in the plan) or choose another basis"
            )
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    fields = {}
    for spec in a.field:
        model, path = spec.split("=", 1)
        fields[model] = load_pocket(
            Path(path), cutoff_ang=a.cutoff, center_ang=tuple(round(float(v), 4) for v in pivot)
        )
    unknown = [x for x in a.run if x != "vac" and x not in fields]
    if unknown:
        raise SystemExit(f"--run names {unknown}, which are neither 'vac' nor a --field model")
    ferric_fields = {m: [c.as_ferric_bohr() for c in f] for m, f in fields.items()}
    poses = build_poses({n: d["coords"] for n, d in ligs.items()}, pivot, a.poses, a.seed, a.jitter_deg, a.jitter_ang)
    names = sorted(ligs, key=lambda n: (n != ref, n))  # parent first
    tasks_path = out / "tasks.jsonl"
    prior = read_tasks(tasks_path)
    done = {task_key(r) for r in prior if r.get("error") is None}
    cpu_s = sum(r["cpu_s"] for r in prior)
    cap_s = a.max_cpu_hours * 3600.0
    todo = []
    for k in range(a.poses):  # pose-major: every ligand has the same number of poses whenever the run stops
        for n in names:
            for what in a.run:
                key = ("vac", None, n, k) if what == "vac" else ("field", what, n, k)
                if key not in done:
                    todo.append(key)
    print(
        f"{len(done)} SCFs done ({cpu_s / 3600:.2f} CPU-h), {len(todo)} to run on {a.workers} workers, "
        f"cap {a.max_cpu_hours} CPU-h; run {a.run}",
        flush=True,
    )

    def spec_of(key):
        kind, model, n, k = key
        s = {
            "kind": kind,
            "ligand": n,
            "pose": k,
            "symbols": ligs[n]["symbols"],
            "coords": poses[n][k],
            "net_charge": ligs[n]["net_charge"],
            "basis": a.basis,
            "energy_conv": 1e-10,
            "density_conv": 1e-8,
        }
        if kind == "field":
            s["model"] = model
        return s

    stopped = False
    with ProcessPoolExecutor(max_workers=a.workers, initializer=_init_worker, initargs=(ferric_fields,)) as ex:
        pending: dict = {}
        queue = iter(todo)

        def submit_more():
            nonlocal stopped
            while len(pending) < a.workers and not stopped:
                if cpu_s >= cap_s:
                    stopped = True
                    print(f"CPU CAP REACHED ({cpu_s / 3600:.2f} >= {a.max_cpu_hours} CPU-h): no new SCFs", flush=True)
                    return
                key = next(queue, None)
                if key is None:
                    return
                pending[ex.submit(run_task, spec_of(key))] = key

        submit_more()
        while pending:
            finished, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in finished:
                key = pending.pop(fut)
                r = fut.result()
                cpu_s += r["cpu_s"]
                if r["kind"] == "field":
                    r["min_dist_to_field_ang"] = min_distance_to_charges(
                        poses[r["ligand"]][r["pose"]], [c.xyz_ang for c in fields[r["model"]]]
                    )
                with tasks_path.open("a") as fh:
                    fh.write(json.dumps(r) + "\n")
                print(
                    f"{key[0]} {key[1] or ''} {r['ligand']} pose {r['pose']}: {r['error'] or 'ok'} "
                    f"wall {r['wall_s']:.0f}s cpu {r['cpu_s']:.0f}s (total {cpu_s / 3600:.2f} CPU-h)",
                    flush=True,
                )
            submit_more()
    meta = {
        "target": a.target,
        "reference": ref,
        "pivot_ang": [float(v) for v in pivot],
        "n_poses": a.poses,
        "seed": a.seed,
        "jitter_deg": a.jitter_deg,
        "jitter_ang": a.jitter_ang,
        "basis": a.basis,
        "cutoff_ang": a.cutoff,
        "models": list(fields),
        "field_provenance": {m: dict(f.provenance) for m, f in fields.items()},
        "prep_provenance": {
            m: json.loads(Path(str(p) + ".json").read_text()) for m, p in (s.split("=", 1) for s in a.field)
        },
        "smeltery_commit": git_commit(),
        "ligand_atom_counts": {n: len(ligs[n]["symbols"]) for n in names},
        "pose_geometry_sha256": {
            n: hashlib.sha256(b"".join(np.asarray(x).tobytes() for x in poses[n])).hexdigest() for n in names
        },
        "excluded_ligands": excluded,
        "max_cpu_hours_last_invocation": a.max_cpu_hours,
    }
    (out / "meta.json").write_text(json.dumps(meta, indent=2, sort_keys=True))
    with (out / "commands.log").open("a") as fh:
        fh.write(" ".join(sys.argv) + "\n")
    return 0


def _prefix(ok: set[int], n_max: int) -> int:
    """Length of the longest prefix 0..n-1 contained in `ok`."""
    n = 0
    while n < n_max and n in ok:
        n += 1
    return n


def cmd_assemble(a: argparse.Namespace) -> int:
    """Fold OUT/tasks.jsonl and OUT/meta.json into one committed RunRecord JSON (per-pose values, provenance).

    A model's poses are the longest prefix 0..n-1 for which EVERY ligand has both the vacuum and that model's field SCF
    converged, so every ligand always has the same poses. Which poses were completed is decided by the CPU cap, never by
    a result.
    """
    meta = json.loads((a.out / "meta.json").read_text())
    tasks = read_tasks(a.out / "tasks.jsonl")
    commands = (a.out / "commands.log").read_text().splitlines()
    by = {task_key(r): r for r in tasks if r.get("error") is None}  # a later successful re-run supersedes
    failures = [r for r in tasks if r.get("error") is not None and task_key(r) not in by]
    ligs = [meta["reference"], *sorted(n for n in meta["ligand_atom_counts"] if n != meta["reference"])]
    npose = meta["n_poses"]
    vac_ok = {k for k in range(npose) if all(("vac", None, n, k) in by for n in ligs)}
    n_vac = _prefix(vac_ok, npose)
    n_by_model, dE, e_fld, dist = {}, {}, {}, {}
    for m in meta["models"]:
        ok = {k for k in vac_ok if all(("field", m, n, k) in by for n in ligs)}
        n_m = _prefix(ok, npose)
        n_by_model[m] = n_m
        e_fld[m] = {n: [by[("field", m, n, k)]["energy_hartree"] for k in range(n_m)] for n in ligs}
        dE[m] = {
            n: [
                (by[("field", m, n, k)]["energy_hartree"] - by[("vac", None, n, k)]["energy_hartree"]) * HARTREE_TO_KCAL
                for k in range(n_m)
            ]
            for n in ligs
        }
        dist[m] = {n: [by[("field", m, n, k)]["min_dist_to_field_ang"] for k in range(n_m)] for n in ligs}
    results = {
        "schema": SCHEMA,
        "dE_int_kcal_mol": dE,
        "E_vac_hartree": {n: [by[("vac", None, n, k)]["energy_hartree"] for k in range(n_vac)] for n in ligs},
        "E_field_hartree": e_fld,
        "min_dist_to_field_ang": dist,
        "task_cpu_s": {
            f"{r['kind']}:{r.get('model') or ''}:{r['ligand']}:{r['pose']}": r["cpu_s"] for r in by.values()
        },
        "failures": failures,
        "total_cpu_s_used_in_results": sum(r["cpu_s"] for r in by.values()),
        "total_cpu_s_all_tasks_incl_failures_and_reruns": sum(r["cpu_s"] for r in tasks),
    }
    inputs = dict(meta)
    inputs["n_poses_by_model"] = n_by_model
    inputs["commands"] = commands
    rec = RunRecord(
        campaign=f"plb-{meta['target']}-paired-ddE",
        inputs=inputs,
        tiers=[
            {
                "name": "field_interaction",
                "method": "RHF",
                "basis": meta["basis"],
                "energy_conv": 1e-10,
                "density_conv": 1e-8,
                "engine": "ferric",
                "field": meta["field_provenance"],
            }
        ],
        results=results,
    )
    rec.ferric = ferric_identity()
    a.result.parent.mkdir(parents=True, exist_ok=True)
    a.result.write_text(rec.to_json() + "\n")
    print(
        f"wrote {a.result}: poses per model {n_by_model}, {len(failures)} unresolved failures, "
        f"{results['total_cpu_s_all_tasks_incl_failures_and_reruns'] / 3600:.2f} CPU-h in all tasks"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plb-dir", type=Path, required=True)
    r.add_argument("--target", default="cdk2")
    r.add_argument("--field", action="append", required=True, metavar="MODEL=PQR")
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--poses", type=int, default=6)
    r.add_argument("--seed", type=int, default=1)
    r.add_argument("--jitter-deg", type=float, default=15.0)
    r.add_argument("--jitter-ang", type=float, default=0.5)
    r.add_argument("--cutoff", type=float, default=15.0)
    r.add_argument("--basis", default="sto-3g")
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--exclude", action="append", default=[], metavar="LIGAND=REASON")
    r.add_argument("--run", nargs="+", required=True, help="which SCFs: 'vac' and/or model names, e.g. vac amber14")
    r.add_argument(
        "--max-cpu-hours", type=float, required=True, help="no new SCF is submitted past this many CPU-hours"
    )
    s = sub.add_parser("assemble")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--result", type=Path, required=True)
    a = ap.parse_args(argv)
    if a.cmd == "run" and not 1 <= a.workers <= 4:
        ap.error("--workers must be 1..4 (shared machine)")
    return cmd_run(a) if a.cmd == "run" else cmd_assemble(a)


if __name__ == "__main__":
    sys.exit(main())
