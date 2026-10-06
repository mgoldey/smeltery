"""Danuglipron halogen scan through the smeltery funnel: F/Cl/CH3 at 4 aromatic CH sites vs the parent.

Run:  OPENBLAS_NUM_THREADS=1 uv run python examples/danuglipron_halogen.py [--poses N] [--out DIR]

Inputs: the 7LCJ pocket (PDB, charges from pdb2pqr30, NO distance cutoff) and the
cryo-EM bound conformer of danuglipron (conf_00_cryo_em.xyz) from a ferric checkout
(env FERRIC_DANUGLIPRON_DIR overrides the search). Output: a table, assertions for
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
  * The tier scores the NEUTRAL acid (FieldInteraction has no net-charge setting
    yet); the campaign scored the carboxylate. Both are disclosed in the record.
  * Parent poses: the cryo-EM bound conformer plus seeded rigid jitters
    (15 deg / 0.5 A), mimicking docked-pose spread. Not docked, not relaxed.
  * Second charge model: pdb2pqr30 --ff=CHARMM against the default AMBER. The
    campaign's second model is not recorded. Two force fields share the same
    structure, protonation tool and point-charge approximation, so their
    disagreement is a LOWER bound on charge-model uncertainty.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
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

PARENT_SMILES = (
    "C1CO[C@@H]1CN2C3=C(C=CC(=C3)C(=O)O)N=C2CN4CCC(CC4)"
    "C5=NC(=CC=C5)OCC6=C(C=C(C=C6)C#N)F"
)
# Atom indices (heavy-atom order of PARENT_SMILES) of the four substituted aromatic CH sites.
SITES = {"bzim-C9": 9, "pyr-C28": 28, "bnz-C34": 34, "bnz-C36": 36}
SUBSTITUENTS = {"F": "F", "Cl": "Cl", "CH3": "C"}

# The campaign's recorded per-analogue paired ddE: {name: (mean, sem)} in kcal/mol.
# EMPTY ON PURPOSE: searched ferric experiments/danuglipron (RESULTS.md, PLAN.md, out/*.json),
# tools/morph, tools/pipeline, site/src/reference, validation_handoff.txt and the sibling
# checkouts; only summary statistics survive (rho 0.78-0.9997, CH3 SEM 1.0-3.5, self-anchor
# +0.31, 4/12 flips, Spearman +0.664), not per-analogue values. Do not fill this with guesses.
CAMPAIGN_RECORDED: dict[str, tuple[float, float]] = {}
CAMPAIGN_SUMMARY = {"rho": (0.78, 0.9997), "CH3_sem": (1.0, 3.5), "self_anchor": 0.31,
                    "n_sign_flips": 4, "n": 12, "spearman": 0.664}


def find_data_dir() -> Path:
    cands = [os.environ.get("FERRIC_DANUGLIPRON_DIR"),
             "/home/matt/qc/ferric/testdata/molecules/c9_systems/danuglipron",
             "/home/matt/.cache/uv/git-v0/checkouts/43eff405c5bd8de8/8637a5d5/testdata/molecules/c9_systems/danuglipron"]
    for c in cands:
        if c and (Path(c) / "7LCJ_pocket.pdb").is_file() and (Path(c) / "conf_00_cryo_em.xyz").is_file():
            return Path(c)
    raise FileNotFoundError("danuglipron test data not found; set FERRIC_DANUGLIPRON_DIR")


def analogue_smiles() -> dict[str, str]:
    """name -> SMILES: replace the aromatic CH at each site with F, Cl or CH3."""
    out = {}
    for sname, idx in SITES.items():
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
        parent_poses = [base.copy()] + [_rigid_jitter(base, rng, self.jitter_deg, self.jitter_ang)
                                        for _ in range(self.n_poses - 1)]
        parent.poses = [Pose(self.bound.symbols, x) for x in parent_poses]
        for cand in candidates:
            if cand is not parent:
                cand.poses = self._paired(pmol, parent_poses, cand)


def _one_pose(name: str, smiles: str, pose: Pose, field) -> float:
    """Worker: dE_int of one pose, through the real `FieldInteraction` tier."""
    c = Candidate(name, smiles, [pose])
    FieldInteraction().run([c], {"field": field})
    return c.per_pose["dE_int"][0]


def run_field_tier(cands: list[Candidate], field, tag: str, workers: int, cache_path: Path) -> FieldInteraction:
    """`FieldInteraction` over every pose of every candidate, one process per pose, with a resumable cache.

    Results are identical to `FieldInteraction().run(cands, ...)`; only scheduling differs. The cache key is
    the pose geometry plus the field's input digest and force field, so a stale entry cannot be reused.
    """
    cache = json.loads(cache_path.read_text()) if cache_path.is_file() else {}
    tier = FieldInteraction()
    tier.field_provenance = getattr(field, "provenance", None)
    fkey = json.dumps({k: tier.field_provenance.get(k) for k in ("input_sha256", "pdb2pqr_ff", "cutoff_ang")}, sort_keys=True)
    keys = {(c.name, i): hashlib.sha256((fkey + tag + c.name + p.to_xyz()).encode()).hexdigest()
            for c in cands for i, p in enumerate(c.poses)}
    todo = [(c, i) for c in cands for i in range(len(c.poses)) if keys[(c.name, i)] not in cache]
    print(f"  {tag}: {len(keys) - len(todo)} cached, {len(todo)} to run on {workers} workers", flush=True)
    if todo:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(_one_pose, c.name, c.smiles, c.poses[i], field): (c, i) for c, i in todo}
            for fut in as_completed(futs):
                c, i = futs[fut]
                cache[keys[(c.name, i)]] = fut.result()
                cache_path.write_text(json.dumps(cache))
                print(f"  {tag}: {c.name} pose {i} done ({len(cache)} cached)", flush=True)
    for c in cands:
        c.per_pose["dE_int"] = [cache[keys[(c.name, i)]] for i in range(len(c.poses))]
    return tier


def compare_to_campaign(paired: dict[str, Measurement]) -> tuple[list[dict], str]:
    """Criterion 2: rows (name, ours, recorded, within SEM) and a status: UNVERIFIED if nothing recorded."""
    rows, checked = [], 0
    for name, m in paired.items():
        rec = CAMPAIGN_RECORDED.get(name)
        if rec is None:
            rows.append({"name": name, "mean": m.mean, "sem": m.sem, "recorded": None, "recorded_sem": None, "agrees": None})
            continue
        checked += 1
        rows.append({"name": name, "mean": m.mean, "sem": m.sem, "recorded": rec[0], "recorded_sem": rec[1],
                     "agrees": bool(abs(m.mean - rec[0]) <= m.sem)})
    if checked == 0:
        return rows, "UNVERIFIED: no campaign per-analogue values available"
    ok = all(r["agrees"] for r in rows if r["agrees"] is not None)
    return rows, f"{'AGREES' if ok else 'DOES NOT AGREE'} for {checked}/{len(rows)} analogues with recorded values"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--poses", type=int, default=6)
    ap.add_argument("--out", type=Path, default=Path("out"))
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    args = ap.parse_args(argv)

    data = find_data_dir()
    field_a = load_pocket(data / "7LCJ_pocket.pdb", ff="AMBER")
    field_b = load_pocket(data / "7LCJ_pocket.pdb", ff="CHARMM")
    print(f"field A (AMBER): {field_a.provenance['n_charges']} charges, net {sum(c.q for c in field_a):+.2f}; "
          f"field B (CHARMM): {field_b.provenance['n_charges']} charges, net {sum(c.q for c in field_b):+.2f}")

    parent = Candidate("parent", PARENT_SMILES)
    smi = analogue_smiles()
    analogues = [Candidate(n, s) for n, s in smi.items()]
    selfpair = Candidate("parent-self", PARENT_SMILES)
    cands = [parent, *analogues, selfpair]
    poses = BoundPosePairedPoses(bound_pose_in_smiles_order(data / "conf_00_cryo_em.xyz"), n_poses=args.poses)
    poses.run(cands, {"parent": parent})

    args.out.mkdir(exist_ok=True)
    cache = args.out / f"halogen-scf-cache-{args.poses}poses.json"
    tier_a = run_field_tier(cands, field_a, "AMBER", args.workers, cache)
    # Model B: the SAME poses under CHARMM charges, in separate candidates so per_pose stays model-specific.
    cands_b = [Candidate(c.name, c.smiles, c.poses) for c in cands if c is not selfpair]
    tier_b = run_field_tier(cands_b, field_b, "CHARMM", args.workers, cache)
    parent_b = cands_b[0]

    # Criterion 1: self-anchor, exactly 0.0 on every pose, and a non-zero field.
    selfdd = paired_delta(parent, selfpair, "dE_int")
    assert all(v == 0.0 for v in selfdd.per_pose), f"self-anchor is not exactly 0.0: {selfdd.per_pose}"
    assert all(abs(v) > 1.0 for v in parent.per_pose["dE_int"]), \
        f"field looks inert: parent dE_int = {parent.per_pose['dE_int']}"
    print(f"\n[1] self-anchor: per-pose ddE = {list(selfdd.per_pose)} (exactly 0.0); "
          f"parent dE_int per pose = {[round(v, 2) for v in parent.per_pose['dE_int']]} kcal/mol (field non-zero). "
          f"The campaign's re-embedding self-anchor was +{CAMPAIGN_SUMMARY['self_anchor']}; this construction copies, so it is 0.")

    paired = {c.name: paired_delta(parent, c, "dE_int", tier_a) for c in analogues}
    unpaired = {c.name: unpaired_delta(parent, c, "dE_int") for c in analogues}
    paired_b = {c.name: paired_delta(parent_b, c, "dE_int", tier_b) for c in cands_b[1:]}
    rho = {c.name: float(np.corrcoef(c.per_pose["dE_int"], parent.per_pose["dE_int"])[0, 1]) for c in analogues}

    print(f"\n[3] paired vs unpaired (AMBER, {args.poses} poses, kcal/mol)")
    print(f"{'analogue':14} {'paired ddE':>10} {'pairedSEM':>10} {'unpairedSEM':>12} {'unp/pair':>9} {'rho':>8}")
    ratios = {}
    for n, m in paired.items():
        ratios[n] = unpaired[n].sem / m.sem if m.sem > 0 else float("inf")
        print(f"{n:14} {m.mean:+10.3f} {m.sem:10.3f} {unpaired[n].sem:12.3f} {ratios[n]:9.2f} {rho[n]:8.4f}")
    worse = [n for n in paired if not paired[n].sem < unpaired[n].sem]
    print("    criterion 3:", "PASS, paired SEM < unpaired SEM for all analogues" if not worse
          else f"FAIL for {worse} (recorded as the result, not tuned)")

    rows, status = compare_to_campaign(paired)
    print(f"\n[2] comparison with campaign-recorded values: {status}")
    print(f"{'analogue':14} {'ours':>9} {'ours SEM':>9} {'recorded':>9} {'agrees':>7}")
    for r in rows:
        rec = "n/a" if r["recorded"] is None else f"{r['recorded']:+.3f}"
        ag = "n/a" if r["agrees"] is None else str(r["agrees"])
        print(f"{r['name']:14} {r['mean']:+9.3f} {r['sem']:9.3f} {rec:>9} {ag:>7}")

    # Criterion 4: charge-sensitivity floor, then the cut.
    va = {c.name: paired[c.name].mean for c in analogues}
    vb = {n: m.mean for n, m in paired_b.items()}
    report = charge_sensitivity(analogues, "dE_int", lambda c, q: va[c.name], lambda c, q: vb[c.name])
    print(f"\n[4] charge sensitivity (AMBER -> CHARMM): sign flips {report.n_sign_flips}/{report.n} "
          f"(campaign: {CAMPAIGN_SUMMARY['n_sign_flips']}/{CAMPAIGN_SUMMARY['n']}), Spearman {report.spearman:+.3f} "
          f"(campaign: {CAMPAIGN_SUMMARY['spearman']:+.3f}), floor {report.floor:.3f} kcal/mol")
    res = cut(paired, keep=3, z=2.0, sensitivity=report)
    print(f"    cut(keep=3, z=2, floor={res.floor:.3f}): groups={res.groups}")
    print("    ", "UNRANKED at the boundary" if res.unranked_at_boundary else f"survivors={res.survivors}", *res.notes)
    order = sorted(paired, key=lambda k: paired[k].mean)
    gaps = {(a, b): paired[b].mean - paired[a].mean for a, b in pairwise(order)}
    in_group = {n: i for i, g in enumerate(res.groups) for n in g}
    for (a, b), gap in gaps.items():
        if gap <= res.floor:  # a gap under the floor must never be reported as a resolved order
            assert in_group[a] == in_group[b], f"cut separated {a},{b} despite gap {gap:.3f} <= floor {res.floor:.3f}"
    n_unres = sum(1 for g in gaps.values() if g <= res.floor)
    print(f"     {n_unres}/{len(gaps)} adjacent gaps are below the floor; none is ranked across.")

    rec = RunRecord(
        campaign="danuglipron-halogen-scan",
        inputs={"parent": PARENT_SMILES, "analogues": smi, "sites": SITES, "pocket": tier_a.field_provenance,
                "pocket_charge_model_b": tier_b.field_provenance,
                "ionization": "neutral acid (tier has no net-charge setting)", "campaign_summary": CAMPAIGN_SUMMARY},
        tiers=[{"name": t.name, **t.settings()} for t in (poses, tier_a, tier_b)],
        results={
            "criterion_1_self_anchor": {"per_pose": list(selfdd.per_pose), "exact_zero": True,
                                        "parent_dE_int": parent.per_pose["dE_int"]},
            "criterion_2_campaign_comparison": {"status": status, "rows": rows},
            "criterion_3_pairing": {n: {"paired_sem": paired[n].sem, "unpaired_sem": unpaired[n].sem,
                                        "ratio": ratios[n], "rho": rho[n]} for n in paired},
            "paired_ddE_amber": {n: {"mean": m.mean, "sem": m.sem, "n": m.n, "per_pose": m.per_pose} for n, m in paired.items()},
            "paired_ddE_charmm": {n: {"mean": m.mean, "sem": m.sem} for n, m in paired_b.items()},
            "criterion_4_sensitivity": {"n_sign_flips": report.n_sign_flips, "spearman": report.spearman,
                                        "floor": report.floor, "deltas": report.deltas},
            "cut": {"groups": res.groups, "survivors": res.survivors, "unranked": res.unranked_at_boundary,
                    "floor": res.floor, "notes": res.notes},
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
