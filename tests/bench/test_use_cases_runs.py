"""The run-judgement use cases (decisions, comments, proposals, the frozen report, comparisons, run-directory reads)
against fake ports: the rules live here, the routes only translate them to HTTP."""

from __future__ import annotations

import json
import types
from datetime import datetime, timezone
from pathlib import Path

import pytest
from gui_fixture import make_run_dir

from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock
from smeltery.bench.use_cases import read_run
from smeltery.bench.use_cases.comment_on_run import CommentOnRun, CommentRefused
from smeltery.bench.use_cases.compare_arms import CompareArms
from smeltery.bench.use_cases.propose_candidate import ProposalRefused, ProposeCandidate
from smeltery.bench.use_cases.publish_report import REPORT_EXTERNAL, PublishReport
from smeltery.bench.use_cases.record_decision import DecisionRefused, RecordDecision, decisions_path, load
from smeltery.decisions import DecisionSet


@pytest.fixture
def run_dir(tmp_path):
    return Path(make_run_dir(tmp_path))


@pytest.fixture
def the_run(run_dir):
    return FakeRunStore(run_dir).read_run(run_dir)


class FakeCommentStore:
    def __init__(self):
        self.added = []

    def add(self, *, run, anchor, author, body):
        c = types.SimpleNamespace(
            id=f"c{len(self.added)}",
            run=run,
            anchor_text=anchor,
            author=author,
            body=body,
            created_at=datetime(2026, 11, 3, 12, 0, 1, tzinfo=timezone.utc),
        )
        self.added.append(c)
        return c

    def for_run(self, digest):
        return [c for c in self.added if c.run == digest]


class FakeProjectStore:
    def __init__(self):
        self.proposals = []

    def add_proposal(self, p):
        self.proposals.append(p)
        return p


# ------------------------------------------------------------------------------------------------ decisions


def test_two_different_candidates_can_be_decided_one_after_the_other(run_dir, the_run):
    """The use case used to load the saved file without the run's candidates, so the second, not-yet-decided
    candidate had no SMILES and was refused ("no SMILES for ..."). The route never had that bug."""
    uc = RecordDecision(FakeRunStore(run_dir), FakeIdentity("mg"), FixedClock())
    uc.set(the_run, "4-F", "keep", "cut_survivor")
    d = uc.set(the_run, "4-Cl", "reject", "liability")
    assert d.smiles == the_run.candidates["4-Cl"]
    saved = DecisionSet.load(decisions_path(the_run))
    assert [(x.candidate, x.status) for x in saved.decisions] == [("4-F", "keep"), ("4-Cl", "reject")]


def test_the_file_is_beside_the_record_and_named_for_the_digest(the_run):
    assert decisions_path(the_run) == Path(the_run.out_dir) / f"decisions-{the_run.record['input_digest'][:12]}.json"


def test_an_unknown_candidate_is_refused_and_nothing_is_written(run_dir, the_run):
    uc = RecordDecision(FakeRunStore(run_dir), FakeIdentity("mg"), FixedClock())
    with pytest.raises(DecisionRefused, match="no candidate 'ghost' in this run"):
        uc.set(the_run, "ghost", "keep", "cut_survivor")
    with pytest.raises(DecisionRefused, match="no candidate 'ghost', 'other' in this run"):
        uc.set_many(the_run, ["4-F", "ghost", "other"], "keep", "cut_survivor")
    assert not decisions_path(the_run).exists()


def test_set_many_is_all_or_nothing(run_dir, the_run):
    uc = RecordDecision(FakeRunStore(run_dir), FakeIdentity("alice"), FixedClock())
    with pytest.raises(DecisionRefused, match="needs a note"):
        uc.set_many(the_run, ["4-F", "4-Cl"], "keep", "within_tie_choice")
    assert not decisions_path(the_run).exists()
    done = uc.set_many(the_run, ["4-F", "4-Cl"], "hold", "synthesis")
    assert [d.user for d in done] == ["alice", "alice"]
    assert [d.candidate for d in load(the_run).decisions] == ["4-F", "4-Cl"]


def test_a_saved_file_that_does_not_validate_is_not_replaced_by_an_empty_set(the_run):
    decisions_path(the_run).write_text("{}")
    with pytest.raises(ValueError) as e:
        load(the_run)
    assert not isinstance(e.value, DecisionRefused)


# ------------------------------------------------------------------------------------------------ comments


@pytest.mark.parametrize("anchor", ["run", "candidate:4-F", "pose:4-F:2"])
def test_a_comment_is_stored_by_the_current_user(anchor):
    store = FakeCommentStore()
    c = CommentOnRun(store, FakeIdentity("alice"), FixedClock()).run("d" * 64, anchor, "look here")
    assert (c.run, c.anchor_text, c.author, c.body) == ("d" * 64, anchor, "alice", "look here")


@pytest.mark.parametrize(
    ("anchor", "body", "why"),
    [
        ("everything", "x", "anchor is 'run', 'candidate:<name>' or 'pose:<name>:<i>'"),
        ("pose:4-F:x", "x", "anchor is"),
        ("run", "   ", "a comment needs some text"),
    ],
)
def test_a_bad_comment_is_refused_and_not_stored(anchor, body, why):
    store = FakeCommentStore()
    with pytest.raises(CommentRefused, match=why.replace("(", r"\(")):
        CommentOnRun(store, FakeIdentity(), FixedClock()).run("d" * 64, anchor, body)
    assert store.added == []


# ------------------------------------------------------------------------------------------------ proposals


def test_a_proposal_is_keyed_on_the_run_and_smiles_and_stamped_by_the_injected_clock():
    store = FakeProjectStore()
    clock = FixedClock(datetime(2026, 11, 3, 9, 30, 0, tzinfo=timezone.utc))
    p = ProposeCandidate(store, FakeIdentity("alice"), clock).run("d" * 64, "Cc1ccccc1", "synthesis", "try it", "4-Me")
    from smeltery.project import make_entry_id

    assert p.created_at == "2026-11-03T09:30:00Z"
    assert p.id == make_entry_id("d" * 64, "Cc1ccccc1", "2026-11-03T09:30:00Z")
    assert (p.run, p.author, p.candidate, p.status) == ("d" * 64, "alice", "4-Me", "pending")
    assert store.proposals == [p]


@pytest.mark.parametrize(("smiles", "reason"), [("C1CC", "synthesis"), ("CCO", "vibes")])
def test_a_bad_proposal_is_refused_and_not_stored(smiles, reason):
    store = FakeProjectStore()
    with pytest.raises(ProposalRefused):
        ProposeCandidate(store, FakeIdentity(), FixedClock()).run("d" * 64, smiles, reason)
    assert store.proposals == []


# ------------------------------------------------------------------------------------------------ report


def test_the_written_snapshot_is_the_rendered_report_byte_for_byte(the_run):
    comments = FakeCommentStore()
    comments.add(run=the_run.record["input_digest"], anchor="run", author="mg", body="first look")
    uc = PublishReport(comments, FixedClock())
    html = uc.render(the_run)
    assert "first look" in html
    snap = uc.write(the_run)
    assert snap.path == Path(the_run.out_dir) / f"report-{the_run.record['input_digest'][:12]}.html"
    assert snap.path.read_bytes() == html.encode("utf-8")
    assert set(snap.external) <= set(REPORT_EXTERNAL)


def test_the_report_carries_the_decisions_made_on_the_run(run_dir, the_run):
    RecordDecision(FakeRunStore(run_dir), FakeIdentity("mg"), FixedClock()).set(
        the_run, "4-F", "keep", "other", "the note in the report"
    )
    assert "the note in the report" in PublishReport(FakeCommentStore(), FixedClock()).render(the_run)


# ------------------------------------------------------------------------------------------------ compare


def test_two_runs_of_one_campaign_are_told_apart_by_arm_and_digest(tmp_path):
    a, b = Path(make_run_dir(tmp_path / "a")), Path(make_run_dir(tmp_path / "b"))
    store = FakeRunStore(tmp_path)
    ra, rb = store.read_run(a), store.read_run(b)
    got = CompareArms(store).runs(ra, rb)
    assert got.comparison.label_a == f"A ({ra.campaign} {ra.record['input_digest'][:8]})"
    assert got.comparison.label_b.startswith("B (")
    assert got.candidates is not None  # one parent: the per-candidate rows are comparable


# ------------------------------------------------------------------------------------------------ run directory


def test_gate_rows_judge_each_pose_and_an_unjudged_pose_is_not_a_pass():
    record = {
        "inputs": {"gates": {"max_strain_kcal": 5}},
        "results": {
            "candidate_order": ["a"],
            "gates": {
                "posebusters": {"a": {"poses": [{"passed": True, "failed_checks": []}]}},
                "strain_kcal": {"a": [1.0, 9.0]},
            },
        },
    }
    got = read_run.gate_rows(record)
    assert got["ran"] == ["posebusters", "strain_kcal"] and got["max_strain_kcal"] == 5.0
    assert [(r["pose"], r["posebusters"], r["over_strain"], r["checked"], r["ok"]) for r in got["rows"]] == [
        (0, True, False, True, True),
        (1, None, True, True, False),
    ]
    assert read_run.gate_rows({"results": {}}) == {"ran": [], "max_strain_kcal": None, "rows": [], "raw": {}}


def test_calc_lines_map_the_pose_and_never_read_outside_the_run_directory(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    lines = [{"candidate": "a", "pose": 2, "field_model": "AMBER"}, {"candidate": "a", "pose": 0}]
    (run_dir / "calcs.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    record = {"results": {"calcs": {"file": "calcs.jsonl", "primary": "AMBER"}, "pose_index": {"a": [0, 2]}}}
    got = read_run.calc_lines(run_dir, record, "a", 1, None)
    assert got == {"file": "calcs.jsonl", "pose": 2, "lines": [lines[0]]}
    with pytest.raises(read_run.PoseOutOfRange, match="a kept 2 poses"):
        read_run.calc_lines(run_dir, record, "a", 2, None)
    (tmp_path / "secret.jsonl").write_text("{}")
    record["results"]["calcs"]["file"] = "../secret.jsonl"
    with pytest.raises(read_run.CalcsUnavailable, match="outside the run directory"):
        read_run.calc_lines(run_dir, record, "a", 0, None)
    with pytest.raises(read_run.CalcsUnavailable, match="kept no SCF log"):
        read_run.calc_lines(run_dir, {"results": {}}, "a", 0, None)


def test_the_recorded_config_is_the_file_and_an_edit_is_flagged(tmp_path):
    import hashlib

    out = tmp_path / "run"
    out.mkdir()
    text = "# the comments are the documentation\ncampaign = 'x'\n"
    (out / "config.toml").write_text(text)
    sha = hashlib.sha256(text.encode()).hexdigest()
    run = types.SimpleNamespace(out_dir=out, record={"results": {"config": {"file": "config.toml", "sha256": sha}}})
    got = read_run.recorded_config(run)
    assert got["text"] == text and got["changed"] is False and got["reason"] is None
    (out / "config.toml").write_text(text + "# edited\n")
    assert read_run.recorded_config(run)["changed"] is True
    run.record["results"]["config"]["file"] = "../elsewhere.toml"
    assert "outside the run directory" in read_run.recorded_config(run)["reason"]
    run.record["results"]["config"]["file"] = "gone.toml"
    assert "no longer beside the run" in read_run.recorded_config(run)["reason"]
    assert read_run.recorded_config(types.SimpleNamespace(out_dir=out, record={}))["file"] is None


# ------------------------------------------------------------------------------------------------ launch port


def test_only_a_durable_queue_launches_with_its_own_run_flags(tmp_path):
    """`LaunchRun` asks the queue (`DurableRuns.run_flags`), not which adapter class it is."""
    from smeltery.bench.use_cases.launch_run import LaunchRun, uses_temporal

    class Durable(FakeTaskQueue):
        def run_flags(self):
            return ["--durable"]

    assert not uses_temporal(FakeTaskQueue()) and uses_temporal(Durable())
    out = tmp_path / "out"
    out.mkdir()
    (out / "config.toml").write_text('campaign = "x"\n')
    q = Durable()
    LaunchRun(q, None, None, FakeIdentity(), FixedClock(), root=tmp_path).resume(out)
    assert q.submitted[0][1][-1] == "--durable"
    assert q.options == [{"memory_limit_gb": None, "timeout_s": None, "capped": True}]
