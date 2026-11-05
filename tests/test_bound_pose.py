"""`[bound_pose]` (`smeltery.methods.bound_pose`): the parent's poses from a bound conformer, the analogues paired to
them. The reference is the danuglipron example's own pairing as it was before the code moved
(`tests/data/bound_pose_reference.json`, made by `tests/data/make_bound_pose_reference.py` from 720e4ca)."""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tomllib
from pathlib import Path

import numpy as np
import pytest
from rdkit import Chem

from smeltery.cli import ConfigError, build_plan, load_config, validate_config
from smeltery.gui.pipeline import Pipeline
from smeltery.methods import registry
from smeltery.methods.bound_pose import bound_pose_in_smiles_order
from smeltery.record import RunRecord
from smeltery.run_inputs import stage_inputs

ROOT = Path(__file__).resolve().parent.parent
XYZ = ROOT / "src" / "smeltery" / "prebuilt" / "danuglipron-halogen" / "conf_00_cryo_em.xyz"
REF = json.loads((ROOT / "tests" / "data" / "bound_pose_reference.json").read_text())
PARENT, ANALOGUE = REF["candidates"]["parent"], REF["candidates"]["bzim-C9-F"]


def _toml(file: str, **bound) -> str:
    knobs = {"seed": 20261005, "n_poses": len(PARENT["poses"]), **bound}
    lines = "\n".join(f"{k} = {json.dumps(v)}" for k, v in knobs.items())
    return f"""
campaign = "t"
parent = "parent"

[candidates]
parent = "{PARENT["smiles"]}"
bzim-C9-F = "{ANALOGUE["smiles"]}"

[bound_pose]
file = "{file}"
{lines}

[poses]
mcs_timeout_s = 60   # the exact MCS of this pair takes about 2 s; a finished search does not depend on the limit

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def _cfg(**bound) -> dict:
    return validate_config(tomllib.loads(_toml(str(XYZ), **bound)))


def _coords(cand) -> list[np.ndarray]:
    return [p.coords_ang for p in cand.poses]


def _digest(plan) -> str:
    return RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest


def test_poses_equal_the_example_pairing_at_720e4ca():
    plan = build_plan(_cfg())
    by = {c.name: c for c in plan.candidates}
    assert plan.poses.parent_poses_given
    assert [e["stage"] for e in plan.stage_log][:2] == ["bound_pose", "poses"]
    for name, ref in REF["candidates"].items():
        got = by[name]
        assert len(got.poses) == len(ref["poses"]) == 4, name
        for i, (pose, want) in enumerate(zip(got.poses, ref["poses"], strict=True)):
            assert list(pose.symbols) == ref["symbols"], (name, i)
            assert np.array_equal(pose.coords_ang, np.array(want)), (name, i)  # bit for bit, not approximately
    # pose 0 is the bound conformer itself; the others are jitters of it
    assert not np.array_equal(by["parent"].poses[0].coords_ang, by["parent"].poses[1].coords_ang)


def test_atom_order_is_the_parent_smiles_with_h_and_pose_0_is_the_file_unaltered():
    pose = bound_pose_in_smiles_order(XYZ, PARENT["smiles"])
    pmol = Chem.AddHs(Chem.MolFromSmiles(PARENT["smiles"]))
    assert pose.symbols == tuple(a.GetSymbol() for a in pmol.GetAtoms())
    for b in pmol.GetBonds():  # every bond of the SMILES topology has a bonded-length distance
        d = np.linalg.norm(pose.coords_ang[b.GetBeginAtomIdx()] - pose.coords_ang[b.GetEndAtomIdx()])
        assert 0.9 < d < 1.9, (b.GetBeginAtomIdx(), b.GetEndAtomIdx(), d)
    rows = [ln.split() for ln in XYZ.read_text().splitlines()[2:] if ln.strip()]
    from_file = sorted((s, *map(float, xyz)) for s, *xyz in rows)
    assert sorted((s, *map(float, x)) for s, x in zip(pose.symbols, pose.coords_ang, strict=True)) == from_file


def test_skip_first_drops_leading_poses_before_pairing():
    plan = build_plan(_cfg(n_poses=3, skip_first=1))
    parent = next(c for c in plan.candidates if c.name == "parent")
    want = [np.array(p) for p in PARENT["poses"][1:]]  # built 4, dropped the bound conformer itself
    assert len(parent.poses) == 3 and all(np.array_equal(a, b) for a, b in zip(_coords(parent), want, strict=True))
    assert all(len(c.poses) == 3 for c in plan.candidates)


def test_exclusive_with_docking_and_needs_its_file_and_seed():
    cfg = _cfg()
    with pytest.raises(ConfigError, match=r"cannot be combined with \[docking\]"):
        validate_config({**cfg, "docking": {"receptor": "r.pdbqt", "box_center": [0.0, 0.0, 0.0]}})
    with pytest.raises(ConfigError, match=r"\[structure\]"):
        validate_config({**cfg, "structure": {"spec": "1ABC"}})
    for key in ("file", "seed"):
        with pytest.raises(ConfigError, match=key):
            validate_config({**cfg, "bound_pose": {k: v for k, v in cfg["bound_pose"].items() if k != key}})
    with pytest.raises(ConfigError, match="n_poses"):
        validate_config({**cfg, "bound_pose": {**cfg["bound_pose"], "n_poses": 0}})
    with pytest.raises(ConfigError, match="unknown"):
        validate_config({**cfg, "bound_pose": {**cfg["bound_pose"], "n_pose": 3}})
    assert "docking" in registry()["bound_pose"].exclusive


def test_the_file_is_staged_and_its_sha256_is_in_the_digest(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    shutil.copyfile(XYZ, src / "bound.xyz")
    text = _toml("bound.xyz")
    (src / "c.toml").write_text(text)
    out = tmp_path / "out"
    staged, inputs = stage_inputs(text, src, out)
    assert set(inputs) == {"bound_pose.file"}
    copy = inputs["bound_pose.file"]["copy"]
    assert tomllib.loads(staged)["bound_pose"]["file"] == copy and (out / copy).read_bytes() == XYZ.read_bytes()
    (out / "c.toml").write_text(staged)

    plan = build_plan(load_config(src / "c.toml"))
    sha = inputs["bound_pose.file"]["sha256"]
    assert plan.inputs["bound_pose"]["file"] == {"name": "bound.xyz", "sha256": sha}
    assert _digest(build_plan(load_config(out / "c.toml"))) == _digest(plan)  # the staged copy digests the same

    moved = src / "c2.toml"  # another seed draws other jitters and is another input
    moved.write_text(_toml("bound.xyz", seed=7))
    assert _digest(build_plan(load_config(moved))) != _digest(plan)


def test_the_pipeline_editor_takes_the_config_and_round_trips_it():
    cfg = tomllib.loads(_toml(str(XYZ)))
    p = Pipeline.from_config(cfg)
    assert "bound_pose" in p.keys()
    assert [i.message for i in p.check() if i.level == "error"] == []
    assert p.to_config() == cfg


def test_the_example_still_pairs_as_it_did():
    name = "danuglipron_halogen_for_bound_pose"
    spec = importlib.util.spec_from_file_location(name, ROOT / "examples" / "danuglipron_halogen.py")
    assert spec is not None and spec.loader is not None
    ex = importlib.util.module_from_spec(spec)
    sys.modules[name] = ex
    spec.loader.exec_module(ex)
    parent = ex.Candidate("parent", ex.PARENT_SMILES)
    ana = ex.Candidate("bzim-C9-F", ex.analogue_smiles()["bzim-C9-F"])
    pairing = ex.make_pairing(ex.bound_pose_in_smiles_order(XYZ), len(PARENT["poses"]))
    assert pairing.settings() == REF["settings"]
    pairing.run([parent, ana], {"parent": parent})
    for cand, ref in ((parent, PARENT), (ana, ANALOGUE)):
        assert all(np.array_equal(a, np.array(b)) for a, b in zip(_coords(cand), ref["poses"], strict=True))
