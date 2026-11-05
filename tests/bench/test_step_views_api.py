"""The reads behind the Poses, Checks and Measure views: pose maps, gate rows, one pose's SCF lines, and the scene
with its core drawn solid.

Data source: the run record and calc log written into the fixture run directory by these tests, as `smeltery run`
writes them (results.pose_maps, results.gates, results.calcs); expected values are literals set in the tests.
"""

import json

from bench.conftest import record_path

GATES = {
    "posebusters": {
        "benzoic": {"poses": [{"passed": True, "passed_checks": ["a"], "failed_checks": []}] * 4},
        "4-F": {
            "poses": [
                {"passed": True, "passed_checks": ["a"], "failed_checks": []},
                {"passed": False, "passed_checks": ["a"], "failed_checks": ["minimum_distance_to_protein"]},
                {"passed": True, "passed_checks": ["a"], "failed_checks": []},
                {"passed": True, "passed_checks": ["a"], "failed_checks": []},
            ]
        },
    },
    "strain_kcal": {"benzoic": [0.5, 0.6, 0.7, 0.8], "4-F": [1.0, 2.0, 9.5, 3.0]},
}
CALCS = [
    {"candidate": "4-F", "pose": 3, "leg": "field", "converged": True, "iterations": 11, "energy_hartree": -1.5},
    {"candidate": "4-F", "pose": 3, "leg": "vacuum", "converged": True, "iterations": 9, "energy_hartree": -1.4},
    {"candidate": "4-F", "pose": 2, "leg": "field", "converged": True, "iterations": 10, "energy_hartree": -1.6},
]


def _edit(run_dir, fn):
    p = record_path(run_dir)
    rec = json.loads(p.read_text())
    fn(rec)
    p.write_text(json.dumps(rec))


def test_gates_are_one_row_per_pose_with_failed_checks_and_strain_over_the_limit(client_for, run_dir):
    def add(rec):
        rec["results"]["gates"] = GATES
        rec["inputs"]["gates"]["max_strain_kcal"] = 5.0

    _edit(run_dir, add)
    c, _ = client_for(run_dir)
    g = c.get("/api/runs/0/gates").json()
    assert g["ran"] == ["posebusters", "strain_kcal"] and g["max_strain_kcal"] == 5.0 and g["raw"] == GATES
    by = {(r["candidate"], r["pose"]): r for r in g["rows"]}
    assert len(by) == 8
    assert by[("4-F", 1)]["posebusters"] is False and by[("4-F", 1)]["failed_checks"] == ["minimum_distance_to_protein"]
    assert by[("4-F", 1)]["ok"] is False
    assert by[("4-F", 2)]["over_strain"] is True and by[("4-F", 2)]["ok"] is False  # 9.5 > 5.0
    assert by[("4-F", 0)]["ok"] is True and by[("4-F", 0)]["over_strain"] is False
    assert by[("benzoic", 3)]["strain_kcal"] == 0.8


def test_without_a_threshold_strain_is_recorded_not_judged(client_for, run_dir):
    _edit(run_dir, lambda rec: rec["results"].update(gates={"strain_kcal": GATES["strain_kcal"]}))
    c, _ = client_for(run_dir)
    rows = c.get("/api/runs/0/gates").json()["rows"]
    assert all(r["over_strain"] is None and r["posebusters"] is None and r["ok"] for r in rows)


def test_a_pose_no_gate_judged_is_not_checked_rather_than_a_pass(client_for, run_dir):
    _edit(run_dir, lambda rec: rec["results"].update(gates={"strain_kcal": GATES["strain_kcal"]}))
    c, _ = client_for(run_dir)
    assert all(r["checked"] is False for r in c.get("/api/runs/0/gates").json()["rows"])

    def limit(rec):
        rec["inputs"]["gates"]["max_strain_kcal"] = 5.0

    _edit(run_dir, limit)
    assert all(r["checked"] is True for r in c.get("/api/runs/0/gates").json()["rows"])


def test_a_run_whose_gates_were_off_has_no_rows(client_for, run_dir):
    c, _ = client_for(run_dir)
    g = c.get("/api/runs/0/gates").json()
    assert g == {"ran": [], "max_strain_kcal": None, "rows": [], "raw": {}}


def test_calcs_returns_only_the_requested_poses_lines(client_for, run_dir):
    (run_dir / "calcs.jsonl").write_text("".join(json.dumps(x) + "\n" for x in CALCS))
    c, _ = client_for(run_dir)
    r = c.get("/api/runs/0/calcs", params={"candidate": "4-F", "pose": 3}).json()
    assert r["file"] == "calcs.jsonl" and r["lines"] == CALCS[:2]
    assert c.get("/api/runs/0/calcs", params={"candidate": "4-F", "pose": 0}).json()["lines"] == []


def test_calcs_is_409_when_the_run_kept_no_log(client_for, run_dir):
    _edit(run_dir, lambda rec: rec["results"].update(calcs=None))
    c, _ = client_for(run_dir)
    r = c.get("/api/runs/0/calcs", params={"candidate": "4-F", "pose": 0})
    assert r.status_code == 409 and "no SCF log" in r.json()["detail"]


def test_calcs_will_not_read_outside_the_run_directory(client_for, run_dir):
    _edit(run_dir, lambda rec: rec["results"].update(calcs={"file": "../secret.jsonl", "n": 0}))
    c, _ = client_for(run_dir)
    assert c.get("/api/runs/0/calcs", params={"candidate": "4-F", "pose": 0}).status_code == 409


def test_pose_maps_are_the_records(client_for, run_dir):
    rec = json.loads(record_path(run_dir).read_text())["results"]["pose_maps"]
    c, _ = client_for(run_dir)
    assert c.get("/api/runs/0/pose_maps").json() == {"maps": rec}
    assert set(rec) == {"4-F", "4-Cl", "4-Br"} and rec["4-F"][0] == [0, 0]


def _structure_components(scene):
    def find(n):
        if n.get("kind") == "structure" and n.get("ref") == "candidate":
            return n
        for ch in n.get("children", []):
            f = find(ch)
            if f:
                return f

    return [x for x in find(scene["root"])["children"] if x["kind"] == "component"]


def test_core_draws_the_mapped_atoms_solid_and_the_rest_as_lines(client_for, run_dir):
    c, _ = client_for(run_dir)
    maps = c.get("/api/runs/0/pose_maps").json()["maps"]["4-F"]
    plain = c.get("/api/runs/0/scene", params={"candidate": "4-F", "pose": 1}).json()
    scene = c.get("/api/runs/0/scene", params={"candidate": "4-F", "pose": 1, "core": True}).json()
    (one,) = _structure_components(plain)
    assert one["params"]["selector"] == "all"
    solid, rest = _structure_components(scene)
    assert solid["params"]["selector"] == [{"atom_index": a} for a in sorted({m[0] for m in maps})]
    assert solid["children"][0]["params"]["type"] == "ball_and_stick"
    assert rest["params"]["selector"] == "all" and rest["children"][0]["params"] == {
        "type": "ball_and_stick",
        "size_factor": 0.35,
    }
    assert [x["kind"] for x in rest["children"]] == ["representation"]


def test_the_core_scene_validates_against_molviewspecs_schema(client_for, run_dir):
    """Data source: molviewspec's own State model and its RepresentationTypeT, which has no "line"."""
    from typing import get_args

    import pytest
    from molviewspec.nodes import RepresentationTypeT, validate_state_tree

    from smeltery.bench.api.runs import _check_representations

    c, _ = client_for(run_dir)
    scene = c.get("/api/runs/0/scene", params={"candidate": "4-F", "pose": 1, "core": True}).json()
    validate_state_tree(json.dumps(scene))
    _check_representations(scene["root"])
    types = {
        r["params"]["type"]
        for comp in _structure_components(scene)
        for r in comp["children"]
        if r["kind"] == "representation"
    }
    assert types == {"ball_and_stick"} <= set(get_args(RepresentationTypeT))
    (_, rest) = _structure_components(scene)
    rest["children"][0]["params"]["type"] = "line"
    with pytest.raises(ValueError, match="'line'"):
        _check_representations(scene["root"])


def test_core_on_the_parent_changes_nothing(client_for, run_dir):
    c, _ = client_for(run_dir)
    q = {"candidate": "benzoic", "pose": 0}
    plain = c.get("/api/runs/0/scene", params=q).json()
    core = c.get("/api/runs/0/scene", params={**q, "core": True}).json()
    assert plain["root"] == core["root"]  # only the build timestamp differs
