import json

from gui_fixture import make_run_dir

from smeltery.cli import main
from smeltery.record import RunRecord


def _stamped(tmp_path):
    out = make_run_dir(tmp_path)
    p = next(out.glob("run-*.json"))
    p.write_text(RunRecord.from_json(p.read_text()).to_json())  # now carries record_sha256
    return out, p


def test_verify_run_passes_then_names_an_edit_and_warns_on_old_records(tmp_path, capsys):
    out, p = _stamped(tmp_path)
    assert main(["verify-run", str(out)]) == 0
    d = json.loads(p.read_text())
    d["campaign"] = "edited"
    p.write_text(json.dumps(d))
    assert main(["verify-run", str(p)]) == 1 and "record_sha256 differs" in capsys.readouterr().out
    del d["record_sha256"]
    p.write_text(json.dumps(d))
    assert main(["verify-run", str(p)]) == 0 and "absent" in capsys.readouterr().out
    (out / "poses" / next(iter((out / "poses").iterdir())).name).write_text("x")
    assert main(["verify-run", str(p)]) == 1 and "sha256 differs" in capsys.readouterr().out


def test_inspect_is_deterministic_and_diff_run_reports_digest(tmp_path, capsys):
    out, p = _stamped(tmp_path)
    assert main(["inspect", str(out)]) == 0
    first = capsys.readouterr().out
    assert "verdict: survivors: 4-F" in first and "4-Cl: ddE=" in first and "sha256=" in first
    main(["inspect", str(p)])
    assert capsys.readouterr().out == first
    assert main(["diff-run", str(out), str(p)]) == 0
    d = json.loads(p.read_text())
    d["inputs"]["parent"] = "other"
    d["input_digest"] = "x"
    q = tmp_path / "b.json"
    q.write_text(json.dumps(d))
    assert main(["diff-run", str(out), str(q)]) == 1 and "inputs.parent differs" in capsys.readouterr().out


def test_a_partial_record_verifies(tmp_path):
    from test_run_split import SMALL

    cfg = tmp_path / "c.toml"
    cfg.write_text(SMALL)
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--out", str(out), "--only-candidate", "propanol"]) == 0
    assert main(["verify-run", str(out / "partial-propanol.json")]) == 0
