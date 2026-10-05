"""Minimal working example: benzoic acid and two para analogues in a point-charge pocket.

Run:  OPENBLAS_NUM_THREADS=1 uv run python examples/mwe_benzoic.py

What it shows, end to end:
  1. paired pose ensembles (analogue pose i built on parent pose i)
  2. a ferric tier producing a DIFFERENCE quantity, ΔE_int = E(field) − E(vacuum)
  3. paired ΔΔE vs the parent with SEM, next to the unpaired SEM it improves on
  4. a cut that refuses to order what it can't resolve
  5. a run record tying the numbers to inputs, settings and the ferric source
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from smeltery import Candidate, PointCharge, RunRecord, cut, paired_delta, unpaired_delta
from smeltery.tiers import FieldInteraction, PairedPoses

CANDIDATES = {
    "benzoic": "OC(=O)c1ccccc1",
    "4-F": "OC(=O)c1ccc(F)cc1",
    "4-Cl": "OC(=O)c1ccc(Cl)cc1",
}


def pocket_from(parent: Candidate) -> list[PointCharge]:
    """Two +1 charges 3 Å beyond each carboxyl oxygen of parent pose 0 (an Arg-like clamp)."""
    pose = parent.poses[0]
    sym, xyz = pose.symbols, pose.coords_ang
    o_idx = [i for i, s in enumerate(sym) if s == "O"]
    c_idx = next(i for i, s in enumerate(sym) if s == "C" and
                 sum(np.linalg.norm(xyz[i] - xyz[o]) < 1.5 for o in o_idx) == 2)
    out = []
    for o in o_idx:
        u = xyz[o] - xyz[c_idx]
        u /= np.linalg.norm(u)
        out.append(PointCharge(1.0, tuple(float(v) for v in xyz[o] + 3.0 * u)))
    return out


def main(out_dir: Path = Path("out")) -> int:
    cands = [Candidate(n, s) for n, s in CANDIDATES.items()]
    parent = cands[0]
    poses = PairedPoses()
    poses.run(cands, {"parent": parent})
    field = pocket_from(parent)
    tier = FieldInteraction()
    tier.run(cands, {"field": field})

    paired = {c.name: paired_delta(parent, c, "dE_int") for c in cands}
    unpaired = {c.name: unpaired_delta(parent, c, "dE_int") for c in cands[1:]}

    print(f"{'candidate':10} {'formula':10} {'paired ddE':>22} {'unpaired SEM':>13}")
    for c in cands:
        m = paired[c.name]
        u = f"{unpaired[c.name].sem:.4f}" if c.name in unpaired else "-"
        print(f"{c.name:10} {c.formula:10} {m.mean:+10.4f} ± {m.sem:.4f} kcal/mol {u:>13}")

    res = cut({k: v for k, v in paired.items() if k != parent.name}, keep=1, z=2.0)
    print("\ncut (keep=1, z=2):", "UNRANKED at the boundary" if res.unranked_at_boundary
          else f"survivors={res.survivors}", "| groups:", res.groups, *res.notes)

    rec = RunRecord(
        campaign="mwe-benzoic",
        inputs={"candidates": CANDIDATES, "field": [(c.q, c.xyz_ang) for c in field], "parent": parent.name},
        tiers=[{"name": t.name, **t.settings()} for t in (poses, tier)],
        results={
            "paired_ddE": {k: {"mean": v.mean, "sem": v.sem, "n": v.n, "per_pose": v.per_pose} for k, v in paired.items()},
            "unpaired_sem": {k: v.sem for k, v in unpaired.items()},
            "cut": {"groups": res.groups, "survivors": res.survivors, "unranked": res.unranked_at_boundary},
        },
    )
    out_dir.mkdir(exist_ok=True)
    path = out_dir / f"run-{rec.input_digest[:12]}.json"
    path.write_text(rec.to_json())
    fer = rec.ferric
    print(f"\nrun record: {path}  (ferric {fer['commit']}, {fer['provenance']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
