"""AIMNet2 on carboxylates (plan: docs/benchmarks/aimnet2_carboxylate_plan.md).

The narrower follow-up to the alkoxide miss: does AIMNet2 reproduce a DFT reference for carboxylate
deprotonation to within 5.0 kcal/mol across more than two small molecules AND for a drug-sized carboxylate,
including the danuglipron anion? The earlier rule stands whatever this finds; nothing here edits
`MEASURED_CHARGE_STATES`.

Quantities, fixed by the plan before any number: (1) pair error per acid/carboxylate pair, (2) isodesmic error
against acetic acid, (3) the 5.0 kcal/mol bar applied to EVERY pair and isodesmic error of the carboxylates in the
plan -- a single miss means carboxylates are not entered, (4) forces against a finite difference of the provider's
own energy on the drug-sized carboxylate and the danuglipron anion, against the standing 1e-4 bar.

Each DFT job is capped by RLIMIT_CPU (plan: 1,500 CPU-s); a limit hit is recorded as "not completed" and is NOT
retried. If the full danuglipron pair does not finish, the truncated fragment runs instead and the truncation is
stated in the record.

Run (needs the `ml-potential` extra with aimnet, ferric, rdkit, xtb on PATH):
    OMP_NUM_THREADS=1 python examples/aimnet2_carboxylate_probe.py --out FILE
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.model import Pose
from smeltery.providers import FORCE_FD_RELATIVE_BAR, check_forces
from smeltery.providers.aimnet2 import Aimnet2

HA2KCAL = 627.5094740631
BAR_KCAL = 5.0  # plan item 3
DFT_CPU_SECONDS = 1500  # plan: Budget

#: Danuglipron parent, as in examples/aimnet2_gpu_run.py.
PARENT_SMILES = "C1CO[C@@H]1CN2C3=C(C=CC(=C3)C(=O)O)N=C2CN4CCC(CC4)C5=NC(=CC=C5)OCC6=C(C=C(C=C6)C#N)F"
#: The benzimidazole-carboxylic-acid fragment, H-capped at a C-C cut: the plan's fallback if the 70-atom pair
#: does not finish inside its CPU limit.
TRUNCATED_SMILES = "Cn1c2ccc(cc2nc1C)C(=O)O"
#: Named in docs/benchmarks/aimnet2_carboxylate_plan.md before any energy was computed (commit 3175bc6).
INDOMETHACIN_SMILES = "COc1ccc2c(c1)c(CC(=O)O)c(C)n2C(=O)c1ccc(Cl)cc1"

#: (name, acid SMILES, carboxylate SMILES). The two already-measured small pairs are re-run here because the
#: plan applies its bar to every carboxylate in the plan, including them.
PAIRS: list[tuple[str, str, str]] = [
    ("acetic acid", "CC(=O)O", "CC(=O)[O-]"),
    ("benzoic acid", "OC(=O)c1ccccc1", "[O-]C(=O)c1ccccc1"),
    ("formic acid", "C(=O)O", "C(=O)[O-]"),
    ("propanoic acid", "CCC(=O)O", "CCC(=O)[O-]"),
    ("indomethacin", INDOMETHACIN_SMILES, INDOMETHACIN_SMILES.replace("CC(=O)O)", "CC(=O)[O-])")),
]
#: Pairs whose forces the plan re-measures (item 4).
FORCE_SPECIES = ("indomethacin", "danuglipron")


def deprotonated(smiles: str) -> str:
    """The danuglipron form, where the acid is mid-string: `...C(=O)O)...` (as in examples/aimnet2_gpu_run.py)."""
    return smiles.replace("C(=O)O)", "C(=O)[O-])")


def deprotonated_acid(smiles: str) -> str:
    """A terminal `C(=O)O`, which `deprotonated` does not match because it has no closing paren."""
    if smiles.endswith("C(=O)O"):
        return smiles[: -len("C(=O)O")] + "C(=O)[O-]"
    return deprotonated(smiles)


def embed(smiles: str, seed: int = 20261109):
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    if AllChem.EmbedMolecule(mol, randomSeed=seed) != 0:
        raise RuntimeError(f"could not embed {smiles}")
    AllChem.MMFFOptimizeMolecule(mol, maxIters=2000)
    return mol


def xtb_geometry(smiles: str, charge: int):
    """GFN2-xtb geometry at the right charge, as in the earlier probes."""
    mol = embed(smiles)
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
    return tuple(syms), np.array([[float(v) for v in r.split()[1:4]] for r in out])


def _limit_cpu() -> None:
    resource.setrlimit(resource.RLIMIT_CPU, (DFT_CPU_SECONDS, DFT_CPU_SECONDS))


def dft_energy(syms, xyz, charge: int, mem_budget_gb: float | None = None) -> tuple[float | None, str]:
    """B3LYP/aug-cc-pVDZ single point in a child process under RLIMIT_CPU.

    Returns (energy_kcal_mol, status). A CPU-limit hit is "not completed" and is not retried: the limit is the
    plan's, and silently retrying it would spend budget the plan did not grant.
    """
    rows = "\n".join(f"{s} {x:.8f} {y:.8f} {z:.8f}" for s, (x, y, z) in zip(syms, xyz))
    code = (
        "import ferric,sys,json\n"
        f"mol=ferric.Molecule.from_xyz_string('''{len(syms)}\n\n{rows}\n''',{charge},1)\n"
        "r=ferric.run_dft(mol,ferric.BasisSet.bundled('aug-cc-pvdz'),functional='B3LYP')\n"
        f"print(json.dumps({{'e':r.total_energy*{HA2KCAL}}}))\n"
    )
    env = dict(os.environ)
    if mem_budget_gb is not None:
        env["FERRIC_MEM_BUDGET_GB"] = f"{mem_budget_gb:g}"
    t = time.perf_counter()
    p = subprocess.run(["python", "-c", code], capture_output=True, text=True, preexec_fn=_limit_cpu, env=env)
    wall = time.perf_counter() - t
    if p.returncode != 0:
        tail = (p.stderr or "").strip().splitlines()[-1:] or [""]
        # -24 is SIGXCPU, the plan's CPU limit. -9 is SIGKILL, which on this box means the OOM killer: a
        # DIFFERENT failure from the one the plan budgeted for, and worth naming as such in the record.
        why = {-24: "CPU limit (plan budget)", -9: "SIGKILL, memory"}.get(p.returncode, f"rc={p.returncode}")
        return None, f"not completed ({why}, {wall:.0f} s wall): {tail[0][:200]}"
    return json.loads(p.stdout.strip().splitlines()[-1])["e"], f"ok ({wall:.0f} s wall)"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--skip-danuglipron", action="store_true", help="small pairs and indomethacin only")
    ap.add_argument("--truncated", action="store_true", help="also run the plan's truncated fragment fallback")
    ap.add_argument("--only", default="", help="comma-separated pair names to run (default: all)")
    ap.add_argument(
        "--mem-budget-gb",
        type=float,
        default=None,
        help="FERRIC_MEM_BUDGET_GB for the DFT children. The default budget assumes a bigger box than this one: "
        "the indomethacin anion was SIGKILLed at 15.5 GB resident against an 11.7 GB budget.",
    )
    a = ap.parse_args()

    # Widened for the measurement only, and recorded: the provider still refuses -1 in ordinary use.
    model = Aimnet2(device=a.device, supported_charge_states=frozenset({-1, 0, 1}))
    rec: dict = {
        "plan": "docs/benchmarks/aimnet2_carboxylate_plan.md",
        "bar_kcal_mol": BAR_KCAL,
        "force_bar": FORCE_FD_RELATIVE_BAR,
        "settings": model.settings(),
        "species": {},
        "pairs": {},
        "isodesmic": {},
        "force_fd": {},
        "notes": [],
    }

    pairs = list(PAIRS)
    if not a.skip_danuglipron:
        pairs.append(("danuglipron", PARENT_SMILES, deprotonated(PARENT_SMILES)))
    if a.truncated:
        # The plan's fallback, run as its own named pair so the truncation is explicit in the record rather than
        # silently standing in for the 70-atom result.
        pairs.append(("truncated danuglipron fragment", TRUNCATED_SMILES, deprotonated_acid(TRUNCATED_SMILES)))

    if a.only:
        want = {s.strip() for s in a.only.split(",") if s.strip()}
        pairs = [p for p in pairs if p[0] in want]
        rec["notes"].append(f"--only {sorted(want)}: a partial run, not the plan's full set")

    geo: dict[str, tuple] = {}
    for name, acid_smi, anion_smi in pairs:
        for label, smi, q in ((f"{name}", acid_smi, 0), (f"{name} anion", anion_smi, -1)):
            syms, xyz = xtb_geometry(smi, q)
            geo[label] = (syms, xyz, q)
            e_model = model.energy_and_forces(Pose(syms, xyz), charge=q).energy
            e_dft, status = dft_energy(syms, xyz, q, a.mem_budget_gb)
            rec["species"][label] = {
                "smiles": smi,
                "charge": q,
                "n_atoms": len(syms),
                "model": e_model,
                "dft": e_dft,
                "dft_status": status,
            }
            print(f"{label:24s} n={len(syms):3d} model={e_model:14.4f} dft={e_dft} {status}", flush=True)

    # Plan item 1: pair error. A pair with an incomplete DFT leg is recorded as incomplete, never as a pass.
    for name, _, _ in pairs:
        acid, anion = rec["species"][name], rec["species"][f"{name} anion"]
        if acid["dft"] is None or anion["dft"] is None:
            rec["pairs"][name] = {"error": None, "status": "incomplete (a DFT leg did not finish)"}
            continue
        d_dft = anion["dft"] - acid["dft"]
        d_model = anion["model"] - acid["model"]
        err = d_model - d_dft
        rec["pairs"][name] = {
            "dft": d_dft,
            "model": d_model,
            "error": err,
            "within_bar": abs(err) <= BAR_KCAL,
        }

    # Plan item 2: isodesmic against acetic acid, for each carboxylate other than acetate itself.
    for name, _, _ in pairs:
        if name == "acetic acid":
            continue
        need = (f"{name} anion", "acetic acid", name, "acetic acid anion")
        # Under --only, the acetic acid reference may not have been run at all: that is "not computed", a
        # different thing from a DFT leg that was attempted and died, so say which.
        if any(s not in rec["species"] for s in need):
            rec["isodesmic"][name] = {"error": None, "status": "not computed (the acetic acid reference was not run)"}
            continue
        if any(rec["species"][s]["dft"] is None for s in need):
            rec["isodesmic"][name] = {"error": None, "status": "incomplete (a DFT leg did not finish)"}
            continue
        d = {
            k: (rec["species"][name][k] + rec["species"]["acetic acid anion"][k])
            - (rec["species"][f"{name} anion"][k] + rec["species"]["acetic acid"][k])
            for k in ("dft", "model")
        }
        err = d["model"] - d["dft"]
        rec["isodesmic"][name] = {**d, "error": err, "within_bar": abs(err) <= BAR_KCAL}

    # Plan item 4: forces against a finite difference of the provider's own energy.
    rng = np.random.default_rng(0)
    for label in FORCE_SPECIES:
        key = f"{label} anion"
        if key not in geo:
            continue
        syms, xyz, q = geo[key]
        disp = Pose(syms, xyz + rng.normal(scale=0.05, size=xyz.shape))
        rec["force_fd"][key] = {
            str(step): check_forces(model, disp, charge=q, step_ang=step) for step in (1e-4, 1e-3, 1e-2)
        }
        print(f"force_fd {key}: {rec['force_fd'][key]}", flush=True)

    errs = [v["error"] for v in (*rec["pairs"].values(), *rec["isodesmic"].values()) if v.get("error") is not None]
    incomplete = [k for k, v in (*rec["pairs"].items(), *rec["isodesmic"].items()) if v.get("error") is None]
    # `completed_within_bar` says only that the errors we HAVE are inside the bar. It is not the plan's verdict:
    # the plan requires the drug-sized carboxylate and the danuglipron anion, so an incomplete leg makes the
    # answer "not established", never "met". Reporting a bare all_within_bar over completed rows only would read
    # as a pass while the two species that matter most were missing.
    rec["verdict"] = {
        "worst_abs_error_kcal_mol": max((abs(e) for e in errs), default=None),
        "completed_within_bar": bool(errs) and all(abs(e) <= BAR_KCAL for e in errs),
        "incomplete": sorted(set(incomplete)),
        # An entry needs the plan's FULL set: every pair present, nothing incomplete, and no --only/--skip
        # narrowing. Without the partial check, a two-species --only run that happened to pass would report an
        # entry decision drawn from a fragment of the evidence the plan demands.
        "partial_run": bool(a.only or a.skip_danuglipron),
        "carboxylates_entered": (
            not incomplete
            and not a.only
            and not a.skip_danuglipron
            and bool(errs)
            and all(abs(e) <= BAR_KCAL for e in errs)
        ),
        "rule": "a single miss means carboxylates are NOT entered (plan item 3); an incomplete required "
        "species, or a partial run, means the question is not established, which is also not an entry",
    }
    a.out.write_text(json.dumps(rec, indent=1))
    print(json.dumps({k: rec[k] for k in ("pairs", "isodesmic", "force_fd", "verdict")}, indent=1))


if __name__ == "__main__":
    main()
