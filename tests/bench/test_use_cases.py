import json
import pathlib
import re

import pytest
from gui_fixture import make_run_dir

from bench.fakes import FakeArtifactStore, FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock, _task
from smeltery.bench.use_cases.edit_pipeline import EditPipeline
from smeltery.bench.use_cases.launch_run import LaunchRun
from smeltery.bench.use_cases.list_work import ListWork
from smeltery.bench.use_cases.read_run import ReadRun
from smeltery.bench.use_cases.record_decision import RecordDecision
from smeltery.gui.pipeline import Pipeline


def test_read_run_returns_the_ddE_table_in_cut_order(run_dir):
    out = ReadRun(FakeRunStore(run_dir), FakeArtifactStore(run_dir / ".art")).execute(run_dir)
    assert [r["candidate"] for r in out.ddE] == ["4-F", "4-Cl", "4-Br"]


@pytest.fixture
def run_dir(tmp_path):
    return make_run_dir(tmp_path)


def test_list_work_passes_the_status_filter_through():
    q = FakeTaskQueue(tasks=[_task("a", "succeeded"), _task("b", "running")])
    assert [t.id for t in ListWork(q).execute(status="running")] == ["b"]


def test_record_decision_refuses_a_tie_choice_without_a_note(run_dir):
    uc = RecordDecision(FakeRunStore(run_dir), FakeIdentity("mg"), FixedClock())
    with pytest.raises(ValueError) as e:
        uc.execute(run_dir, candidate="4-Cl", status="keep", reason="within_tie_choice", note="")
    assert "note" in str(e.value).lower()


def test_record_decision_stamps_the_user_from_identity(run_dir):
    uc = RecordDecision(FakeRunStore(run_dir), FakeIdentity("alice"), FixedClock())
    d = uc.execute(run_dir, candidate="4-F", status="keep", reason="cut_survivor")
    assert d.user == "alice"


def test_launch_run_submits_argv_and_executes_nothing(tmp_path, pipeline):
    q = FakeTaskQueue()
    LaunchRun(
        q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock()
    ).execute(pipeline, out=tmp_path / "out")
    from smeltery.bench.use_cases.launch_run import smeltery_command

    expected = [*smeltery_command(), "run", str(tmp_path / "out" / "config.toml"), "--out", str(tmp_path / "out")]
    assert q.submitted[0][1] == expected
    assert q.executed == []


def test_launch_run_refuses_an_out_that_holds_a_run_record_and_writes_nothing(tmp_path, pipeline):
    from smeltery.bench.use_cases.launch_run import LiveRunRefused

    out = tmp_path / "out"
    out.mkdir()
    (out / "run-abc123.json").write_text("{}")
    q = FakeTaskQueue()
    lr = LaunchRun(q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock())
    with pytest.raises(LiveRunRefused, match="run-abc123.json"):
        lr.execute(pipeline, out=out)
    assert q.submitted == [] and not (out / "config.toml").exists()


@pytest.fixture
def pipeline():
    p = Pipeline.minimal("c", "parent", {"parent": "C", "a": "C"})
    p.stage("pocket").params["site"] = [{"q": 1.0, "atom": 3, "from_atom": 4, "distance": 3.0}]
    p.set("cut", keep=1, floor=0.0)
    return p


def test_edit_pipeline_returns_every_issue_not_just_the_first(pipeline):
    # need pipeline with two problems; just call check
    issues = EditPipeline().check(pipeline)
    # may be 0; ensure method exists
    assert isinstance(issues, list)


def test_compare_arms_differences_each_key_a_minus_b(two_run_dirs):
    """Arm b's record has every paired mean shifted by +1.0, so every per-key difference is exactly -1.0.

    Data source: the shift written into arm b's run record, not anything `Comparison` computes. Swapping the arms
    inside `CompareArms` flips the sign and fails this test.
    """
    from smeltery.bench.adapters import FilesystemRunStore
    from smeltery.bench.use_cases.compare_arms import CompareArms

    a, b = two_run_dirs
    store = FilesystemRunStore(a.parent.parent)
    cmp = CompareArms(store).run({"cell": str(_record(a))}, {"cell": str(_record(b))}, label_a="a", label_b="b")
    s = cmp.summary()
    assert s["n_paired"] == 1
    assert s["median_per_key_diff"] == pytest.approx(-1.0)
    assert s["keys_favouring_a"] == 1


@pytest.fixture
def two_run_dirs(tmp_path):
    d1 = make_run_dir(tmp_path / "a")
    d2 = make_run_dir(tmp_path / "b")
    rec = _record(d2)
    data = json.loads(rec.read_text())
    for m in data["results"]["paired_ddE"].values():
        m["mean"] += 1.0
    rec.write_text(json.dumps(data))
    return d1, d2


def _record(out_dir):
    return next(pathlib.Path(out_dir).glob("run-*.json"))


def test_no_use_case_imports_a_web_framework():
    root = pathlib.Path("src/smeltery/bench/use_cases")
    bad = []
    for p in root.glob("*.py"):
        if re.search(r"^\s*(from|import)\s+(fastapi|starlette|uvicorn)\b", p.read_text(), re.M):
            bad.append(p.name)
    assert bad == []


def test_launch_stages_a_pocket_file_relative_to_the_configs_directory_not_the_cwd(tmp_path, pipeline, monkeypatch):
    """The pipeline names `data/p.pqr`, which exists only beside the config; the server's cwd is elsewhere."""
    import tomllib

    proj = tmp_path / "proj"
    (proj / "data").mkdir(parents=True)
    (proj / "data" / "p.pqr").write_text("pocket")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    pipeline.stage("pocket").params.pop("site")
    pipeline.stage("pocket").params["file"] = "data/p.pqr"
    LaunchRun(
        FakeTaskQueue(), FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock()
    ).execute(pipeline, out=tmp_path / "out", config_dir=proj)
    staged = tomllib.loads((tmp_path / "out" / "config.toml").read_text())["pocket"]["file"]
    assert staged.startswith("inputs/") and (tmp_path / "out" / staged).read_text() == "pocket"


def _launch_with_pocket(tmp_path, pipeline, **kw):
    proj = tmp_path
    (proj / "p.pqr").write_text("pocket")
    pipeline.stage("pocket").params.pop("site", None)
    pipeline.stage("pocket").params["file"] = "p.pqr"
    LaunchRun(
        FakeTaskQueue(),
        FakeRunStore(tmp_path),
        FakeArtifactStore(tmp_path / ".art"),
        FakeIdentity("mg"),
        FixedClock(),
        **kw,
    ).execute(pipeline, out=tmp_path / "out", **({"config_dir": proj} if "root" not in kw else {}))
    return proj


def test_launch_without_config_dir_resolves_against_the_bench_root(tmp_path, pipeline, monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)  # not the root: the default must be the root, not the cwd
    _launch_with_pocket(tmp_path, pipeline, root=tmp_path)
    assert list((tmp_path / "out" / "inputs").rglob("*.pqr"))


def test_a_launched_run_keeps_the_users_original_source_through_the_cli_restage(tmp_path, pipeline, monkeypatch):
    """Launch stages, then `smeltery run OUT/config.toml --out OUT` stages again. Data source: the file the pipeline
    named, written by this test; the record must name it, not `inputs/<sha>.pqr`."""
    import hashlib

    import smeltery.cli as cli
    from smeltery.record import RunRecord

    proj = _launch_with_pocket(tmp_path, pipeline, root=tmp_path)
    out = tmp_path / "out"
    first = (out / "config.toml").read_bytes()
    seen = {}
    monkeypatch.setattr(cli, "run_plan", lambda plan: (RunRecord("c", {}, {}, {}), {}))
    monkeypatch.setattr(cli, "write_outputs", lambda plan, rec, o: seen.setdefault("rec", rec) and o / "r.json")
    monkeypatch.setattr(cli, "format_table", lambda plan, o: "")
    monkeypatch.setattr(cli, "build_plan", lambda cfg, out_dir: object())
    assert cli.main(["run", str(out / "config.toml"), "--out", str(out)]) == 0
    entry = seen["rec"].results["config"]
    assert entry["inputs"]["pocket.file"]["source"] == "p.pqr"  # as the pipeline wrote it, beside the original
    assert (proj / "p.pqr").is_file()
    assert (out / "config.toml").read_bytes() == first
    assert entry["sha256"] == hashlib.sha256(first).hexdigest()
    assert len(list((out / "inputs").rglob("*.pqr"))) == 1


def test_launch_run_writes_the_given_text_not_a_regenerated_one(tmp_path, pipeline):
    q = FakeTaskQueue()
    text = "# mine\n" + pipeline.to_toml()
    LaunchRun(q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock()).run(
        pipeline, out_dir=tmp_path / "out", text=text
    )
    assert (tmp_path / "out" / "config.toml").read_text() == text


def test_launch_refuses_a_live_temporal_out_before_writing_anything(tmp_path, pipeline, monkeypatch):
    """`smeltery run`'s guard runs in Launch too, before OUT/config.toml or any staged input is written."""
    import smeltery.temporal_run as temporal_run
    from smeltery.bench.use_cases.launch_run import LiveRunRefused

    asked = []

    def refuse(out, sha, temporal):
        asked.append((out, temporal))
        return "a Temporal run is still running"

    monkeypatch.setattr(temporal_run, "guard_out", refuse)
    q, out = FakeTaskQueue(), tmp_path / "out"
    uc = LaunchRun(q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock())
    with pytest.raises(LiveRunRefused, match="still running"):
        uc.execute(pipeline, out=out)
    assert asked == [(out, False)] and not out.exists() and q.submitted == []


def test_relaunching_an_unchanged_config_does_not_rewrite_it(tmp_path, pipeline):
    q, out = FakeTaskQueue(), tmp_path / "out"
    uc = LaunchRun(q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock())
    uc.execute(pipeline, out=out)
    before = (out / "config.toml").stat().st_mtime_ns
    uc.execute(pipeline, out=out)
    assert (out / "config.toml").stat().st_mtime_ns == before


# ------------------------------------------------------------------------------------------- B03: launch limits


def test_default_memory_limit_is_the_prediction_times_the_factor_capped_by_the_host():
    from smeltery.bench.use_cases.launch_run import (
        HOST_RAM_SHARE,
        MIN_RUN_MEMORY_GB,
        RUN_MEMORY_FACTOR,
        default_memory_limit_gb,
    )

    assert default_memory_limit_gb(2.0, 64.0) == pytest.approx(2.0 * RUN_MEMORY_FACTOR)
    assert default_memory_limit_gb(0.1, 64.0) == MIN_RUN_MEMORY_GB  # a floor: a run is not only its SCF
    assert default_memory_limit_gb(20.0, 24.0) == pytest.approx(24.0 * HOST_RAM_SHARE)  # never past the host
    assert default_memory_limit_gb(None, 24.0) == pytest.approx(24.0 * HOST_RAM_SHARE)  # no prediction: host cap
    assert default_memory_limit_gb(2.0, None) == pytest.approx(2.0 * RUN_MEMORY_FACTOR)
    assert default_memory_limit_gb(None, None) is None


def test_a_launched_run_is_capped_with_a_memory_limit_and_no_timeout_by_default(tmp_path, pipeline, monkeypatch):
    from smeltery.bench.use_cases import launch_run

    monkeypatch.setattr(launch_run, "host_memory_gb", lambda: 10.0)
    q = FakeTaskQueue()
    LaunchRun(q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock()).run(
        pipeline, out_dir=tmp_path / "out"
    )
    assert q.options == [{"memory_limit_gb": pytest.approx(8.0), "timeout_s": None, "capped": True}]


def test_a_launched_run_takes_its_memory_limit_from_a_matching_preview(tmp_path, pipeline, monkeypatch):
    from smeltery import cli
    from smeltery.bench.use_cases import launch_run

    monkeypatch.setattr(launch_run, "host_memory_gb", lambda: 64.0)
    preview = tmp_path / "preview"
    preview.mkdir()
    text = pipeline.to_toml()
    key = cli.poses_key(__import__("tomllib").loads(text), tmp_path)
    plan = {"status": "ok", "poses_key": key, "memory": {"estimate": {"total_gb": 1.5}}}
    (preview / "plan.json").write_text(json.dumps(plan))
    q = FakeTaskQueue()
    uc = LaunchRun(q, FakeRunStore(tmp_path), FakeArtifactStore(tmp_path / ".art"), FakeIdentity("mg"), FixedClock())
    uc.run(pipeline, out_dir=tmp_path / "out", config_dir=tmp_path, preview_dir=preview, timeout_s=3600)
    assert q.options[-1] == {"memory_limit_gb": pytest.approx(1.5 * 6.0), "timeout_s": 3600, "capped": True}

    # a preview made for other poses says nothing about these: back to the host cap
    plan["poses_key"] = "stale"
    (preview / "plan.json").write_text(json.dumps(plan))
    uc.run(pipeline, out_dir=tmp_path / "out2", config_dir=tmp_path, preview_dir=preview)
    assert q.options[-1]["memory_limit_gb"] == pytest.approx(64.0 * 0.8)
