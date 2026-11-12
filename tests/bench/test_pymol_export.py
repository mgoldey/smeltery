import hashlib
import io
import json
import re
import zipfile

from fastapi.testclient import TestClient

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.app import create_app
from smeltery.bench.pymol_export import (
    DROPPED_COLOR,
    GROUP_COLORS,
    PARENT_COLOR,
    candidate_colors,
    ligand_stems,
    pymol_session,
)

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


def test_names_that_clash_after_sanitising_or_by_case_get_numbered_suffixes():
    stems = ligand_stems(["4-Cl", "4_Cl", "4_cl", "x y", "x/y"])
    assert stems == {"4-Cl": "4_Cl", "4_Cl": "4_Cl_2", "4_cl": "4_cl_3", "x y": "x_y", "x/y": "x_y_2"}
    assert len({v.lower() for v in stems.values()}) == 5


def test_control_characters_in_names_cannot_start_a_command_in_a_comment():
    evil = "a\nquit"
    pml = pymol_session(["parent", evil], "parent", [[evil]], [evil], False)
    assert all(line.startswith("#") or line.split(" ")[0] != "quit" for line in pml.splitlines())
    assert "quit" not in [line.strip() for line in pml.splitlines()]


def test_session_changes_directory_to_the_script_folder_before_loading():
    lines = pymol_session(["parent"], "parent", [], [], False).splitlines()
    cd = next(i for i, x in enumerate(lines) if "cmd.cd(" in x and "__script__" in x)
    assert lines.index("reinitialize") < cd < lines.index("load receptor.pdb, receptor")
    assert lines[cd - 2] == "python" and lines[cd + 1] == "python end"


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
    assert r.status_code == 409 and "no receptor" in r.json()["reason"]


def test_a_changed_receptor_file_is_409_not_a_silently_wrong_scene(tmp_path):
    from gui_fixture import make_run_dir

    run_dir = make_run_dir(tmp_path)
    give_receptor(run_dir).write_text("tampered\n")
    r = client(run_dir).get("/api/runs/0/pymol.zip")
    assert r.status_code == 409 and "sha256" in r.json()["reason"]


def test_a_record_that_predates_pose_output_is_409(tmp_path):
    from gui_fixture import make_run_dir

    run_dir = make_run_dir(tmp_path)
    path = next(run_dir.glob("run-*.json"))
    rec = json.loads(path.read_text())
    del rec["results"]["poses"]
    path.write_text(json.dumps(rec))
    r = client(run_dir).get("/api/runs/0/pymol.zip")
    assert r.status_code == 409 and "predates pose output" in r.json()["reason"]


def test_awkward_candidate_names_give_flat_unique_zip_entries_and_matching_load_lines(tmp_path):
    from gui_fixture import make_run_dir

    from smeltery.record import _canonical_sha256

    run_dir = make_run_dir(tmp_path)
    path = next(run_dir.glob("run-*.json"))
    rec = json.loads(path.read_text())
    awkward = ["a b", "a/b", "it's", 'q"uote', "x+y", "caf\u00e9", "Case", "case"]
    old = list(rec["results"]["poses"])
    template = rec["results"]["poses"][old[1]]
    for n in awkward:
        rec["results"]["poses"][n] = dict(template)
        rec["inputs"]["candidates"][n] = rec["inputs"]["candidates"][old[1]]
    rec["results"]["candidate_order"] += awkward
    rec["record_sha256"] = _canonical_sha256(rec)
    path.write_text(json.dumps(rec, indent=2, sort_keys=True))
    give_receptor(run_dir)
    r = client(run_dir).get("/api/runs/0/pymol.zip")
    assert r.status_code == 200
    z = zipfile.ZipFile(io.BytesIO(r.content))
    sdfs = [n for n in z.namelist() if n.endswith(".sdf")]
    assert len(sdfs) == len(old) + len(awkward)
    assert all(re.fullmatch(r"ligands/[A-Za-z0-9_-]+\.sdf", n) for n in sdfs)
    assert len({n.lower() for n in sdfs}) == len(sdfs)  # unique even on a case-folding filesystem
    pml = z.read("session.pml").decode()
    loads = [ln.split()[1].rstrip(",") for ln in pml.splitlines() if ln.startswith("load ligands/")]
    assert sorted(loads) == sorted(sdfs)


def test_a_record_without_a_cut_is_409_not_500(tmp_path):
    from gui_fixture import make_run_dir

    run_dir = make_run_dir(tmp_path)
    give_receptor(run_dir)
    path = next(run_dir.glob("run-*.json"))
    rec = json.loads(path.read_text())
    del rec["results"]["cut"]
    path.write_text(json.dumps(rec))
    c = client(run_dir)
    assert c.get("/api/runs/0/pymol.zip").status_code == 409
