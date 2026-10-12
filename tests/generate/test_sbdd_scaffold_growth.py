"""ScaffoldGrowth end to end against a fake runner that replays the real fixture; no GPU, no torch."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

import smeltery.generate
from smeltery.generate.base import GeneratorError
from smeltery.generate.sbdd import ScaffoldGrowth
from smeltery.generate.sbdd_post import FilterConfig

RUNNER_DIR = str(Path(smeltery.generate.__file__).parent)
DATA = Path(__file__).resolve().parents[1] / "data" / "sbdd"

FAKE_RUNNER = """
import json, os, shutil, sys
sys.path.insert(0, RUNNER_DIR)
import _sbdd_runner


def _fail(message):
    print(message, file=sys.stderr)
    sys.exit(2)


try:
    request = _sbdd_runner.load_request(sys.argv[1])
except Exception as exc:
    _fail(f"bad request: {exc}")
extra = set(request) - set(_sbdd_runner.REQUIRED) - {"add_n_nodes"}
if extra:
    _fail(f"unexpected request keys: {sorted(extra)}")
for key in ("diffsbdd_dir", "checkpoint", "pdb", "ref_ligand", "fix_atoms"):
    if not os.path.exists(request[key]):
        _fail(f"{key} does not exist: {request[key]}")
for key in ("ref_ligand", "fix_atoms"):
    if not request[key].endswith(".sdf"):
        _fail(f"{key} must end in .sdf: {request[key]}")
for key in ("n_samples", "timesteps", "resamplings", "seed"):
    if not isinstance(request[key], int):
        _fail(f"{key} must be an int: {request[key]!r}")
if not isinstance(request["center"], str):
    _fail("center must be a string")
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "crash":
    print("boom: something broke", file=sys.stderr)
    sys.exit(3)
if mode == "list_report":
    json.dump([1], open(request["out_report"], "w"))
    shutil.copy(FIXTURE, request["out_sdf"])
    sys.exit(0)
if mode == "no_n_raw":
    json.dump({"ok": True}, open(request["out_report"], "w"))
    shutil.copy(FIXTURE, request["out_sdf"])
    sys.exit(0)
if mode == "garbage_report":
    open(request["out_report"], "w").write("not json")
    shutil.copy(FIXTURE, request["out_sdf"])
    sys.exit(0)
if mode != "no_sdf":
    shutil.copy(FIXTURE if mode != "empty" else EMPTY, request["out_sdf"])
n_raw = 16 if mode not in ("empty", "wrong_count") else (0 if mode == "empty" else 99)
json.dump({
    "ok": True, "n_raw": n_raw, "wall_time_s": 0.1, "seed": request["seed"],
    "checkpoint_sha256": "abc", "diffsbdd_commit": "deadbeef", "diffsbdd_dirty": False,
    "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"), "patches": [],
    "cwd": os.getcwd(),
}, open(request["out_report"], "w"))
"""


@pytest.fixture
def fake_runner(tmp_path):
    empty = tmp_path / "empty.sdf"
    empty.write_text("")
    path = tmp_path / "fake_runner.py"
    path.write_text(
        f"FIXTURE = {str(DATA / 'grown_raw.sdf')!r}\nEMPTY = {str(empty)!r}\nRUNNER_DIR = {RUNNER_DIR!r}\n"
        + FAKE_RUNNER
    )
    return str(path)


@pytest.fixture
def files(tmp_path):
    ckpt = tmp_path / "model.ckpt"
    ckpt.write_bytes(b"x")
    repo = tmp_path / "diffsbdd"
    repo.mkdir()
    pocket = tmp_path / "pocket.pdb"
    pocket.write_text("END\n")
    return {"checkpoint": str(ckpt), "diffsbdd_dir": str(repo), "pocket": str(pocket)}


def _generator(fake_runner, files, **kw):
    return ScaffoldGrowth(
        python=sys.executable,
        runner=fake_runner,
        diffsbdd_dir=files["diffsbdd_dir"],
        checkpoint=files["checkpoint"],
        **kw,
    )


def test_a_run_returns_the_measured_split_and_accounts_for_every_output(
    fake_runner, files, parent_candidate, scaffold_indices
):
    gen = _generator(fake_runner, files)
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=11)
    assert len(result.candidates) == 1 and result.n_raw == 16
    assert result.rejection_counts() == {"unsanitizable": 12, "scaffold_lost": 3}
    assert result.candidates[0].name == "sbdd-0000" and result.candidates[0].poses == []
    prov = result.provenance
    assert prov["n_batches"] == 1 and prov["n_raw"] == 16 and prov["n_accepted"] == 1 and prov["seed"] == 11
    assert prov["rejection_counts"] == {"unsanitizable": 12, "scaffold_lost": 3}
    assert prov["runs"][0]["checkpoint_sha256"] == "abc" and prov["runs"][0]["diffsbdd_dirty"] is False
    json.dumps(prov)  # provenance goes into the run record, so it must be JSON-able


def test_model_poses_are_carried_when_asked(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files, pose_source="model")
    cand = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1).candidates[0]
    assert len(cand.poses) == 1


def test_settings_describe_everything_that_shaped_the_output(fake_runner, files):
    gen = _generator(fake_runner, files, n_samples=8, add_n_nodes=9, filters=FilterConfig(scaffold_match="topology"))
    s = gen.settings()
    assert s["n_samples"] == 8 and s["add_n_nodes"] == 9 and s["timesteps"] == 50 and s["resamplings"] == 20
    assert s["filters"]["scaffold_match"] == "topology" and s["pose_source"] == "embed"
    json.dumps(s)


def test_the_runner_gets_the_inputs_it_needs(fake_runner, files, parent_candidate, scaffold_indices, tmp_path):
    keep = tmp_path / "kept"
    gen = _generator(fake_runner, files, keep_workdir=str(keep), n_samples=5, add_n_nodes=9)
    gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=21)
    request = json.loads((keep / "request_0.json").read_text())
    assert request["n_samples"] == 5 and request["add_n_nodes"] == 9 and request["seed"] == 21
    assert request["pdb"] == str(Path(files["pocket"]).resolve())
    assert request["timesteps"] == 50 and request["resamplings"] == 20 and request["center"] == "ligand"
    scaffold_sdf = (keep / "scaffold.sdf").read_text()
    parent_sdf = (keep / "parent.sdf").read_text()
    assert scaffold_sdf.splitlines()[3].split()[0] == "32" and parent_sdf.splitlines()[3].split()[0] == "41"
    report = json.loads((keep / "report_0.json").read_text())
    assert report["cwd"] == str(Path(files["diffsbdd_dir"]).resolve())  # DiffSBDD is run from its own directory


def test_the_device_sets_cuda_visible_devices_and_none_leaves_it_alone(
    fake_runner, files, parent_candidate, scaffold_indices, monkeypatch
):
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    off = _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert off.provenance["runs"][0]["cuda_visible_devices"] is None
    on = _generator(fake_runner, files, device="1").generate(
        parent_candidate, scaffold_indices, files["pocket"], seed=1
    )
    assert on.provenance["runs"][0]["cuda_visible_devices"] == "1"


def test_every_sample_rejected_is_a_result_not_an_error(fake_runner, files, parent_candidate, scaffold_indices):
    """Review focus 4."""
    gen = _generator(fake_runner, files, filters=FilterConfig(forbidden_smarts={"phosphorus": "[P]"}))
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert result.candidates == [] and result.n_raw == 16
    assert result.rejection_counts() == {"unsanitizable": 12, "scaffold_lost": 3, "forbidden_substructure": 1}


def test_an_empty_sdf_is_zero_raw_outputs(fake_runner, files, parent_candidate, scaffold_indices, monkeypatch):
    """Review focus 3."""
    monkeypatch.setenv("FAKE_MODE", "empty")
    result = _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert result.candidates == [] and result.rejected == [] and result.provenance["n_raw"] == 0


def test_batches_continue_until_the_target_and_deduplicate_across_them(
    fake_runner, files, parent_candidate, scaffold_indices
):
    """Review focus 5, across batches. The fake replays the same 16 samples every time."""
    gen = _generator(fake_runner, files, target_accepted=3, max_batches=3)
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=100)
    assert len(result.candidates) == 1 and result.n_raw == 48
    assert result.rejection_counts()["duplicate"] == 2
    assert [r["seed"] for r in result.provenance["runs"]] == [100, 101, 102]
    assert result.provenance["n_batches"] == 3
    assert sorted(r.index for r in result.rejected) == [i for i in range(48) if i != 12]


def test_sampling_stops_as_soon_as_the_target_is_reached(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files, target_accepted=1, max_batches=5)
    result = gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert result.provenance["n_batches"] == 1


def test_without_a_target_exactly_one_batch_runs(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files, max_batches=4)
    assert gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1).provenance["n_batches"] == 1


@pytest.mark.parametrize(
    "mode, message",
    [
        ("crash", "status 3"),
        ("no_sdf", "no SDF"),
        ("garbage_report", "readable report"),
        ("list_report", "readable report"),
        ("no_n_raw", "readable report"),
        ("wrong_count", "99"),
    ],
)
def test_a_failed_run_is_a_generator_error_with_the_reason(
    fake_runner, files, parent_candidate, scaffold_indices, monkeypatch, mode, message
):
    monkeypatch.setenv("FAKE_MODE", mode)
    with pytest.raises(GeneratorError, match=message):
        _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)


def test_the_runner_stderr_tail_is_in_the_error(fake_runner, files, parent_candidate, scaffold_indices, monkeypatch):
    monkeypatch.setenv("FAKE_MODE", "crash")
    with pytest.raises(GeneratorError, match="boom: something broke"):
        _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)


def test_an_interpreter_that_cannot_start_is_a_generator_error(files, parent_candidate, scaffold_indices, tmp_path):
    not_executable = tmp_path / "python"
    not_executable.write_text("")
    not_executable.chmod(0o755)  # exists and passes `which`, but is not a program the OS can run
    gen = ScaffoldGrowth(python=str(not_executable), diffsbdd_dir=files["diffsbdd_dir"], checkpoint=files["checkpoint"])
    with pytest.raises(GeneratorError, match="could not start"):
        gen.generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)


def test_no_working_directory_is_left_behind_after_success_or_failure(
    fake_runner, files, parent_candidate, scaffold_indices, monkeypatch, tmp_path
):
    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    monkeypatch.setenv("FAKE_MODE", "crash")
    with pytest.raises(GeneratorError):
        _generator(fake_runner, files).generate(parent_candidate, scaffold_indices, files["pocket"], seed=1)
    assert list(root.iterdir()) == []


def test_misconfiguration_is_refused_before_anything_runs(fake_runner, files, parent_candidate, scaffold_indices):
    gen = _generator(fake_runner, files)
    with pytest.raises(ValueError, match="pocket not found"):
        gen.generate(parent_candidate, scaffold_indices, "/nonexistent.pdb", seed=1)
    with pytest.raises(ValueError, match="out of range"):
        gen.generate(parent_candidate, [0, 999], files["pocket"], seed=1)
    from smeltery import Candidate

    with pytest.raises(ValueError, match="bound pose"):
        gen.generate(Candidate("p", parent_candidate.smiles), scaffold_indices, files["pocket"], seed=1)
    for bad in (
        {"pose_source": "docked"},
        {"max_batches": 0},
        {"n_samples": 0},
        {"checkpoint": "/nonexistent.ckpt"},
        {"diffsbdd_dir": "/nonexistent_dir"},
        {"python": "/nonexistent/python"},
    ):
        kw = {"diffsbdd_dir": files["diffsbdd_dir"], "checkpoint": files["checkpoint"], "python": sys.executable, **bad}
        with pytest.raises(ValueError):
            ScaffoldGrowth(runner=fake_runner, **kw).generate(
                parent_candidate, scaffold_indices, files["pocket"], seed=1
            )


def test_the_fake_runner_rejects_a_request_the_real_runner_would_reject(fake_runner, tmp_path):
    """Guards the guard: a request missing a required key must fail the fake, naming the key."""
    request = {
        "diffsbdd_dir": str(tmp_path),
        "checkpoint": str(tmp_path),
        "pdb": str(tmp_path),
        "fix_atoms": str(tmp_path / "s.sdf"),
        "out_sdf": str(tmp_path / "o.sdf"),
        "out_report": str(tmp_path / "r.json"),
        "n_samples": 1,
        "timesteps": 1,
        "resamplings": 1,
        "center": "ligand",
        "seed": 1,
    }  # no "ref_ligand"
    path = tmp_path / "request.json"
    path.write_text(json.dumps(request))
    proc = subprocess.run([sys.executable, fake_runner, str(path)], capture_output=True, text=True)
    assert proc.returncode != 0 and "ref_ligand" in proc.stderr
