"""export_run on a run written today: results.poses[name] is {"file", "sha256", "n_poses"}, not a path string."""

from __future__ import annotations

import json

import pytest
from gui_fixture import CANDIDATES, make_run_dir

from smeltery.avogadro import export_run
from smeltery.cli import pose_file_name
from smeltery.run_view import load_run, pose_sdf


def test_export_run_writes_every_candidates_pose_sdf(tmp_path):
    run = load_run(make_run_dir(tmp_path))
    dest = export_run(run, tmp_path / "avo")
    for name in CANDIDATES:
        assert (dest / pose_file_name(name)).read_text() == pose_sdf(run, name)
    assert (dest / "field.xyz").exists()
    assert not (dest / "receptor.pdb").exists()  # the fixture run has no receptor


def test_export_run_writes_the_receptor_from_the_record(tmp_path):
    import hashlib

    out = make_run_dir(tmp_path)
    (out / "receptor.pdb").write_text(
        "ATOM      1  N   GLY A   1       0.000   0.000   0.000  1.00  0.00           N\nEND\n"
    )
    rec_path = next(out.glob("run-*.json"))
    rec = json.loads(rec_path.read_text())
    rec["results"]["receptor"] = {
        "file": "receptor.pdb",
        "sha256": hashlib.sha256((out / "receptor.pdb").read_bytes()).hexdigest(),
    }
    rec_path.write_text(json.dumps(rec))
    dest = export_run(load_run(out), tmp_path / "avo")
    assert (dest / "receptor.pdb").read_text() == (out / "receptor.pdb").read_text()


def test_export_run_refuses_a_pose_file_changed_since_the_run(tmp_path):
    out = make_run_dir(tmp_path)
    (out / "poses" / pose_file_name("4-F")).write_text("tampered\n")
    with pytest.raises(ValueError, match="sha256"):
        export_run(load_run(out), tmp_path / "avo")


def test_export_run_names_files_by_the_cli_rule(tmp_path):
    """A candidate name with a non-ASCII letter is sanitised like cli.pose_file_name, not str.isalnum."""
    out = make_run_dir(tmp_path)
    rec_path = next(out.glob("run-*.json"))
    rec = json.loads(rec_path.read_text())
    rec["results"]["poses"]["café"] = rec["results"]["poses"]["4-F"]
    rec_path.write_text(json.dumps(rec))
    dest = export_run(load_run(out), tmp_path / "avo")
    assert (dest / "caf_.sdf").exists()
    assert not (dest / "café.sdf").exists()
