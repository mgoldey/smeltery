# ruff: noqa: E501  (long docstring and report-table lines)
"""Single-structure MM/GBSA for the PLB series: dG = E(complex) - E(receptor) - E(ligand), one snapshot, GB solvation.

Runs ONLY in the optional conda environment of docs/environments.md (OpenMM, openmmforcefields, openff-toolkit, AmberTools):

    PATH=/path/to/env/bin:$PATH PYTHONPATH=src nice -n 10 python scripts/plb_mmgbsa.py run \\
        --plb-dir DIR --target cdk2 --out OUTDIR --workers 4
    python scripts/plb_mmgbsa.py assemble --out OUTDIR --result benchmarks/plb/results/cdk2_mmgbsa.json

The protocol is fixed in docs/benchmarks/plb_mmgbsa_plan.md (committed before any series energy was computed):
AMBER ff14SB receptor, ligand AM1-BCC charges from AmberTools' sqm, GAFF2 (primary) or OpenFF Sage 2.2.1 (the one declared
variant), OBC2 GB + OpenMM's ACE surface-area term, the PLB pose unmoved, a minimisation of the complex with the protein
heavy atoms restrained, then the three energies on the SAME minimised snapshot with the Reference platform (double
precision). Entropy, explicit water, ensembles and ligand strain are not included. Nothing under src/smeltery imports this.

Results are appended to OUT/tasks.jsonl as each (ligand, variant) finishes; a restart skips finished ones. The log prints
timings only, never energies (the analysis is committed before anyone looks at the values).
"""

from __future__ import annotations

import os

os.environ.setdefault("OPENMM_CPU_THREADS", "1")  # parallelism is across tasks (processes)
os.environ.setdefault("OMP_NUM_THREADS", "1")

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import multiprocessing  # noqa: E402
import resource  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

SCHEMA = "smeltery-plb-mmgbsa/1"
KJ_PER_KCAL = 4.184
TERMS = ("bonded", "vdw", "elec", "gbsa")  # the reported decomposition; total = their sum
VARIANTS = {
    "gaff2": {"generator": "gaff", "forcefield": "gaff-2.11"},
    "sage": {"generator": "smirnoff", "forcefield": "openff_unconstrained-2.2.1.offxml"},
}
PROTEIN_FF = ("amber14-all.xml",)  # GB is added by add_gb() below (OpenMM's native OBC2, not the xml's CustomGBForce)
RESTRAINT_K_KJ_NM2 = 10.0 * KJ_PER_KCAL * 100.0  # 10 kcal/mol/A^2
RMS_FORCE_TOL = 5.0  # kJ/mol/nm (plan amendment A3; the committed plan said 1.0)
ROUND_ITERS = 1000
MAX_ITERS = 5000
CHARGE_CHECK_TOL = 1e-6  # e
BONDED_CHECK_TOL = 1e-6  # kcal/mol
SHELL_ANG = 10.0  # receptor: residues with any atom within this distance of any atom of any ligand pose of the series
WATER_NAMES = frozenset({"HOH", "WAT", "TIP3", "SOL"})
STANDARD_RESIDUES = frozenset(
    "ALA ARG ASN ASP CYS CYX GLN GLU GLY HIS HID HIE HIP ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL ACE NME".split()
)


class MmgbsaError(RuntimeError):
    """A decision that must not be made silently."""


# ------------------------------------------------------------------------------------------------ pure arithmetic


def delta_terms(complex_: dict[str, float], receptor: dict[str, float], ligand: dict[str, float]) -> dict[str, float]:
    """dG term by term: complex - receptor - ligand (kcal/mol), plus the total. Each input holds `TERMS`."""
    out = {t: float(complex_[t] - receptor[t] - ligand[t]) for t in TERMS}
    out["total"] = float(sum(out[t] for t in TERMS))
    return out


def relative_scores(dg: dict[str, float], reference: str) -> dict[str, float]:
    """ddG_MM(L) = dG(L) - dG(reference) for every ligand (the reference maps to 0)."""
    if reference not in dg:
        raise MmgbsaError(f"the reference {reference!r} has no dG")
    return {n: float(v - dg[reference]) for n, v in dg.items()}


def min_distance_ang(a: np.ndarray, b: np.ndarray) -> float:
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    return float(np.sqrt(((a[:, None, :] - b[None, :, :]) ** 2).sum(-1)).min())


def residues_in_shell(
    residue_xyz: dict[str, np.ndarray], ligand_xyz: np.ndarray, radius_ang: float
) -> dict[str, float]:
    """{residue key: minimum atom-to-ligand distance} for the residues with an atom within `radius_ang` of the ligand atoms."""
    dist = {k: min_distance_ang(xyz, ligand_xyz) for k, xyz in residue_xyz.items()}
    return {k: d for k, d in dist.items() if d <= radius_ang}


def sha256_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------ OpenMM side


def _stack():
    try:
        import openff.toolkit
        import openmm
        import openmmforcefields
        from openff.toolkit import Molecule
        from openmm import app, unit
        from openmmforcefields.generators import GAFFTemplateGenerator, SMIRNOFFTemplateGenerator
    except ImportError as e:  # pragma: no cover - environment dependent
        raise MmgbsaError(
            "needs openmm, openmmforcefields, openff-toolkit and AmberTools (sqm, antechamber on PATH): "
            "see docs/environments.md and environment-openff.yml"
        ) from e
    return {
        "openmm": openmm,
        "app": app,
        "unit": unit,
        "Molecule": Molecule,
        "GAFF": GAFFTemplateGenerator,
        "SMIRNOFF": SMIRNOFFTemplateGenerator,
        "versions": {
            "openmm": openmm.__version__,
            "openff-toolkit": openff.toolkit.__version__,
            "openmmforcefields": openmmforcefields.__version__,
        },
    }


def assign_groups(system) -> None:
    """Force groups: 0 bonded, 1 nonbonded (vdW + Coulomb), 2 GB + SA. Anything else is refused."""
    for f in system.getForces():
        name = type(f).__name__
        if name in ("HarmonicBondForce", "HarmonicAngleForce", "PeriodicTorsionForce"):
            f.setForceGroup(0)
        elif name == "NonbondedForce":
            f.setForceGroup(1)
        elif name in ("CustomGBForce", "GBSAOBCForce"):
            f.setForceGroup(2)
        elif name == "CMMotionRemover":
            f.setForceGroup(3)
        else:
            raise MmgbsaError(f"unexpected force {name} in the System: its energy would be unaccounted for")


def energy_terms(system, positions_nm: np.ndarray) -> dict[str, float]:
    """bonded, vdw, elec, gbsa in kcal/mol from a System on the Reference platform (double precision).

    vdW is the NonbondedForce energy with every charge (and every exception's chargeProd) set to zero; Coulomb is the
    rest of the NonbondedForce energy. The GB force contains the GB and the surface-area energy; they are not split.
    """
    s = _stack()
    mm, unit = s["openmm"], s["unit"]
    system = mm.XmlSerializer.deserialize(mm.XmlSerializer.serialize(system))
    assign_groups(system)
    ref = mm.Platform.getPlatformByName("Reference")

    def evaluate(sys_, groups):
        ctx = mm.Context(sys_, mm.VerletIntegrator(0.001), ref)
        ctx.setPositions(positions_nm)
        e = ctx.getState(getEnergy=True, groups=groups).getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole)
        del ctx
        return e

    bonded, nb, gb = (evaluate(system, {g}) for g in (0, 1, 2))
    nb_force = next(f for f in system.getForces() if type(f).__name__ == "NonbondedForce")
    for i in range(nb_force.getNumParticles()):
        _q, sig, eps = nb_force.getParticleParameters(i)
        nb_force.setParticleParameters(i, 0.0, sig, eps)
    for i in range(nb_force.getNumExceptions()):
        a, b, _qq, sig, eps = nb_force.getExceptionParameters(i)
        nb_force.setExceptionParameters(i, a, b, 0.0, sig, eps)
    vdw = evaluate(system, {1})
    return {
        "bonded": bonded / KJ_PER_KCAL,
        "vdw": vdw / KJ_PER_KCAL,
        "elec": (nb - vdw) / KJ_PER_KCAL,
        "gbsa": gb / KJ_PER_KCAL,
    }


def prepare_receptor(pdb_path: Path, ligand_xyz_ang: np.ndarray, radius_ang: float = SHELL_ANG):
    """(Modeller of the receptor, provenance): crystal waters dropped, then every residue with no atom within `radius_ang`
    of `ligand_xyz_ang` (the atoms of ALL the series' ligand poses, so one receptor serves every ligand) deleted.

    Whole residues are kept or dropped. A residue the force field has no template for that stays in the shell is an error
    at createSystem, never skipped.
    """
    s = _stack()
    app, unit = s["app"], s["unit"]
    pdb = app.PDBFile(str(pdb_path))
    mod = app.Modeller(pdb.topology, pdb.positions)
    pos = np.asarray(mod.positions.value_in_unit(unit.angstrom))
    index = {a.index: i for i, a in enumerate(mod.topology.atoms())}
    xyz, to_delete, n_water = {}, [], 0
    for r in mod.topology.residues():
        if r.name in WATER_NAMES:
            to_delete.append(r)
            n_water += 1
        else:
            xyz[f"{r.name}:{r.chain.id}:{r.id}"] = np.array([pos[index[a.index]] for a in r.atoms()])
    keep = residues_in_shell(xyz, ligand_xyz_ang, radius_ang)
    dropped = [
        r for r in mod.topology.residues() if r.name not in WATER_NAMES and f"{r.name}:{r.chain.id}:{r.id}" not in keep
    ]
    far = {
        f"{r.name}:{r.chain.id}:{r.id}": min_distance_ang(xyz[f"{r.name}:{r.chain.id}:{r.id}"], ligand_xyz_ang)
        for r in dropped
    }
    n_total = mod.topology.getNumResidues() - n_water
    mod.delete(to_delete + dropped)
    prov = {
        "protein_sha256": sha256_file(pdb_path),
        "shell_radius_ang": radius_ang,
        "n_waters_dropped": n_water,
        "n_residues_in_file_without_water": n_total,
        "n_residues_kept": int(mod.topology.getNumResidues()),
        "nearest_dropped_residue_ang": min(far.values()) if far else None,
        "residue_names_non_standard_kept": sorted({r.name for r in mod.topology.residues()} - STANDARD_RESIDUES),
        "n_atoms_receptor": int(mod.topology.getNumAtoms()),
    }
    return mod, prov


def add_gb(system, topology) -> None:
    """Add OpenMM's native GBSAOBCForce (OBC type II: alpha 1.0, beta 0.8, gamma 4.85; Amber igb 5) with Amber mbondi2
    intrinsic radii and the OBC screening parameters by element, from OpenMM's own tables (`openmm.app.internal.customgbforces`),
    the charges from the System's NonbondedForce, solute dielectric 1, solvent dielectric 78.5, no salt, and the force's default
    ACE surface-area term. The same model as `implicit/obc2.xml`'s CustomGBForce, about 7 times faster on the CPU platform.
    """
    s = _stack()
    mm = s["openmm"]
    from openmm.app.internal import customgbforces as cg

    nb = next(f for f in system.getForces() if type(f).__name__ == "NonbondedForce")
    radii = cg._mbondi2_radii(topology) / 10.0  # nm
    gb = mm.GBSAOBCForce()
    for i, atom in enumerate(topology.atoms()):
        gb.addParticle(nb.getParticleParameters(i)[0], float(radii[i]), float(cg._screen_parameter(atom)[0]))
    gb.setSolventDielectric(78.5)
    gb.setSoluteDielectric(1.0)
    gb.setNonbondedMethod(mm.GBSAOBCForce.NoCutoff)
    system.addForce(gb)


def create_system(ff, topology):
    """A System with the force field's terms, no constraints, no cutoff, plus the GB force."""
    system = ff.createSystem(
        topology, nonbondedMethod=_stack()["app"].NoCutoff, constraints=None, ignoreExternalBonds=True
    )
    add_gb(system, topology)
    return system


def ligand_molecule(symbols, coords_ang: np.ndarray, sdf_mol):
    """(openff Molecule with the pose as its conformer and AM1-BCC charges, charge provenance)."""
    s = _stack()
    Molecule = s["Molecule"]
    from openff.units import unit as ounit

    off = Molecule.from_rdkit(sdf_mol, allow_undefined_stereo=True, hydrogens_are_explicit=True)
    if [a.symbol for a in off.atoms] != list(symbols):
        raise MmgbsaError("atom order changed on the way into openff-toolkit")
    off.assign_partial_charges("am1bcc", use_conformers=off.conformers)
    q = off.partial_charges.m_as(ounit.elementary_charge)
    return off, {
        "charge_method": "AM1-BCC (antechamber/sqm via openff-toolkit), started from the PLB pose; sqm minimises first",
        "charge_sum_e": float(q.sum()),
        "formal_charge": int(off.total_charge.m_as(ounit.elementary_charge)),
    }


def build_forcefield(variant: str, off_mol):
    s = _stack()
    spec = VARIANTS[variant]
    ff = s["app"].ForceField(*PROTEIN_FF)
    cls = s["GAFF"] if spec["generator"] == "gaff" else s["SMIRNOFF"]
    gen = cls(molecules=off_mol, forcefield=spec["forcefield"])
    ff.registerTemplateGenerator(gen.generator)
    return ff


def check_ligand_charges(system, n_protein: int, off_mol) -> float:
    """Largest |System charge - AM1-BCC charge| over the ligand atoms (e); refuse above CHARGE_CHECK_TOL."""
    from openff.units import unit as ounit

    s = _stack()
    unit = s["unit"]
    nb = next(f for f in system.getForces() if type(f).__name__ == "NonbondedForce")
    q_sys = np.array(
        [
            nb.getParticleParameters(n_protein + i)[0].value_in_unit(unit.elementary_charge)
            for i in range(off_mol.n_atoms)
        ]
    )
    dev = float(np.abs(q_sys - off_mol.partial_charges.m_as(ounit.elementary_charge)).max())
    if dev > CHARGE_CHECK_TOL:
        raise MmgbsaError(f"the ligand force field did not use the supplied charges (max deviation {dev:.2e} e)")
    return dev


def minimise_complex(system, positions_nm: np.ndarray, protein_heavy: list[int]):
    """Minimise the complex with the protein heavy atoms restrained; (positions_nm, info). CPU platform, single precision."""
    s = _stack()
    mm, unit = s["openmm"], s["unit"]
    system = mm.XmlSerializer.deserialize(mm.XmlSerializer.serialize(system))
    restraint = mm.CustomExternalForce("k*((x-x0)^2+(y-y0)^2+(z-z0)^2)")
    restraint.addGlobalParameter("k", RESTRAINT_K_KJ_NM2)
    for p in ("x0", "y0", "z0"):
        restraint.addPerParticleParameter(p)
    for i in protein_heavy:
        restraint.addParticle(i, list(positions_nm[i]))
    system.addForce(restraint)
    plat = mm.Platform.getPlatformByName("CPU")
    ctx = mm.Context(system, mm.VerletIntegrator(0.001), plat, {"Threads": "1"})
    ctx.setPositions(positions_nm)
    used, rms = 0, float("inf")
    while used < MAX_ITERS:
        mm.LocalEnergyMinimizer.minimize(ctx, RMS_FORCE_TOL, ROUND_ITERS)
        used += ROUND_ITERS
        f = ctx.getState(getForces=True).getForces(asNumpy=True).value_in_unit(unit.kilojoule_per_mole / unit.nanometer)
        rms = float(np.sqrt((np.asarray(f) ** 2).sum(1).mean()))
        if rms <= RMS_FORCE_TOL:
            break
    final = np.asarray(ctx.getState(getPositions=True).getPositions(asNumpy=True).value_in_unit(unit.nanometer))
    return final, {
        "iterations_budget_used": used,
        "rms_force_kj_mol_nm": rms,
        "converged": bool(rms <= RMS_FORCE_TOL),
    }


def score_ligand(name: str, variant: str, plb_dir: Path, ligs: dict):
    """One (ligand, variant): the three energies on one minimised snapshot. Returns the task record (no energies printed)."""
    s = _stack()
    app, unit = s["app"], s["unit"]
    from rdkit import Chem

    t0, c0 = time.time(), _cpu_s()
    sdf_mols = {m.GetProp("_Name"): m for m in Chem.SDMolSupplier(str(plb_dir / "ligands.sdf"), removeHs=False)}
    sdf = sdf_mols[name]
    coords = np.array(sdf.GetConformer().GetPositions(), dtype=float)
    union = np.vstack([d["coords"] for d in ligs.values()])  # every ligand pose of the series: one receptor for all
    rec, rec_prov = prepare_receptor(plb_dir / "protein.pdb", union)
    off, charge_prov = ligand_molecule(ligs[name]["symbols"], coords, sdf)
    n_p = rec.topology.getNumAtoms()
    com = app.Modeller(rec.topology, rec.positions)
    com.add(off.to_topology().to_openmm(), coords / 10.0 * unit.nanometer)
    ff = build_forcefield(variant, off)
    system = create_system(ff, com.topology)
    dev = check_ligand_charges(system, n_p, off)
    pos0 = np.asarray(com.positions.value_in_unit(unit.nanometer))
    heavy = [
        a.index for a in com.topology.atoms() if a.index < n_p and a.element is not None and a.element.symbol != "H"
    ]
    pos, info = minimise_complex(system, pos0, heavy)
    ligand_move = float(np.sqrt(((pos[n_p:] - pos0[n_p:]) ** 2).sum(1).mean()) * 10.0)

    e_c = energy_terms(system, pos)
    sys_r = create_system(app.ForceField(*PROTEIN_FF), rec.topology)
    e_r = energy_terms(sys_r, pos[:n_p])
    lig_top = off.to_topology().to_openmm()
    sys_l = create_system(build_forcefield(variant, off), lig_top)
    e_l = energy_terms(sys_l, pos[n_p:])
    dg = delta_terms(e_c, e_r, e_l)
    if abs(dg["bonded"]) > BONDED_CHECK_TOL:
        raise MmgbsaError(f"{name}/{variant}: the bonded term of dG is {dg['bonded']:.3e} kcal/mol, not zero")
    return {
        "kind": "mmgbsa",
        "ligand": name,
        "variant": variant,
        "dG": dg,
        "E_complex": e_c,
        "E_receptor": e_r,
        "E_ligand": e_l,
        "minimisation": {**info, "ligand_rms_displacement_ang": ligand_move},
        "charges": {**charge_prov, "max_deviation_from_system_e": dev},
        "receptor": rec_prov,
        "wall_s": time.time() - t0,
        "cpu_s": _cpu_s() - c0,
    }


def _cpu_s() -> float:
    own, kids = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
    return own.ru_utime + own.ru_stime + kids.ru_utime + kids.ru_stime


# ------------------------------------------------------------------------------------------------ driver


def _worker(args):
    name, variant, plb_dir, target = args
    ligs = _load_ligs(Path(plb_dir), target)[1]
    try:
        return score_ligand(name, variant, Path(plb_dir), ligs)
    except Exception as e:  # recorded, never silently dropped
        return {"kind": "error", "ligand": name, "variant": variant, "error": f"{type(e).__name__}: {str(e)[:500]}"}


def _load_ligs(plb_dir: Path, target: str):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import run_plb_ddE as rp

    _bench, tgt, ligs = rp.load_series(plb_dir, target)
    return tgt, ligs


def read_tasks(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.is_file() else []


def cmd_run(a: argparse.Namespace) -> int:
    tgt, ligs = _load_ligs(a.plb_dir, a.target)
    parent = tgt.reference
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks_path = out / "tasks.jsonl"
    done = {(r["ligand"], r["variant"]) for r in read_tasks(tasks_path) if r["kind"] == "mmgbsa"}
    names = [parent, *sorted(n for n in ligs if n != parent)]
    if a.ligands:
        names = [n for n in names if n in a.ligands]
    todo = [(n, v, str(a.plb_dir), a.target) for v in a.variants for n in names if (n, v) not in done]
    cap_s = a.cpu_cap_h * 3600.0
    spent = sum(r.get("cpu_s", 0.0) for r in read_tasks(tasks_path))
    print(
        f"{len(todo)} tasks to run, {len(done)} finished, {spent / 3600:.2f} CPU-h spent, cap {a.cpu_cap_h} CPU-h",
        flush=True,
    )
    ctx = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=a.workers, mp_context=ctx) as ex, tasks_path.open("a") as fh:
        pending, it = set(), iter(todo)
        for _ in range(a.workers):
            t = next(it, None)
            if t:
                pending.add(ex.submit(_worker, t))
        while pending:
            fin, pending = wait(pending, return_when=FIRST_COMPLETED)
            for f in fin:
                r = f.result()
                fh.write(json.dumps(r, sort_keys=True) + "\n")
                fh.flush()
                if r["kind"] == "error":
                    print(f"ERROR {r['ligand']}/{r['variant']}: {r['error']}", flush=True)
                else:
                    spent += r["cpu_s"]
                    print(
                        f"done {r['ligand']}/{r['variant']}: {r['cpu_s']:.0f} CPU s, minimisation "
                        f"{'converged' if r['minimisation']['converged'] else 'NOT converged'}; {spent / 3600:.2f} CPU-h total",
                        flush=True,
                    )
                if spent < cap_s:
                    t = next(it, None)
                    if t:
                        pending.add(ex.submit(_worker, t))
    if spent >= cap_s:
        print(f"CPU cap reached ({spent / 3600:.2f} CPU-h): the remaining tasks were not started", flush=True)
    return 0


def cmd_assemble(a: argparse.Namespace) -> int:
    tasks = read_tasks(Path(a.out) / "tasks.jsonl")
    if a.versions_from_env:
        versions = _stack()["versions"]
    else:
        versions = None
    good = [r for r in tasks if r["kind"] == "mmgbsa"]
    errors = [r for r in tasks if r["kind"] == "error"]
    meta = json.loads((Path(a.out) / "meta.json").read_text())
    ligands: dict[str, dict[str, dict]] = {}
    for r in good:
        ligands.setdefault(r["ligand"], {})[r["variant"]] = {
            k: r[k]
            for k in (
                "dG",
                "E_complex",
                "E_receptor",
                "E_ligand",
                "minimisation",
                "charges",
                "receptor",
                "cpu_s",
                "wall_s",
            )
        }
    rec = {
        "schema": SCHEMA,
        **meta,
        "versions": versions or meta.get("versions"),
        "ligands": ligands,
        "errors": errors,
        "total_cpu_s": float(sum(r["cpu_s"] for r in good)),
    }
    Path(a.result).write_text(json.dumps(rec, indent=1, sort_keys=True) + "\n")
    print(f"{len(good)} scores, {len(errors)} errors, {rec['total_cpu_s'] / 3600:.2f} CPU-h -> {a.result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--plb-dir", type=Path, required=True)
    r.add_argument("--target", required=True)
    r.add_argument("--out", type=Path, required=True)
    r.add_argument("--workers", type=int, default=4)
    r.add_argument("--variants", nargs="+", default=["gaff2", "sage"], choices=sorted(VARIANTS))
    r.add_argument("--ligands", nargs="+", default=None, help="run only these ligands (default: all)")
    r.add_argument("--cpu-cap-h", type=float, default=6.0)
    s = sub.add_parser("assemble")
    s.add_argument("--out", type=Path, required=True)
    s.add_argument("--result", type=Path, required=True)
    s.add_argument("--versions-from-env", action="store_true")
    a = ap.parse_args(argv)
    if a.cmd == "run":
        tgt, ligs = _load_ligs(a.plb_dir, a.target)
        out = Path(a.out)
        out.mkdir(parents=True, exist_ok=True)
        (out / "meta.json").write_text(
            json.dumps(
                {
                    "target": a.target,
                    "reference": tgt.reference,
                    "ligands": sorted(ligs),
                    "variants": a.variants,
                    "protocol": {
                        "protein_ff": list(PROTEIN_FF),
                        "variants": {k: VARIANTS[k] for k in a.variants},
                        "restraint_k_kj_mol_nm2": RESTRAINT_K_KJ_NM2,
                        "rms_force_tol_kj_mol_nm": RMS_FORCE_TOL,
                        "max_minimisation_iterations": MAX_ITERS,
                        "shell_radius_ang": SHELL_ANG,
                        "plan": "docs/benchmarks/plb_mmgbsa_plan.md",
                    },
                    "versions": _stack()["versions"],
                    "script_sha256": sha256_file(Path(__file__)),
                    "command": sys.argv,
                },
                indent=1,
                sort_keys=True,
            )
            + "\n"
        )
        return cmd_run(a)
    return cmd_assemble(a)


if __name__ == "__main__":
    sys.exit(main())
