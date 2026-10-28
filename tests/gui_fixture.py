"""A run directory built to docs/gui-contract.md, without running a tier: the shared fixture for GUI tests.

`make_run_dir(tmp_path)` writes OUT/run-<digest12>.json and OUT/poses/*.sdf for the benzoic MWE (parent plus
three analogues), with per-pose values chosen so the cut has a tie group: 4-F and 4-Cl are resolved from each
other, 4-Br and 4-Cl are not. Values are fixed numbers, not physics. Pose geometry is a seeded ETKDG embedding
in SMILES-with-H atom order, and the field is two +1 charges near the parent's carboxyl.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import numpy as np
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.funnel import cut
from smeltery.model import Measurement

CANDIDATES = {
    "benzoic": "OC(=O)c1ccccc1",
    "4-F": "OC(=O)c1ccc(F)cc1",
    "4-Cl": "OC(=O)c1ccc(Cl)cc1",
    "4-Br": "OC(=O)c1ccc(Br)cc1",
}
PARENT = "benzoic"
N_POSES = 4
#: Per-pose dE_int (kcal/mol). Parent pose noise is shared by the analogues, so pairing cancels it.
_PARENT_DE = np.array([-60.0, -58.5, -61.2, -59.4])
_SHIFT = {"benzoic": 0.0, "4-F": 1.8, "4-Cl": 5.0, "4-Br": 5.1}
_JITTER = {"benzoic": 0.0, "4-F": 0.05, "4-Cl": 0.3, "4-Br": 0.35}
FIELD = [[1.0, 2.6, 1.9, 0.0], [1.0, 2.4, -2.1, 0.0]]
Z, FLOOR, KEEP = 2.0, 0.0, 1


def _file_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", name) + ".sdf"


def _poses(smiles: str, seed: int) -> Chem.Mol:
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    ids = AllChem.EmbedMultipleConfs(mol, numConfs=N_POSES, randomSeed=seed)
    assert len(ids) == N_POSES
    return mol


def make_run_dir(root: Path, *, unranked: bool = False) -> Path:
    """Write a contract-shaped OUT directory under `root` and return it. `unranked=True` sets keep=2, which falls
    inside the {4-Cl, 4-Br} tie group, so the cut is UNRANKED."""
    out = Path(root) / "out"
    (out / "poses").mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    per_pose = {}
    for name in CANDIDATES:
        de = _PARENT_DE + _SHIFT[name] + rng.normal(0.0, _JITTER[name], N_POSES) * (name != PARENT)
        per_pose[name] = {"dE_int": [float(x) for x in de]}
    paired = {
        n: Measurement.from_samples(np.subtract(per_pose[n]["dE_int"], per_pose[PARENT]["dE_int"]))
        for n in CANDIDATES
        if n != PARENT
    }
    keep = 2 if unranked else KEEP
    res = cut(paired, keep=keep, z=Z, floor=FLOOR)
    poses = {}
    for k, (name, smi) in enumerate(CANDIDATES.items()):
        mol = _poses(smi, 1000 + k)
        path = out / "poses" / _file_name(name)
        with Chem.SDWriter(str(path)) as w:
            for i, conf in enumerate(mol.GetConformers()):
                m = Chem.Mol(mol)
                m.RemoveAllConformers()
                m.AddConformer(Chem.Conformer(conf), assignId=True)
                m.SetProp("_Name", f"{name} pose {i}")
                m.SetProp("smeltery_candidate", name)
                m.SetProp("smeltery_pose_index", str(i))
                m.SetProp("smeltery_parent", "1" if name == PARENT else "0")
                m.SetProp("dE_int", f"{per_pose[name]['dE_int'][i]:.10g}")
                if name != PARENT:
                    m.SetProp("paired_ddE", f"{paired[name].per_pose[i]:.10g}")
                w.write(m)
        poses[name] = {
            "file": f"poses/{path.name}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "n_poses": N_POSES,
        }
    n = len(CANDIDATES)
    record = {
        "campaign": "gui-fixture",
        "inputs": {
            "candidates": dict(CANDIDATES),
            "parent": PARENT,
            "cut": {"keep": keep, "z": Z},
            "floor": {"value": FLOOR, "source": "config [cut] floor (explicit; the tier's own is unmeasured)"},
            "gates": {"forcefield": False, "posebusters": False, "xtb": False},
            "field": [(c[0], tuple(c[1:])) for c in FIELD],
        },
        "tiers": [{"name": "paired_poses", "n_poses": N_POSES}, {"name": "field_interaction", "basis": "sto-3g"}],
        "results": {
            "paired_ddE": {
                k: {"mean": v.mean, "sem": v.sem, "n": v.n, "per_pose": list(v.per_pose)} for k, v in paired.items()
            },
            "unpaired_sem": {k: v.sem * 9.0 for k, v in paired.items()},
            "cut": {"groups": res.groups, "survivors": res.survivors, "unranked": res.unranked_at_boundary},
            "stages": [
                {
                    "stage": "poses",
                    "summary": f"paired poses: {N_POSES} per candidate",
                    "kind": "pair",
                    "candidates_in": n,
                    "candidates_out": n,
                },
                {
                    "stage": "pocket",
                    "summary": "2 point charge(s) from sites",
                    "kind": "field",
                    "candidates_in": n,
                    "candidates_out": n,
                },
            ],
            "funnel": [
                {"stage": "poses", "kind": "pair", "candidates_in": n, "candidates_out": n},
                {"stage": "pocket", "kind": "field", "candidates_in": n, "candidates_out": n},
                {"stage": "parity", "kind": "gate", "candidates_in": n, "candidates_out": n},
                {"stage": "field_interaction", "kind": "measure", "candidates_in": n, "candidates_out": n},
                {
                    "stage": "cut",
                    "kind": "rank",
                    "candidates_in": n - 1,
                    "candidates_out": None if res.unranked_at_boundary else len(res.survivors or []),
                    "unranked": bool(res.unranked_at_boundary),
                },
            ],
            "per_pose": per_pose,
            "units": {"dE_int": "kcal/mol"},
            "poses": poses,
            "field": FIELD,
            "receptor": None,
        },
        "smeltery_version": "0.0.0+fixture",
        "ferric": {"provenance": "UNKNOWN (fixture: no tier ran)"},
        "host": {"node": "fixture", "python": "3"},
        "started_utc": "2026-10-28T00:00:00Z",
    }
    digest = hashlib.sha256(
        json.dumps({"inputs": record["inputs"], "tiers": record["tiers"]}, sort_keys=True, default=str).encode()
    ).hexdigest()
    record["input_digest"] = digest
    (out / f"run-{digest[:12]}.json").write_text(json.dumps(record, indent=2, sort_keys=True, default=str))
    return out
