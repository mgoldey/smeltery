"""Danuglipron halogen scan through the smeltery funnel: F/Cl/CH3 at 4 aromatic CH sites vs the parent.

Run:  OPENBLAS_NUM_THREADS=1 uv run python examples/danuglipron_halogen.py [--poses N] [--out DIR]

Inputs: the 7LCJ pocket (PDB, charges from pdb2pqr30, NO distance cutoff) and the
cryo-EM bound conformer of danuglipron (conf_00_cryo_em.xyz), both committed under
experiments/danuglipron/data/c9_danuglipron. Output: a table, assertions for
the acceptance criteria of mgoldey/smeltery#6, and a RunRecord JSON.

WHAT IS AND IS NOT REPRODUCED (read before citing anything here)
  * The campaign's per-analogue paired ddE values and SEMs could not be found in any
    local ferric checkout or branch (see CAMPAIGN_RECORDED below). Acceptance
    criterion 2 (agreement within SEM) is therefore UNVERIFIED, not passed. The
    comparison table prints "n/a" for the recorded column rather than a guess.
  * The campaign's exact 12-analogue set is also not recorded anywhere found. The
    four sites below are an a-priori choice (one benzimidazole CH, one pyridine CH,
    two benzonitrile-ring CH), fixed before any energy was computed, 3 substituents
    each. They are NOT known to be the campaign's sites.
  * The SMILES given is the NEUTRAL acid, and the tier scores the net charge the SMILES
    carries; the campaign scored the carboxylate. Both are disclosed in the record.
  * Parent poses: the cryo-EM bound conformer plus seeded rigid jitters
    (15 deg / 0.5 A), mimicking docked-pose spread. Not docked, not relaxed.
  * Second charge model: pdb2pqr30 --ff=CHARMM against the default AMBER (pass --skip-model-b to omit it). The
    campaign's second model is not recorded. Two force fields share the same
    structure, protonation tool and point-charge approximation, so their
    disagreement is a LOWER bound on charge-model uncertainty. Without it the cut uses ONLY the declared
    floor (`DECLARED_FLOOR_KCAL_MOL`, a lower bound taken from other targets, see its comment), and the sign-flip and
    Spearman comparison with the campaign is not run.
  * The run is metered: each SCF records its CPU seconds and `--cap-cpu-hours` stops a pass at the cap.
    Plan and outcome language: docs/benchmarks/danuglipron_halogen_plan.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from itertools import pairwise
from pathlib import Path

os.environ.setdefault("RAYON_NUM_THREADS", "1")  # parallelism is across poses (processes), not inside ferric
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")

import numpy as np
from rdkit import Chem
from rdkit.Chem import rdDetermineBonds

from smeltery import (
    Candidate,
    Measurement,
    Pose,
    RunRecord,
    charge_sensitivity,
    cut,
    load_pocket,
    paired_delta,
    unpaired_delta,
)
from smeltery.tiers import FieldInteraction, PairedPoses, _mol_with_h, _rigid_jitter

PARENT_SMILES = "C1CO[C@@H]1CN2C3=C(C=CC(=C3)C(=O)O)N=C2CN4CCC(CC4)C5=NC(=CC=C5)OCC6=C(C=C(C=C6)C#N)F"
# Atom indices (heavy-atom order of PARENT_SMILES) of the four substituted aromatic CH sites.
SITES = {"bzim-C9": 9, "pyr-C28": 28, "bnz-C34": 34, "bnz-C36": 36}
SUBSTITUENTS = {"F": "F", "Cl": "Cl", "CH3": "C"}
# The tier default (10 s) is too short for this 74-atom parent: the exact MCS needs 2 s (bzim sites) to ~75 s
# (bnz-C36-CH3) on the dev box, and the default hit its timeout, so NoCommonCoreError, for 9 of the 12 analogues. The
# search is exact when it finishes, so a longer limit changes only whether it finishes; it is recorded in the
# tier settings.
MCS_TIMEOUT_S = 900
# FieldInteraction's systematic floor is UNMEASURED (docs/tiers/field_interaction.md), so the cut needs a DECLARED
# floor. 1.26 kcal/mol is the larger of the two measured pocket-side charge-model sensitivities (AMBER ff14SB vs
# CHARMM36, docs/limits.md L6 cdk2 1.26, L11 tyk2 0.67), each a lower bound measured on another target.
DECLARED_FLOOR_KCAL_MOL = 1.26
DECLARED_FLOOR_SOURCE = "1.26 kcal/mol, docs/limits.md L6 (cdk2; L11 tyk2 is 0.67), a lower bound from other targets"

# The campaign's recorded per-analogue paired ddE: {name: (mean, sem)} in kcal/mol.
# EMPTY ON PURPOSE: searched ferric experiments/danuglipron (RESULTS.md, PLAN.md, out/*.json),
# tools/morph, tools/pipeline, site/src/reference, validation_handoff.txt and the sibling
# checkouts; only summary statistics survive (rho 0.78-0.9997, CH3 SEM 1.0-3.5, self-anchor
# +0.31, 4/12 flips, Spearman +0.664), not per-analogue values. Do not fill this with guesses.
CAMPAIGN_RECORDED: dict[str, tuple[float, float]] = {}
CAMPAIGN_SUMMARY = {
    "rho": (0.78, 0.9997),
    "CH3_sem": (1.0, 3.5),
    "self_anchor": 0.31,
    "n_sign_flips": 4,
    "n": 12,
    "spearman": 0.664,
}


def find_data_dir() -> Path:
    """The committed danuglipron inputs (moved from ferric's testdata with the campaign, issue #13)."""
    d = Path(__file__).resolve().parents[1] / "experiments" / "danuglipron" / "data" / "c9_danuglipron"
    for name in ("7LCJ_pocket.pdb", "conf_00_cryo_em.xyz"):
        if not (d / name).is_file():
            raise FileNotFoundError(f"{d / name} is missing; it is committed with experiments/danuglipron")
    return d


def analogue_smiles(sites: dict[str, int] | None = None) -> dict[str, str]:
    """name -> SMILES: replace the aromatic CH at each site with F, Cl or CH3."""
    out = {}
    for sname, idx in (SITES if sites is None else sites).items():
        for sub, elem in SUBSTITUENTS.items():
            rw = Chem.RWMol(Chem.MolFromSmiles(PARENT_SMILES))
            atom = rw.GetAtomWithIdx(idx)
            if not (atom.GetIsAromatic() and atom.GetTotalNumHs() == 1):
                raise ValueError(f"site {sname} (atom {idx}) is not an aromatic CH")
            new = rw.AddAtom(Chem.Atom(elem))
            rw.AddBond(idx, new, Chem.BondType.SINGLE)
            mol = rw.GetMol()
            Chem.SanitizeMol(mol)
            out[f"{sname}-{sub}"] = Chem.MolToSmiles(mol)
    return out


def bound_pose_in_smiles_order(xyz_path: Path) -> Pose:
    """The cryo-EM conformer, relabelled to the atom order of `AddHs(PARENT_SMILES)`.

    Heavy atoms by skeleton substructure match on the connectivity perceived from
    the file; each hydrogen goes to a slot of the heavy atom it sits on.
    Hydrogens of one heavy atom are interchangeable, so slot order within a heavy
    atom is immaterial. Coordinates are copied, never altered.
    """
    pmol = _mol_with_h(PARENT_SMILES)
    xmol = Chem.MolFromXYZFile(str(xyz_path))
    rdDetermineBonds.DetermineConnectivity(xmol)
    xyz = np.array([list(xmol.GetConformer().GetAtomPosition(i)) for i in range(xmol.GetNumAtoms())])

    def skeleton(m):
        r = Chem.RWMol(Chem.RemoveHs(m, sanitize=False))
        for b in r.GetBonds():
            b.SetBondType(Chem.BondType.SINGLE)
            b.SetIsAromatic(False)
        for a in r.GetAtoms():
            a.SetIsAromatic(False)
            a.SetFormalCharge(0)
        return r.GetMol()

    sx, sp = skeleton(xmol), skeleton(pmol)
    match = sp.GetSubstructMatch(sx)  # xyz heavy-atom order index -> pmol heavy-atom index
    if len(match) != sx.GetNumAtoms():
        raise RuntimeError("cryo-EM conformer does not match the parent's heavy-atom skeleton")
    xheavy = [a.GetIdx() for a in xmol.GetAtoms() if a.GetAtomicNum() > 1]
    out = np.full((pmol.GetNumAtoms(), 3), np.nan)
    for xi, pi in zip(xheavy, match, strict=True):
        out[pi] = xyz[xi]
    xh_by_heavy: dict[int, list[int]] = {}
    for a in xmol.GetAtoms():
        if a.GetAtomicNum() == 1:
            nb = [n.GetIdx() for n in a.GetNeighbors() if n.GetAtomicNum() > 1]
            if len(nb) != 1:
                raise RuntimeError("hydrogen without exactly one heavy neighbour in the cryo-EM conformer")
            xh_by_heavy.setdefault(nb[0], []).append(a.GetIdx())
    x2p = dict(zip(xheavy, match, strict=True))
    for xi, hs in xh_by_heavy.items():
        slots = [n.GetIdx() for n in pmol.GetAtomWithIdx(x2p[xi]).GetNeighbors() if n.GetAtomicNum() == 1]
        if len(slots) != len(hs):
            raise RuntimeError("hydrogen count differs between the cryo-EM conformer and the parent SMILES")
        for slot, h in zip(slots, hs, strict=True):
            out[slot] = xyz[h]
    if np.isnan(out).any():
        raise RuntimeError("some parent atoms received no cryo-EM coordinates")
    return Pose(tuple(a.GetSymbol() for a in pmol.GetAtoms()), out)


class BoundPosePairedPoses(PairedPoses):
    """`PairedPoses` with the parent ensemble built on the cryo-EM bound conformer.

    Parent pose 0 is the bound conformer; poses 1.. are seeded rigid jitters of it.
    Analogue pairing is `PairedPoses._paired`, unchanged: mapped atoms are exact
    copies of the parent pose, so pairing a molecule with itself copies everything.
    """

    def __init__(self, bound: Pose, **kw):
        super().__init__(**kw)
        self.bound = bound

    def run(self, candidates: list[Candidate], ctx: dict) -> None:
        parent = ctx["parent"]
        pmol = _mol_with_h(parent.smiles)
        if tuple(a.GetSymbol() for a in pmol.GetAtoms()) != self.bound.symbols:
            raise ValueError("bound pose atom order does not match the parent SMILES")
        rng = np.random.default_rng(self.seed)
        base = self.bound.coords_ang
        parent_poses = [base.copy()] + [
            _rigid_jitter(base, rng, self.jitter_deg, self.jitter_ang) for _ in range(self.n_poses - 1)
        ]
        parent.poses = [Pose(self.bound.symbols, x) for x in parent_poses]
        for cand in candidates:
            if cand is not parent:
                cand.poses = self._paired(pmol, parent_poses, cand)


def make_pairing(bound: Pose, n_poses: int) -> BoundPosePairedPoses:
    """The pairing tier as this example runs it: default seed, jitter and core rules, MCS limit `MCS_TIMEOUT_S`."""
    return BoundPosePairedPoses(bound, n_poses=n_poses, mcs_timeout_s=MCS_TIMEOUT_S)


class CpuCapReached(RuntimeError):
    """The CPU-time cap stopped the pass; every finished SCF is in the cache and a rerun resumes from it."""


class ScfFailed(RuntimeError):
    """One or more SCFs failed (e.g. did not converge). They are cached as failed and are never retried."""


def _one_pose(name: str, smiles: str, pose: Pose, field) -> tuple[float | None, float, str | None]:
    """Worker: (dE_int, CPU seconds, error) of one pose, through the real `FieldInteraction` tier.

    An SCF that raises (non-convergence) returns (None, cpu_spent, message) so its CPU is metered and the other
    workers' results are not discarded with it.
    """
    t0 = time.process_time()
    c = Candidate(name, smiles, [pose])
    try:
        FieldInteraction().run([c], {"field": field})
    except RuntimeError as e:
        return None, time.process_time() - t0, str(e)
    return c.per_pose["dE_int"][0], time.process_time() - t0, None


def run_field_tier(
    cands: list[Candidate],
    field,
    tag: str,
    workers: int,
    cache_path: Path,
    cap_cpu_s: float | None = None,
    est_cpu_s: float = 0.0,
) -> FieldInteraction:
    """`FieldInteraction` over every pose of every candidate, one process per pose, with a resumable cache.

    Results are identical to `FieldInteraction().run(cands, ...)`; only scheduling differs. The cache key is
    the pose geometry plus the field's input digest and force field, so a stale entry cannot be reused.
    Work is scheduled pose-major with `workers` calls in flight (never the whole queue). Each entry stores the
    worker's CPU seconds. A new SCF is submitted only if the CPU spent by THIS call, plus `est_cpu_s` for every SCF
    in flight and for the new one, stays within `cap_cpu_s`; if work remains when that stops submission,
    `CpuCapReached` is raised after the in-flight SCFs are collected and cached. A failed SCF is cached as failed
    (with its CPU), is never retried, stops further submission, and `ScfFailed` is raised after the in-flight
    SCFs are collected and cached.
    """
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    tier = FieldInteraction()
    tier.field_provenance = getattr(field, "provenance", None)
    fkey = json.dumps(
        {k: tier.field_provenance.get(k) for k in ("input_sha256", "pdb2pqr_ff", "cutoff_ang")}, sort_keys=True
    )
    keys = {
        (c.name, i): hashlib.sha256((fkey + tag + c.name + p.to_xyz()).encode()).hexdigest()
        for c in cands
        for i, p in enumerate(c.poses)
    }
    todo = sorted(
        ((c, i) for c in cands for i in range(len(c.poses)) if keys[(c.name, i)] not in cache), key=lambda t: t[1]
    )
    print(f"  {tag}: {len(keys) - len(todo)} cached, {len(todo)} to run on {workers} workers", flush=True)
    spent = 0.0
    failures: list[str] = []
    pending = list(reversed(todo))
    if todo:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            live: dict = {}
            while pending or live:
                # Rolling window: ProcessPoolExecutor hands every queued call to its call queue at once, where it can
                # no longer be cancelled, so the whole queue is never submitted.
                while pending and len(live) < workers and not failures:
                    if cap_cpu_s is not None and spent + est_cpu_s * (len(live) + 1) > cap_cpu_s:
                        break
                    c, i = pending.pop()
                    live[ex.submit(_one_pose, c.name, c.smiles, c.poses[i], field)] = (c, i)
                if not live:
                    break
                done, _ = wait(live, return_when=FIRST_COMPLETED)
                for fut in done:
                    c, i = live.pop(fut)
                    de, cpu, err = fut.result()
                    spent += cpu
                    if err is None:
                        cache[keys[(c.name, i)]] = {"dE": de, "cpu_s": cpu}
                        msg = f"done, {cpu:.0f} CPU-s"
                    else:
                        cache[keys[(c.name, i)]] = {"dE": None, "cpu_s": cpu, "error": err}
                        failures.append(f"{c.name} pose {i}: {err}")
                        msg = f"FAILED ({err}), {cpu:.0f} CPU-s"
                    cache_path.write_text(json.dumps(cache))
                    print(f"  {tag}: {c.name} pose {i} {msg} ({spent / 3600:.2f} CPU-h this call)", flush=True)
    if failures:
        raise ScfFailed("; ".join(failures))
    if pending:
        raise CpuCapReached(
            f"{tag}: {len(pending)} SCFs not started; {spent / 3600:.2f} CPU-h spent against a cap of "
            f"{(cap_cpu_s or 0) / 3600:.2f} CPU-h (in-flight SCFs counted at {est_cpu_s:.0f} CPU-s each)"
        )
    for c in cands:
        entries = [cache[keys[(c.name, i)]] for i in range(len(c.poses))]
        if any(e.get("error") for e in entries):  # a cached failure from an earlier call is not retried
            raise ScfFailed(f"{c.name}: cached SCF failure {[e.get('error') for e in entries if e.get('error')]}")
        c.per_pose["dE_int"] = [e["dE"] for e in entries]
        c.per_pose["cpu_s"] = [e["cpu_s"] for e in entries]
    return tier


def range_report(values: dict[str, float], lo: float, hi: float) -> dict:
    """Criterion 2, range form: which values fall inside the closed range [lo, hi] and which outside (no tolerance)."""
    inside = sorted(n for n, v in values.items() if lo <= v <= hi)
    outside = sorted(n for n, v in values.items() if not lo <= v <= hi)
    return {"range": [lo, hi], "n_inside": len(inside), "n_outside": len(outside), "inside": inside, "outside": outside}


def unranked_pairs(paired: dict[str, Measurement], floor: float) -> list[tuple[str, str, float]]:
    """Adjacent pairs (by mean ddE) whose gap is at or below the floor: these must not be reported as ordered."""
    order = sorted(paired, key=lambda k: paired[k].mean)
    gaps = [(a, b, paired[b].mean - paired[a].mean) for a, b in pairwise(order)]
    return [(a, b, g) for a, b, g in gaps if g <= floor]


def compare_to_campaign(paired: dict[str, Measurement]) -> tuple[list[dict], str]:
    """Criterion 2: rows (name, ours, recorded, within SEM) and a status: UNVERIFIED if nothing recorded."""
    rows, checked = [], 0
    for name, m in paired.items():
        rec = CAMPAIGN_RECORDED.get(name)
        if rec is None:
            rows.append(
                {"name": name, "mean": m.mean, "sem": m.sem, "recorded": None, "recorded_sem": None, "agrees": None}
            )
            continue
        checked += 1
        rows.append(
            {
                "name": name,
                "mean": m.mean,
                "sem": m.sem,
                "recorded": rec[0],
                "recorded_sem": rec[1],
                "agrees": bool(abs(m.mean - rec[0]) <= m.sem),
            }
        )
    if checked == 0:
        return rows, "UNVERIFIED: no campaign per-analogue values available"
    ok = all(r["agrees"] for r in rows if r["agrees"] is not None)
    return rows, f"{'AGREES' if ok else 'DOES NOT AGREE'} for {checked}/{len(rows)} analogues with recorded values"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poses", type=int, default=6)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument(
        "--cap-cpu-hours", type=float, default=26.0, help="stop (CpuCapReached) once a field pass has spent this much"
    )
    ap.add_argument(
        "--est-scf-cpu-s",
        type=float,
        default=2000.0,
        help="CPU seconds assumed for every in-flight SCF when deciding whether the cap allows another submission",
    )
    ap.add_argument(
        "--skip-first-poses",
        type=int,
        default=0,
        help="drop this many leading pose indices for every molecule (plan amendment A1 drops pose 0)",
    )
    ap.add_argument(
        "--floor",
        type=float,
        default=DECLARED_FLOOR_KCAL_MOL,
        help="declared systematic floor for the cut (kcal/mol); default " + DECLARED_FLOOR_SOURCE,
    )
    ap.add_argument("--sites", default=None, help="comma-separated subset of " + ",".join(SITES) + " (default: all)")
    ap.add_argument(
        "--skip-model-b",
        action="store_true",
        help="skip the CHARMM pass: criterion 4 (sensitivity floor and cut) is then reported as SKIPPED",
    )
    args = ap.parse_args(argv)

    data = find_data_dir()
    field_a = load_pocket(data / "7LCJ_pocket.pdb", ff="AMBER")
    field_b = None if args.skip_model_b else load_pocket(data / "7LCJ_pocket.pdb", ff="CHARMM")
    print(
        f"field A (AMBER): {field_a.provenance['n_charges']} charges, net {sum(c.q for c in field_a):+.2f}; "
        + (
            f"field B (CHARMM): {field_b.provenance['n_charges']} charges, net {sum(c.q for c in field_b):+.2f}"
            if field_b
            else "field B: skipped"
        )
    )

    parent = Candidate("parent", PARENT_SMILES)
    sites = SITES if args.sites is None else {k: SITES[k] for k in args.sites.split(",")}
    smi = analogue_smiles(sites)
    analogues = [Candidate(n, s) for n, s in smi.items()]
    selfpair = Candidate("parent-self", PARENT_SMILES)
    cands = [parent, *analogues, selfpair]
    k0 = args.skip_first_poses
    poses = make_pairing(bound_pose_in_smiles_order(data / "conf_00_cryo_em.xyz"), args.poses + k0)
    poses.run(cands, {"parent": parent})
    for c in cands:  # amendment A1: the first k0 pose indices are dropped for EVERY molecule, before any SCF
        c.poses = c.poses[k0:]

    args.out.mkdir(exist_ok=True)
    cache = args.out / "halogen-scf-cache.json"  # content-addressed, so shared by runs with different --poses
    cap_s = args.cap_cpu_hours * 3600.0
    tier_a = run_field_tier(cands, field_a, "AMBER", args.workers, cache, cap_s, args.est_scf_cpu_s)
    # Model B: the SAME poses under CHARMM charges, in separate candidates so per_pose stays model-specific.
    tier_b = parent_b = None
    cands_b: list[Candidate] = []
    if field_b is not None:
        cands_b = [Candidate(c.name, c.smiles, c.poses) for c in cands if c is not selfpair]
        tier_b = run_field_tier(cands_b, field_b, "CHARMM", args.workers, cache, cap_s, args.est_scf_cpu_s)
        parent_b = cands_b[0]

    # Criterion 1: self-anchor, exactly 0.0 on every pose, and a non-zero field.
    selfdd = paired_delta(parent, selfpair, "dE_int")
    assert all(v == 0.0 for v in selfdd.per_pose), f"self-anchor is not exactly 0.0: {selfdd.per_pose}"
    assert all(abs(v) > 1.0 for v in parent.per_pose["dE_int"]), (
        f"field looks inert: parent dE_int = {parent.per_pose['dE_int']}"
    )
    print(
        f"\n[1] self-anchor: per-pose ddE = {list(selfdd.per_pose)} (exactly 0.0); "
        f"parent dE_int per pose = {[round(v, 2) for v in parent.per_pose['dE_int']]} kcal/mol (field non-zero). "
        f"The campaign's re-embedding self-anchor was +{CAMPAIGN_SUMMARY['self_anchor']}; "
        "this construction copies, so it is 0."
    )

    paired = {c.name: paired_delta(parent, c, "dE_int", tier_a) for c in analogues}
    unpaired = {c.name: unpaired_delta(parent, c, "dE_int") for c in analogues}
    paired_b = {c.name: paired_delta(parent_b, c, "dE_int", tier_b) for c in cands_b[1:]} if tier_b else {}
    rho = {c.name: float(np.corrcoef(c.per_pose["dE_int"], parent.per_pose["dE_int"])[0, 1]) for c in analogues}

    print(f"\n[3] paired vs unpaired (AMBER, {args.poses} poses, kcal/mol)")
    print(f"{'analogue':14} {'paired ddE':>10} {'pairedSEM':>10} {'unpairedSEM':>12} {'unp/pair':>9} {'rho':>8}")
    ratios = {}
    for n, m in paired.items():
        ratios[n] = unpaired[n].sem / m.sem if m.sem > 0 else float("inf")
        print(f"{n:14} {m.mean:+10.3f} {m.sem:10.3f} {unpaired[n].sem:12.3f} {ratios[n]:9.2f} {rho[n]:8.4f}")
    worse = [n for n in paired if not paired[n].sem < unpaired[n].sem]
    print(
        "    criterion 3:",
        "PASS, paired SEM < unpaired SEM for all analogues"
        if not worse
        else f"FAIL for {worse} (recorded as the result, not tuned)",
    )

    rows, status = compare_to_campaign(paired)
    print(f"\n[2] comparison with campaign-recorded values: {status}")
    print(f"{'analogue':14} {'ours':>9} {'ours SEM':>9} {'recorded':>9} {'agrees':>7}")
    for r in rows:
        rec = "n/a" if r["recorded"] is None else f"{r['recorded']:+.3f}"
        ag = "n/a" if r["agrees"] is None else str(r["agrees"])
        print(f"{r['name']:14} {r['mean']:+9.3f} {r['sem']:9.3f} {rec:>9} {ag:>7}")

    # Criterion 2, range form (issue #6 as amended): rho and the CH3 paired SEM against the campaign's ranges.
    rho_rng = range_report(rho, *CAMPAIGN_SUMMARY["rho"])
    sem_rng = range_report({n: m.sem for n, m in paired.items() if n.endswith("-CH3")}, *CAMPAIGN_SUMMARY["CH3_sem"])
    print(
        f"    rho (analogue vs parent per-pose dE_int) against campaign {CAMPAIGN_SUMMARY['rho']}: "
        f"{rho_rng['n_inside']} inside, {rho_rng['n_outside']} outside {rho_rng['outside']}"
    )
    print(
        f"    CH3 paired SEM against campaign {CAMPAIGN_SUMMARY['CH3_sem']}: "
        f"{sem_rng['n_inside']} inside, {sem_rng['n_outside']} outside {sem_rng['outside']}"
    )

    # Criterion 4: the floor, then the cut. The declared floor is always applied; the measured AMBER->CHARMM
    # sensitivity, when that pass was run, can only raise it (cut takes the max).
    report = None
    if tier_b is None:
        print("\n[4] second charge model NOT run (--skip-model-b): sign flips and Spearman between models unmeasured.")
    else:
        va = {c.name: paired[c.name].mean for c in analogues}
        vb = {n: m.mean for n, m in paired_b.items()}
        report = charge_sensitivity(analogues, "dE_int", lambda c, q: va[c.name], lambda c, q: vb[c.name])
        print(
            f"\n[4] charge sensitivity (AMBER -> CHARMM): sign flips {report.n_sign_flips}/{report.n} "
            f"(campaign: {CAMPAIGN_SUMMARY['n_sign_flips']}/{CAMPAIGN_SUMMARY['n']}), Spearman {report.spearman:+.3f} "
            f"(campaign: {CAMPAIGN_SUMMARY['spearman']:+.3f}), measured floor {report.floor:.3f} kcal/mol"
        )
    res = cut(paired, keep=3, z=2.0, floor=args.floor, sensitivity=report)
    src = DECLARED_FLOOR_SOURCE if args.floor == DECLARED_FLOOR_KCAL_MOL else "set by --floor"
    print(f"    declared floor {args.floor} kcal/mol ({src})")
    print(f"    cut(keep=3, z=2, floor={res.floor:.3f}): groups={res.groups}")
    print("    ", "UNRANKED at the boundary" if res.unranked_at_boundary else f"survivors={res.survivors}", *res.notes)
    in_group = {n: i for i, g in enumerate(res.groups) for n in g}
    unres = unranked_pairs(paired, res.floor)
    for a, b, gap in unres:  # a gap under the floor must never be reported as a resolved order
        assert in_group[a] == in_group[b], f"cut separated {a},{b} despite gap {gap:.3f} <= floor {res.floor:.3f}"
    print(f"     {len(unres)}/{len(paired) - 1} adjacent gaps are at or below the floor; none is ranked across.")
    all_cands = [*cands, *(cands_b if tier_b else [])]
    total_cpu_s = sum(sum(c.per_pose["cpu_s"]) for c in all_cands)
    print(f"    CPU spent in the SCFs behind this table: {total_cpu_s / 3600:.2f} CPU-h")

    rec = RunRecord(
        campaign="danuglipron-halogen-scan",
        inputs={
            "parent": PARENT_SMILES,
            "analogues": smi,
            "sites": sites,
            "pocket": tier_a.field_provenance,
            "pocket_charge_model_b": tier_b.field_provenance if tier_b else None,
            "dropped_pose_indices": {
                "indices": list(range(k0)),
                "reason": "plan amendment A1 (docs/benchmarks/danuglipron_halogen_plan.md)",
            },
            "ionization": "neutral acid (the SMILES given carries no formal charge)",
            "campaign_summary": CAMPAIGN_SUMMARY,
        },
        tiers=[{"name": t.name, **t.settings()} for t in (poses, tier_a, tier_b) if t is not None],
        results={
            "criterion_1_self_anchor": {
                "per_pose": list(selfdd.per_pose),
                "exact_zero": True,
                "parent_dE_int": parent.per_pose["dE_int"],
            },
            "criterion_2_campaign_comparison": {
                "status": status,
                "rows": rows,
                "rho_range": rho_rng,
                "ch3_paired_sem_range": sem_rng,
            },
            "criterion_3_pairing": {
                n: {"paired_sem": paired[n].sem, "unpaired_sem": unpaired[n].sem, "ratio": ratios[n], "rho": rho[n]}
                for n in paired
            },
            "paired_ddE_amber": {
                n: {"mean": m.mean, "sem": m.sem, "n": m.n, "per_pose": m.per_pose} for n, m in paired.items()
            },
            "paired_ddE_charmm": {n: {"mean": m.mean, "sem": m.sem} for n, m in paired_b.items()},
            "criterion_4_sensitivity": (
                {
                    "n_sign_flips": report.n_sign_flips,
                    "spearman": report.spearman,
                    "floor": report.floor,
                    "deltas": report.deltas,
                }
                if report
                else "NOT RUN: --skip-model-b"
            ),
            "declared_floor": {"value": args.floor, "source": DECLARED_FLOOR_SOURCE},
            "cost": {
                "scf_cpu_hours": total_cpu_s / 3600,
                "n_poses": args.poses,
                "first_pose_index": k0,
                "workers": args.workers,
            },
            "cut": {
                "groups": res.groups,
                "survivors": res.survivors,
                "unranked": res.unranked_at_boundary,
                "floor": res.floor,
                "notes": res.notes,
                "adjacent_gaps_at_or_below_floor": [[a, b, g] for a, b, g in unres],
            },
        },
    )
    path = args.out / f"danuglipron-halogen-{rec.input_digest[:12]}.json"
    path.write_text(rec.to_json())
    fer = rec.ferric
    assert fer["commit"] and fer["provenance"].startswith(("VERIFIED", "INFERRED")), f"ferric commit unnamed: {fer}"
    print(f"\n[5] run record: {path}\n    ferric {fer['version']} commit {fer['commit']} ({fer['provenance']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
