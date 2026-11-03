"""smeltery.gui.scenes: MolViewSpec states for a pose, built from the fixture run."""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
from pathlib import Path

import pytest
from gui_fixture import FIELD, make_run_dir

from smeltery.gui import load_run, pose_scene

pytest.importorskip("molviewspec")

PEP5 = Path(__file__).parent / "data" / "pocket_pep5.pdb"


def _nodes(node, kind=None):
    if kind is None or node["kind"] == kind:
        yield node
    for child in node.get("children") or []:
        yield from _nodes(child, kind)


def _decode(url: str) -> str:
    assert url.startswith("data:text/plain;base64,")
    return base64.b64decode(url.split(",", 1)[1]).decode()


@pytest.fixture
def run(tmp_path):
    return load_run(make_run_dir(tmp_path))


def test_scene_is_json_and_has_ligand_and_field(run):
    scene = pose_scene(run, "4-F", 2)
    assert json.loads(json.dumps(scene)) == scene
    root = scene["root"]
    downloads = list(_nodes(root, "download"))
    assert len(downloads) == 2  # candidate and parent; no receptor in the fixture
    texts = [_decode(d["params"]["url"]) for d in downloads]
    assert any("4-F pose 2" in t for t in texts) and any("benzoic pose 2" in t for t in texts)
    structures = list(_nodes(root, "structure"))
    assert {s.get("ref") for s in structures} == {"candidate", "parent"}
    assert all(s["params"]["block_index"] == 2 for s in structures)
    spheres = [p for p in _nodes(root, "primitive") if p["params"]["kind"] == "ellipsoid"]
    assert len(spheres) == len(FIELD)
    assert all(p["params"]["color"] == "#2166ac" for p in spheres)  # both fixture charges are +1
    assert [list(p["params"]["center"]) for p in spheres] == [c[1:] for c in FIELD]
    assert list(_nodes(root, "focus"))


def test_scene_parent_is_muted_and_optional(run):
    scene = pose_scene(run, "4-Cl", 0)
    parent = next(s for s in _nodes(scene["root"], "structure") if s.get("ref") == "parent")
    assert list(_nodes(parent, "opacity"))
    alone = pose_scene(run, "4-Cl", 0, with_parent=False)
    assert {s.get("ref") for s in _nodes(alone["root"], "structure")} == {"candidate"}
    assert len(list(_nodes(pose_scene(run, "benzoic", 0)["root"], "structure"))) == 1


def test_scene_data_url_hook(run):
    scene = pose_scene(run, "4-F", 1, data_url=lambda p: f"/files/{p}")
    urls = sorted(d["params"]["url"] for d in _nodes(scene["root"], "download"))
    assert urls == ["/files/poses/4-F.sdf", "/files/poses/benzoic.sdf"]


def test_scene_refuses_changed_pose_file(run):
    path = run.out_dir / "poses" / "4-F.sdf"
    path.write_text(path.read_text() + "\n")
    with pytest.raises(ValueError, match=r"4-F\.sdf"):
        pose_scene(run, "4-F", 0)
    with pytest.raises(IndexError):
        pose_scene(run, "4-Cl", 99)


def test_scene_with_receptor(run):
    dst = run.out_dir / "structure" / "receptor.pdb"
    dst.parent.mkdir()
    shutil.copy(PEP5, dst)
    run.record["results"]["receptor"] = {
        "file": "structure/receptor.pdb",
        "sha256": hashlib.sha256(dst.read_bytes()).hexdigest(),
    }
    scene = pose_scene(run, "4-F", 0)
    rec = next(s for s in _nodes(scene["root"], "download") if "ATOM" in _decode(s["params"]["url"]))
    reps = [r["params"]["type"] for r in _nodes(rec, "representation")]
    assert "cartoon" in reps
    # the peptide and the ligand embedding both sit near the origin, so some residues are within 5 A
    assert "ball_and_stick" in reps
    json.dumps(scene)
    dst.write_text(dst.read_text().replace("SER", "THR", 1))
    with pytest.raises(ValueError, match="receptor"):
        pose_scene(run, "4-F", 0)
