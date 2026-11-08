import hashlib
import io
import json
import re
import zipfile

import pytest
from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app
from smeltery.bench.pymol_export import DROPPED_COLOR, GROUP_COLORS, PARENT_COLOR, candidate_colors, pymol_session

NAMES = ["parent", "a", "b", "c", "d"]
GROUPS = [["a", "b"], ["c"], ["d"]]


def test_colours_follow_status_parent_distinct_survivors_by_group_dropped_grey():
    got = candidate_colors(NAMES, "parent", GROUPS, ["a", "b", "c"])
    assert got == {
        "parent": PARENT_COLOR,
        "a": GROUP_COLORS[0],
        "b": GROUP_COLORS[0],  # a tie group shares a colour
        "c": GROUP_COLORS[1],
        "d": DROPPED_COLOR,  # in a group but cut
    }
    assert PARENT_COLOR not in GROUP_COLORS and DROPPED_COLOR not in GROUP_COLORS


def test_an_unranked_cut_colours_every_analogue_by_its_group_and_says_so():
    assert candidate_colors(NAMES, "parent", GROUPS, None)["d"] == GROUP_COLORS[2]
    assert "UNRANKED" in pymol_session(NAMES, "parent", GROUPS, None, True)
    assert "UNRANKED" not in pymol_session(NAMES, "parent", GROUPS, ["a", "b", "c"], False)


def test_session_loads_relative_paths_colours_ligand_carbons_and_shows_the_pocket():
    pml = pymol_session(["parent", "4-Cl"], "parent", [["4-Cl"]], ["4-Cl"], False)
    lines = pml.splitlines()
    assert "load receptor.pdb, receptor" in lines
    assert "load ligands/parent.sdf, lig_parent" in lines and "load ligands/4_Cl.sdf, lig_4_Cl" in lines
    assert not re.search(r"load\s+/", pml)  # nothing absolute
    assert f"color {PARENT_COLOR}, lig_parent and elem C" in lines
    assert f"color {GROUP_COLORS[0]}, lig_4_Cl and elem C" in lines
    assert "show cartoon, receptor" in lines and "select pocket, byres (receptor within 5 of ligands)" in lines
    assert lines[0].startswith("#") and any("Legend" in x for x in lines[:3]) and "parent parent" in pml


def test_names_that_collide_after_sanitising_are_refused():
    with pytest.raises(ValueError, match="one PyMOL object name"):
        pymol_session(["4-Cl", "4_Cl"], "4-Cl", [["4_Cl"]], ["4_Cl"], False)


# ----------------------------------------------------------------------------------------------------- the route


def client(run_dir):
    return TestClient(
        create_app(
            run_store=FakeRunStore(run_dir), task_queue=FakeTaskQueue(), identity=FakeIdentity("mg"), clock=FixedClock()
        )
    )


def give_receptor(
    run_dir, text="ATOM      1  N   ALA A   1       0.000   0.000   0.000  1.00  0.00           N\nEND\n"
):
    from smeltery.record import _canonical_sha256

    (run_dir / "structure").mkdir()
    pdb = run_dir / "structure" / "receptor.pdb"
    pdb.write_text(text)
    path = next(run_dir.glob("run-*.json"))
    rec = json.loads(path.read_text())
    rec["results"]["receptor"] = {"file": "structure/receptor.pdb", "sha256": hashlib.sha256(text.encode()).hexdigest()}
    rec["record_sha256"] = _canonical_sha256(rec)
    path.write_text(json.dumps(rec, indent=2, sort_keys=True))
    return pdb


def test_the_zip_holds_the_receptor_every_candidates_own_pose_file_and_a_session(tmp_path):
    from gui_fixture import CANDIDATES, PARENT, _file_name, make_run_dir

    run_dir = make_run_dir(tmp_path)
    pdb = give_receptor(run_dir)
    r = client(run_dir).get("/api/runs/0/pymol.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert "attachment" in r.headers["content-disposition"] and ".zip" in r.headers["content-disposition"]
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = set(z.namelist())
    assert {"session.pml", "receptor.pdb"} <= names and len(names) == 2 + len(CANDIDATES)
    assert z.read("receptor.pdb") == pdb.read_bytes()
    for cand in CANDIDATES:  # as-is: byte-identical to the pose file the run wrote
        written = (run_dir / "poses" / _file_name(cand)).read_bytes()
        assert z.read(f"ligands/{cand.replace('-', '_')}.sdf") == written
    pml = z.read("session.pml").decode()
    assert f"color {PARENT_COLOR}, lig_{PARENT.replace('-', '_')} and elem C" in pml
    for name in names - {"session.pml"}:  # every load line points at a file inside the zip
        assert name in pml


def test_a_run_without_a_receptor_is_409_with_a_clear_detail(tmp_path):
    from gui_fixture import make_run_dir

    r = client(make_run_dir(tmp_path)).get("/api/runs/0/pymol.zip")
    assert r.status_code == 409 and "no receptor" in r.json()["detail"]


def test_a_changed_receptor_file_is_409_not_a_silently_wrong_scene(tmp_path):
    from gui_fixture import make_run_dir

    run_dir = make_run_dir(tmp_path)
    give_receptor(run_dir).write_text("tampered\n")
    r = client(run_dir).get("/api/runs/0/pymol.zip")
    assert r.status_code == 409 and "sha256" in r.json()["detail"]


def test_a_record_that_predates_pose_output_is_409(tmp_path):
    from gui_fixture import make_run_dir

    run_dir = make_run_dir(tmp_path)
    path = next(run_dir.glob("run-*.json"))
    rec = json.loads(path.read_text())
    del rec["results"]["poses"]
    path.write_text(json.dumps(rec))
    r = client(run_dir).get("/api/runs/0/pymol.zip")
    assert r.status_code == 409 and "predates pose output" in r.json()["detail"]
