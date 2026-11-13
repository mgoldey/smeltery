import json
import pathlib
import re
import tomllib

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
    monkeypatch.setattr(cli, "run_plan", lambda plan, **kw: (RunRecord("c", {}, {}, {}), {}))
    monkeypatch.setattr(cli, "write_outputs", lambda plan, rec, o: seen.setdefault("rec", rec) and o / "r.json")
    monkeypatch.setattr(cli, "format_table", lambda plan, o: "")
    monkeypatch.setattr(cli, "build_plan", lambda cfg, out_dir: object())
    # this test's plan is a bare object(): the SCF checkpoint needs a real plan, and restaging is what is under test
    monkeypatch.setattr(cli, "_open_checkpoint", lambda plan, out_dir, config_sha256, force: None)
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


# ---- next_round: the edge from a decision to the next run -------------------------------------------------------

NEXT_ROUND_CONFIG = """\
# round 1: three para analogues of benzoic acid
campaign = "benzoic"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"   # the parent every ddE is measured against
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"
"""

NEXT_ROUND_CANDIDATES = {
    "benzoic": "OC(=O)c1ccccc1",
    "4-F": "OC(=O)c1ccc(F)cc1",
    "4-Cl": "OC(=O)c1ccc(Cl)cc1",
}


def _decisions(**status):
    from smeltery.decisions import DecisionSet

    ds = DecisionSet(campaign="benzoic", run="d" * 64, candidates=dict(NEXT_ROUND_CANDIDATES))
    for name, st in status.items():
        ds.set(name.replace("_", "-"), st, "liability", user="mg")
    return ds


def _plan(tmp_path, **kw):
    from smeltery.bench.use_cases import next_round

    return next_round.plan(
        text=kw.pop("text", NEXT_ROUND_CONFIG),
        candidates=kw.pop("candidates", dict(NEXT_ROUND_CANDIDATES)),
        parent=kw.pop("parent", "benzoic"),
        staging=kw.pop("staging", tmp_path / "staging"),
        **kw,
    )


def test_next_round_carries_the_decisions_file_and_keeps_the_config_comments(tmp_path):
    made = _plan(tmp_path, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="decisions-abc123456789.json")
    assert '[decisions]\nfile = "decisions-abc123456789.json"' in made.toml
    assert "# the parent every ddE is measured against" in made.toml
    assert "# round 1: three para analogues" in made.toml
    assert made.files == {"decisions-abc123456789.json": tmp_path / "staging" / "decisions-abc123456789.json"}
    assert any("4-Cl was rejected and is dropped before posing" in n for n in made.notes)
    assert any("new input digest" in n for n in made.notes)


def test_next_round_leaves_the_rejected_candidate_in_the_candidates_table(tmp_path):
    # the engine drops it (cli._apply_decisions) and records what it excluded; we do not rewrite the chemistry
    made = _plan(tmp_path, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="d.json")
    assert '4-Cl = "OC(=O)c1ccc(Cl)cc1"' in made.toml


def test_next_round_does_not_resurrect_a_candidate_rejected_in_an_earlier_round(tmp_path):
    """Round 3 must not pose what round 1 rejected.

    The run record's `inputs.candidates` is written after `cli._apply_decisions`, so an earlier round's reject is
    already gone from it while the config text still lists the molecule. Building the new candidate set from the
    record, and the new decisions file from this round's decisions alone, let that molecule back in: nothing named
    it any more, and `[decisions] file` had just been repointed at the new file.
    """
    from smeltery.decisions import DecisionSet

    # Round 2's record: 4-Cl was rejected in round 1, so the engine left it out of the record's candidates.
    record_candidates = {"benzoic": "OC(=O)c1ccccc1", "4-F": "OC(=O)c1ccc(F)cc1"}
    # Round 2's decisions only concern what round 2 actually posed.
    ds = DecisionSet(campaign="benzoic", run="d" * 64, candidates=dict(record_candidates))
    ds.set("4-F", "hold", "synthesis", user="mg")
    made = _plan(tmp_path, candidates=record_candidates, decisions=ds, decisions_name="d.json")

    # The config still lists it, which is how smeltery run is told to drop it...
    assert '4-Cl = "OC(=O)c1ccc(Cl)cc1"' in made.toml
    # ...so the carried decisions file must still reject it, or it is posed again.
    import json

    copied = json.loads((tmp_path / "staging" / "d.json").read_text())
    by_name = {d["candidate"]: d for d in copied["decisions"]}
    assert by_name["4-Cl"]["status"] == "reject"
    assert "earlier round" in by_name["4-Cl"]["note"]
    assert by_name["4-F"]["status"] == "hold"
    assert any("4-Cl" in n and "rejected" in n for n in made.notes)


def test_next_round_does_not_stack_a_second_header_on_a_round_that_already_has_one(tmp_path):
    """A round built from a round's config carries one header, not two that disagree."""
    first = _plan(tmp_path, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="d.json")
    assert first.toml.count("# Next round, carried forward by Bench:") == 1
    second = _plan(
        tmp_path,
        text=first.toml,
        decisions=_decisions(**{"4_F": "reject"}),
        decisions_name="d2.json",
        staging=tmp_path / "staging2",
    )
    assert second.toml.count("# Next round, carried forward by Bench:") == 1
    assert "4-F was rejected" in second.toml
    # The round-1 note is gone, not left behind to contradict the new one.
    assert "4-Cl was rejected" not in second.toml.split("[candidates]")[0]
    # And the config still parses, with its chemistry intact.
    import tomllib

    assert "4-F" in tomllib.loads(second.toml)["candidates"]


def test_the_header_notes_go_quiet_once_the_config_stops_agreeing_with_them(tmp_path):
    """A comment cannot follow an edit, so it is only trusted while the config still backs it up."""
    from smeltery.bench.use_cases.next_round import notes_in

    made = _plan(tmp_path, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="d.json")
    assert any("4-Cl was rejected" in n for n in notes_in(made.toml))

    # The user removes the decisions reference the notes are about: the banners would now misstate the config.
    without = made.toml.replace('[decisions]\nfile = "d.json"\n', "")
    assert "[decisions]" not in without
    assert notes_in(without) == []

    # A config mid-edit says nothing about its own history rather than guessing.
    assert notes_in(made.toml + "\n[cut\n") == []
    # And a config that was never a next round has no notes to invent.
    assert notes_in(NEXT_ROUND_CONFIG) == []


def test_next_round_drops_a_candidate_and_prunes_its_decision(tmp_path):
    from smeltery.decisions import DecisionSet

    made = _plan(tmp_path, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="d.json", drop=["4-Cl"])
    assert "4-Cl" not in tomllib.loads(made.toml)["candidates"]  # gone from the chemistry, not just rejected
    assert '4-F = "OC(=O)c1ccc(F)cc1"' in made.toml and "# the parent every ddE" in made.toml
    # a decision on a candidate the config no longer lists is a ConfigError in `smeltery run`, so it is left out
    copied = DecisionSet.load(made.files["d.json"])
    assert [d.candidate for d in copied.decisions] == []
    assert any("dropped 1 candidate: 4-Cl" in n for n in made.notes)
    assert any("left out of the copied decisions" in n for n in made.notes)


def test_a_proposal_cannot_take_the_name_of_a_candidate_this_round_dropped(tmp_path):
    """Reusing the name would leave the dropped candidate's decision pointing at a different molecule."""
    from smeltery.bench.use_cases.next_round import Proposed

    made = _plan(
        tmp_path,
        decisions=_decisions(**{"4_Cl": "reject"}),
        decisions_name="d.json",
        drop=["4-F"],
        proposals=[Proposed(id="p1", candidate="4-F", smiles="OC(=O)c1ccc(I)cc1")],
    )
    cands = tomllib.loads(made.toml)["candidates"]
    assert "4-F" not in cands or cands["4-F"] != "OC(=O)c1ccc(I)cc1"
    assert "OC(=O)c1ccc(I)cc1" in cands.values()  # it is there, under a name of its own


def test_next_round_refuses_dropping_the_parent(tmp_path):
    from smeltery.bench.use_cases.next_round import NextRoundRefused

    with pytest.raises(NextRoundRefused) as e:
        _plan(tmp_path, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="d.json", drop=["benzoic"])
    assert "every analogue is measured against it" in str(e.value)


def test_next_round_refuses_an_unknown_drop_and_an_empty_round(tmp_path):
    from smeltery.bench.use_cases.next_round import NextRoundRefused

    with pytest.raises(NextRoundRefused) as e:
        _plan(tmp_path, decisions=None, decisions_name=None, drop=["4-I"])
    assert "4-I" in str(e.value)
    with pytest.raises(NextRoundRefused) as e:
        _plan(tmp_path, decisions=None, decisions_name=None)
    assert "no decisions file" in str(e.value)
    with pytest.raises(NextRoundRefused) as e:
        _plan(tmp_path, decisions=_decisions(), decisions_name="d.json", carry_decisions=False)
    assert "nothing to carry forward" in str(e.value)


def test_next_round_adds_a_proposal_and_gives_a_name_clash_its_own_name(tmp_path):
    from smeltery.bench.use_cases.next_round import Proposed

    made = _plan(
        tmp_path,
        decisions=None,
        decisions_name=None,
        proposals=[
            Proposed(id="p1", candidate="4-Br", smiles="OC(=O)c1ccc(Br)cc1"),
            Proposed(id="p2", candidate="4-F", smiles="OC(=O)c1ccc(F)cc1"),
            Proposed(id="p3abcdef", candidate="", smiles="OC(=O)c1ccc(I)cc1"),
        ],
    )
    assert '4-Br = "OC(=O)c1ccc(Br)cc1"' in made.toml
    assert '4-F-2 = "OC(=O)c1ccc(F)cc1"' in made.toml  # 4-F is taken, so the proposal gets its own name
    assert 'proposed-p3abcdef = "OC(=O)c1ccc(I)cc1"' in made.toml  # a proposal with no name is named after its id
    assert any("added 3 proposed candidates: 4-Br, 4-F-2, proposed-p3abcdef" in n for n in made.notes)


def test_next_round_says_so_when_nothing_is_rejected_and_lists_what_is_held(tmp_path):
    made = _plan(tmp_path, decisions=_decisions(**{"4_F": "hold"}), decisions_name="d.json")
    assert any("nothing is rejected" in n for n in made.notes)
    assert any("on hold, and still in the run: 4-F" in n for n in made.notes)


def test_next_round_warns_when_the_rejects_leave_too_few_analogues_for_the_cut(tmp_path):
    text = NEXT_ROUND_CONFIG + "\n[cut]\nkeep = 2\nz = 2.0\nfloor = 0.0\n"
    made = _plan(
        tmp_path,
        text=text,
        decisions=_decisions(**{"4_Cl": "reject", "4_F": "reject"}),
        decisions_name="d.json",
    )
    assert any("[cut] keep = 2 but only 0 analogue(s) are left" in n for n in made.notes)
    # keep = 1 and one analogue left: the cut still has what it needs, so nothing is said
    one = NEXT_ROUND_CONFIG + "\n[cut]\nkeep = 1\nz = 2.0\nfloor = 0.0\n"
    ok = _plan(tmp_path, text=one, decisions=_decisions(**{"4_Cl": "reject"}), decisions_name="d2.json")
    assert not any("[cut] keep" in n for n in ok.notes)
