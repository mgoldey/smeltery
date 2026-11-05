"""A record with a pairwise pose drop (`smeltery run --temporal`): every reader maps through `results.pose_index`.

The record is the fixture run rewritten with the exact shape `temporal_run.finalize_sync` writes: the parent's pose 0
failed every rung (every candidate loses pose 0), and 4-Br's pose 2 failed its vacuum leg (4-Br alone loses it). So
the parent keeps poses [1, 2, 3] and 4-Br keeps [1, 3]: 4-Br's record 1 is pose 3, paired with the parent's record 2.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from gui_fixture import PARENT
from rdkit import Chem

from bench.conftest import record_path
from smeltery.gui import results as R

DROPPED = "4-Br"
KEPT = {PARENT: [1, 2, 3], "4-F": [1, 2, 3], "4-Cl": [1, 2, 3], DROPPED: [1, 3]}


def _energy(name: str, pose: int, leg: str) -> float:
    """A distinct energy per (candidate, original pose, leg), so a line read for the wrong pose is caught."""
    return -100.0 - 10.0 * list(KEPT).index(name) - pose - (0.5 if leg == "field" else 0.0)


@pytest.fixture
def dropped_run(run_dir):
    p = record_path(run_dir)
    rec = json.loads(p.read_text())
    res = rec["results"]
    full = {n: list(v["dE_int"]) for n, v in res["per_pose"].items()}
    for name, keep in KEPT.items():
        res["per_pose"][name]["dE_int"] = [full[name][i] for i in keep]
        sdf = run_dir / res["poses"][name]["file"]
        mols = [m for m in Chem.SDMolSupplier(str(sdf), removeHs=False, sanitize=False)]
        with Chem.SDWriter(str(sdf)) as w:
            for i in keep:
                w.write(mols[i])
        res["poses"][name].update(n_poses=len(keep), sha256=hashlib.sha256(sdf.read_bytes()).hexdigest())
    res["pose_index"] = KEPT
    res["dropped_poses"] = [
        {
            "candidate": PARENT,
            "pose": 0,
            "leg": "field",
            "field_model": "primary",
            "reason": "SCF did not converge",
            "outcome": "not_converged",
            "rungs_tried": [0, 1],
            "dropped_with": list(KEPT),
        },
        {
            "candidate": DROPPED,
            "pose": 2,
            "leg": "vacuum",
            "field_model": None,
            "reason": "SCF did not converge",
            "outcome": "not_converged",
            "rungs_tried": [0, 1],
            "dropped_with": [PARENT, DROPPED],
        },
    ]
    lines = [
        {
            "candidate": name,
            "pose": i,
            "leg": leg,
            "field_model": None if leg == "vacuum" else "primary",
            "rung": 0,
            "converged": True,
            "energy_hartree": _energy(name, i, leg),
            "digest": rec["input_digest"][:12],
        }
        for name in KEPT
        for i in range(4)
        for leg in ("vacuum", "field")
    ]
    (run_dir / "calcs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))
    res["calcs"] = {"file": "calcs.jsonl", "n": len(lines), "primary": "primary", "by_model": {}}
    p.write_text(json.dumps(rec, indent=2, sort_keys=True))
    return run_dir, full


def test_an_analogue_only_drop_pairs_each_pose_with_the_parents_same_pose(client_for, dropped_run):
    out, full = dropped_run
    client, _ = client_for(out)
    got = client.get("/api/runs/0/per_pose")
    assert got.status_code == 200, got.text
    rows = [r for r in got.json() if r["candidate"] == DROPPED]
    assert [(r["pose"], r["position"], r["parent_position"]) for r in rows] == [(1, 0, 0), (3, 1, 2)]
    assert [r["value"] for r in rows] == [full[DROPPED][1], full[DROPPED][3]]
    assert [r["parent_value"] for r in rows] == [full[PARENT][1], full[PARENT][3]]
    others = [r for r in got.json() if r["candidate"] == "4-F"]
    assert [r["pose"] for r in others] == [1, 2, 3]
    # the data layer says the same
    table = R.per_pose_table(R.load_run(out))
    assert [(t["pose"], t["parent_value"]) for t in table if t["candidate"] == DROPPED] == [
        (1, full[PARENT][1]),
        (3, full[PARENT][3]),
    ]


def test_the_scf_log_of_a_kept_pose_is_its_original_poses(client_for, dropped_run):
    out, _ = dropped_run
    client, _ = client_for(out)
    got = client.get("/api/runs/0/calcs", params={"candidate": DROPPED, "pose": 1})
    assert got.status_code == 200, got.text
    j = got.json()
    assert j["pose"] == 3
    assert {(x["pose"], x["leg"], x["energy_hartree"]) for x in j["lines"]} == {
        (3, "vacuum", _energy(DROPPED, 3, "vacuum")),
        (3, "field", _energy(DROPPED, 3, "field")),
    }
    parent = client.get("/api/runs/0/calcs", params={"candidate": PARENT, "pose": 0}).json()
    assert parent["pose"] == 1 and {x["pose"] for x in parent["lines"]} == {1}
    assert client.get("/api/runs/0/calcs", params={"candidate": DROPPED, "pose": 2}).status_code == 400


def test_the_drops_are_in_the_api_with_reason_and_rungs(client_for, dropped_run):
    out, _ = dropped_run
    client, _ = client_for(out)
    j = client.get("/api/runs/0/dropped_poses").json()
    assert j["pose_index"] == KEPT
    by = {(d["candidate"], d["pose"]): d for d in j["dropped"]}
    assert set(by) == {(PARENT, 0), (DROPPED, 2)}
    assert by[(DROPPED, 2)]["leg"] == "vacuum" and by[(DROPPED, 2)]["field_model"] is None
    assert by[(DROPPED, 2)]["rungs_tried"] == [0, 1] and by[(DROPPED, 2)]["reason"] == "SCF did not converge"


def test_a_run_without_drops_has_none(client_for, run_dir):
    client, _ = client_for(run_dir)
    assert client.get("/api/runs/0/dropped_poses").json() == {"pose_index": {}, "dropped": []}


def test_the_per_pose_figures_place_each_point_at_its_original_pose(client_for, dropped_run):
    out, full = dropped_run
    client, _ = client_for(out)
    fig = client.get("/api/runs/0/figures/per_pose_values")
    assert fig.status_code == 200, fig.text
    traces = {t["name"]: t for t in fig.json()["data"]}
    br = traces[DROPPED]
    assert br["x"] == [1, 3] and br["y"] == [full[DROPPED][1], full[DROPPED][3]]
    assert [c[1] for c in br["customdata"]] == [1, 3] and [c[4] for c in br["customdata"]] == [0, 1]
    assert traces[f"{PARENT} (parent)"]["x"] == [1, 2, 3]
    assert client.get("/api/runs/0/figures/per_pose").status_code == 200


def test_the_scene_shows_the_parents_paired_pose(client_for, dropped_run):
    out, _ = dropped_run
    client, _ = client_for(out)
    scene = client.get("/api/runs/0/scene", params={"candidate": DROPPED, "pose": 1})
    assert scene.status_code == 200, scene.text

    def block(node, ref):
        if node.get("kind") == "structure" and node.get("ref") == ref:
            return node["params"].get("block_index")
        for ch in node.get("children", []):
            found = block(ch, ref)
            if found is not None:
                return found
        return None

    root = scene.json()["root"]
    assert block(root, "candidate") == 1 and block(root, "parent") == 2


def test_lineage_lists_the_original_poses(client_for, dropped_run):
    out, full = dropped_run
    client, _ = client_for(out)
    j = client.get("/api/runs/0/lineage", params={"candidate": DROPPED})
    assert j.status_code == 200, j.text
    assert [(p["pose"], p["parent_value"]) for p in j.json()["poses"]] == [(1, full[PARENT][1]), (3, full[PARENT][3])]
