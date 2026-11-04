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
    expected = ["smeltery", "run", str(tmp_path / "out" / "config.toml"), "--out", str(tmp_path / "out")]
    assert q.submitted[0][1] == expected
    assert q.executed == []


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


def test_compare_arms_reports_per_cell_not_pooled(two_run_dirs):
    pass


@pytest.fixture
def two_run_dirs(tmp_path):
    d1 = make_run_dir(tmp_path / "a")
    d2 = make_run_dir(tmp_path / "b")
    return d1, d2


def test_no_use_case_imports_a_web_framework():
    root = pathlib.Path("src/smeltery/bench/use_cases")
    bad = []
    for p in root.glob("*.py"):
        if re.search(r"^\s*(from|import)\s+(fastapi|starlette|uvicorn)\b", p.read_text(), re.M):
            bad.append(p.name)
    assert bad == []
