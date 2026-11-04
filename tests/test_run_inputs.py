"""A run directory carries its config and every input file it read (Task 1.3)."""

import hashlib
import json
import pathlib
import shutil
import tomllib

import pytest

from smeltery.cli import main
from smeltery.run_inputs import INPUT_PATHS, stage_inputs

ROOT = pathlib.Path(__file__).resolve().parent.parent
DECISIONS = '{"campaign": "t", "decisions": []}\n'

CFG = """# keep me
campaign = "t"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"

[pocket]
file = "{pocket}"  # the pocket

{structure}
[cut]
keep = 1
floor = 0.0
"""


def _write(tmp_path, structure=""):
    """A project folder: config `c.toml` beside `data/p.pqr`, a real (tiny) pocket so `--plan` can build."""
    src = tmp_path / "proj"
    (src / "data").mkdir(parents=True)
    shutil.copyfile(ROOT / "tests" / "data" / "gly3.pqr", src / "data" / "p.pqr")
    cfg = src / "c.toml"
    cfg.write_text(CFG.format(pocket="data/p.pqr", structure=structure))
    return src, cfg


def test_the_input_paths_are_the_four_the_run_reads():
    assert INPUT_PATHS == (
        ("structure", "spec"),
        ("pocket", "file"),
        ("docking", "receptor"),
        ("decisions", "file"),
    )


def test_a_pdb_id_is_left_alone_and_comments_survive_staging(tmp_path):
    src, cfg = _write(tmp_path, '[structure]\nspec = "5R83"\n')
    text, staged = stage_inputs(cfg.read_text(), src, tmp_path / "out")
    assert tomllib.loads(text)["structure"]["spec"] == "5R83"
    assert "structure.spec" not in staged
    assert "# keep me" in text and "# the pocket" in text
    assert tomllib.loads(text)["pocket"]["file"].startswith("inputs/")


def test_a_relative_path_resolves_against_the_config_dir_not_the_cwd(tmp_path, monkeypatch):
    src, cfg = _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    _, staged = stage_inputs(cfg.read_text(), src, tmp_path / "out")
    assert list(staged) == ["pocket.file"]
    assert (tmp_path / "out" / staged["pocket.file"]["copy"]).read_bytes() == (src / "data" / "p.pqr").read_bytes()


def test_the_record_names_each_input_by_sha256(tmp_path):
    src, cfg = _write(tmp_path)
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--plan", "--out", str(out)]) == 0
    staged = (out / "config.toml").read_text()
    info = stage_inputs(staged, out, out)[1]  # staging a staged config is a no-op that still names its inputs
    want = hashlib.sha256((src / "data" / "p.pqr").read_bytes()).hexdigest()
    assert info["pocket.file"]["sha256"] == want
    assert info["pocket.file"]["copy"] == f"inputs/{want}.pqr"


def test_the_run_record_holds_the_config_entry(tmp_path):
    from smeltery.cli import _stage_config

    src, cfg = _write(tmp_path)
    out = tmp_path / "out"
    dest, entry = _stage_config(cfg, out)
    want = hashlib.sha256(dest.read_bytes()).hexdigest()
    assert entry["file"] == "config.toml" and entry["sha256"] == want
    assert entry["inputs"]["pocket.file"]["source"] == "data/p.pqr"
    json.dumps(entry)


def test_without_out_nothing_is_staged(tmp_path, monkeypatch):
    src, cfg = _write(tmp_path)
    monkeypatch.chdir(tmp_path)
    assert main(["run", str(cfg), "--plan"]) == 0
    assert not (tmp_path / "out").exists()


def test_a_run_directory_reruns_after_the_original_inputs_move(tmp_path):
    src, cfg = _write(tmp_path)
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--plan", "--out", str(out)]) == 0
    staged = (out / "config.toml").read_text()
    assert "data/p.pqr" not in staged and "inputs/" in staged
    shutil.move(str(src), str(tmp_path / "gone"))
    shutil.move(str(out), str(tmp_path / "moved"))
    assert main(["run", str(tmp_path / "moved" / "config.toml"), "--plan"]) == 0


def test_the_docking_example_stages_its_relative_paths(tmp_path):
    """Data source: examples/mwe_docking.toml's own `../tests/data/...` paths. Staging only: building the docking
    plan needs the docking extra, so this does not run it."""
    ex = ROOT / "examples" / "mwe_docking.toml"
    text, staged = stage_inputs(ex.read_text(), ex.parent, tmp_path / "out")
    assert "../tests" not in text and "inputs/" in text
    assert set(staged) == {"docking.receptor", "pocket.file"}


def test_a_staged_value_that_did_not_take_is_refused(tmp_path, monkeypatch):
    src, cfg = _write(tmp_path)
    import smeltery.run_inputs as ri

    monkeypatch.setattr(ri, "set_value", lambda text, dotted, value: text)
    with pytest.raises(ValueError):
        stage_inputs(cfg.read_text(), src, tmp_path / "out")


def test_a_full_run_records_the_config_entry_in_results(tmp_path, monkeypatch):
    """The SCFs are stubbed: what is checked is that `main` hands `write_outputs` a record carrying the entry."""
    import smeltery.cli as cli
    from smeltery.record import RunRecord

    src, cfg = _write(tmp_path)
    seen = {}
    monkeypatch.setattr(cli, "run_plan", lambda plan: (RunRecord("t", {}, {}, {}), {}))
    monkeypatch.setattr(cli, "write_outputs", lambda plan, rec, out: seen.setdefault("rec", rec) and out / "r.json")
    monkeypatch.setattr(cli, "format_table", lambda plan, out: "")
    assert main(["run", str(cfg), "--out", str(tmp_path / "out")]) == 0
    entry = seen["rec"].results["config"]
    staged = (tmp_path / "out" / "config.toml").read_bytes()
    assert entry["file"] == "config.toml" and entry["sha256"] == hashlib.sha256(staged).hexdigest()
    assert set(entry["inputs"]) == {"pocket.file"}
