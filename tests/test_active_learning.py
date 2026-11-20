"""The active-learning loop (`smeltery.al`): propose, score, record, repeat (docs/active-learning.md).

An iteration is a real campaign job on the fake engine against a `LocalJobBackend`, exactly as
`tests/test_mcp_server.py` and `tests/test_job_backends.py` drive one, so "scored" and "complete" mean what they mean
in production. The heavy tier is never run. The core-record assertions are gated on `attestation_schema` (the
`attestation` extra), like the rest of the record tests; everything else runs without it.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("attestation_schema", reason="campaign jobs need attestation-schema (the `attestation` extra)")

import fake_ferric  # noqa: E402

from smeltery import al, jobs  # noqa: E402
from smeltery.settings import resolve  # noqa: E402

FAKE_RUNNER = [sys.executable, str(Path(fake_ferric.__file__).resolve()), "_job"]
#: A reference alcohol and a pool of longer ones; the fake engine's field term grows with atom count, so the
#: candidates get distinct, deterministic scores (as the MCP tests rely on).
REFERENCE = "CCO"
POOL = ["CCCO", "CCCCO", "CCCCCO", "CCCCCCO", "CCCCCCCO", "CCCCCCCCO"]
TARGET = """
schema_version = 1
[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[[pocket.site]]
q = -0.5
atom = 0
from_atom = 1
distance = 3.5
"""


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    d = tmp_path / "fake"
    d.mkdir()
    monkeypatch.setenv("FAKE_FERRIC", str(d / "cfg.json"))
    (d / "cfg.json").write_text(json.dumps({"rules": {}, "dir": str(d)}))
    (tmp_path / "target.toml").write_text(TARGET)
    return tmp_path


def _backend(workspace):
    return jobs.LocalJobBackend(workspace / "ws", runner=FAKE_RUNNER)


def _run(workspace, backend, **kw):
    kw.setdefault("loop", "demo")
    kw.setdefault("target", str(workspace / "target.toml"))
    kw.setdefault("reference_smiles", REFERENCE)
    kw.setdefault("proposer", al.PoolProposer(POOL, seed=0))
    kw.setdefault("iterations", 2)
    kw.setdefault("batch", 2)
    kw.setdefault("out_dir", str(workspace / "loop"))
    return al.run_loop(settings=resolve({}), backend=backend, timeout_s=300, **kw)


def test_end_to_end_loop_scores_and_records(workspace):
    backend = _backend(workspace)
    try:
        manifest = _run(workspace, backend, iterations=3, batch=2)
    finally:
        backend.queue.stop_live("teardown")

    done = [it for it in manifest.iterations if it.state == "complete"]
    assert len(done) == 3, manifest.stopped
    assert manifest.stopped["reason"] in ("iterations", "exhausted")
    # every iteration is an arm of the one family, with real scores
    assert [it.family for it in done] == ["al:demo"] * 3
    assert [it.arm for it in done] == ["iter-000", "iter-001", "iter-002"]
    for it in done:
        assert len(it.candidates) == 2
        assert all(c["score"] is not None for c in it.candidates)
        assert it.batch_best == min(c["score"] for c in it.candidates)
        assert it.core_record_sha256 is not None


def test_dedup_across_iterations(workspace):
    backend = _backend(workspace)
    try:
        manifest = _run(workspace, backend, iterations=3, batch=2)
    finally:
        backend.queue.stop_live("teardown")
    scored = [c["smiles"] for it in manifest.iterations for c in it.candidates]
    assert len(scored) == len(set(scored)), f"a molecule was scored twice: {scored}"
    assert REFERENCE not in scored  # the reference is never proposed as a candidate


def test_unparseable_proposal_is_dropped_not_fatal(workspace):
    class WithJunk:
        """Returns one junk SMILES alongside good ones; the loop drops the junk and scores the rest."""

        def propose(self, history, n):
            good = [s for s in POOL if s not in {c.smiles for c in history}][: n - 1]
            return ["not-a-smiles", *good]

    backend = _backend(workspace)
    try:
        manifest = _run(workspace, backend, iterations=1, batch=3, proposer=WithJunk())
    finally:
        backend.queue.stop_live("teardown")
    done = [it for it in manifest.iterations if it.state == "complete"]
    assert len(done) == 1
    assert all(c["smiles"] != "not-a-smiles" for c in done[0].candidates)


def test_resume_continues_from_the_manifest(workspace):
    backend = _backend(workspace)
    try:
        first = _run(workspace, backend, iterations=1, batch=2)
        assert len([it for it in first.iterations if it.state == "complete"]) == 1
        # a fresh proposer and a fresh backend, same loop dir: resume picks up where it stopped
        second = _run(workspace, _backend(workspace), iterations=3, batch=2, proposer=al.PoolProposer(POOL, seed=0))
    finally:
        backend.queue.stop_live("teardown")
    arms = [it.arm for it in second.iterations if it.state == "complete"]
    assert arms == ["iter-000", "iter-001", "iter-002"]
    # the first iteration's run directory was not recomputed (its record predates the second call)
    assert (workspace / "loop" / "iter-000" / "attestation" / "run.json").exists()


def test_budget_refusal_stops_before_submitting(workspace):
    backend = _backend(workspace)
    try:
        manifest = _run(workspace, backend, iterations=2, batch=2, budget_cpu_h=1e-9)
    finally:
        backend.queue.stop_live("teardown")
    assert manifest.stopped["reason"] == "over_budget"
    assert [it for it in manifest.iterations if it.state == "complete"] == []
    assert not (workspace / "loop" / "iter-000").exists()  # nothing was submitted


def test_too_few_new_candidates_exhausts(workspace):
    backend = _backend(workspace)
    try:
        manifest = _run(workspace, backend, iterations=5, batch=2, proposer=al.PoolProposer(POOL[:2], seed=0))
    finally:
        backend.queue.stop_live("teardown")
    # the pool has two molecules: iteration 0 scores both, iteration 1 has nothing new and stops
    assert manifest.stopped["reason"] == "exhausted"
    assert len([it for it in manifest.iterations if it.state == "complete"]) == 1


def test_core_records_are_grouped_for_compare(workspace):
    """The records carry what `attest runs compare al:<loop>` needs, AT THEIR FIRST WRITE: one family, an arm per
    iteration, the loop's own ext namespace, and label-free directional `best_paired_ddE` / `mean_paired_ddE` that
    are the same key in every arm's record (the per-analogue metrics cannot serve: each arm screens other
    molecules)."""
    import attestation_schema as schema

    backend = _backend(workspace)
    try:
        manifest = _run(workspace, backend, iterations=2, batch=2)
    finally:
        backend.queue.stop_live("teardown")

    for it in manifest.iterations:
        run_dir = workspace / "loop" / it.run_dir
        rec = schema.read(run_dir)  # the frozen reader accepts it
        assert rec.family == "al:demo"
        assert rec.arm == it.arm
        raw = json.loads((run_dir / "attestation" / "run.json").read_bytes())
        al_ext = raw["ext"]["smeltery.al"]
        assert raw["ext"]["smeltery"], "the run's own namespace is kept beside the loop's"
        assert al_ext["loop"] == "demo" and al_ext["iteration"] == it.iteration
        assert al_ext["proposer"]["class"] == "PoolProposer"
        assert al_ext["proposer_seeds"] == {"pool": 0}  # exposed by the proposer, never invented
        assert [c["name"] for c in al_ext["batch"]] == [c["name"] for c in it.candidates]
        best = [m for m in raw["metrics"] if m["name"] == "best_paired_ddE"]
        mean = [m for m in raw["metrics"] if m["name"] == "mean_paired_ddE"]
        assert len(best) == 1 and len(mean) == 1
        assert best[0]["direction"] == "min" and best[0]["labels"] == {}
        assert best[0]["unit"] == "kcal/mol"
        assert best[0]["value"] == pytest.approx(it.batch_best)


def test_a_run_without_an_arm_is_unchanged(workspace):
    """The arm and its metrics appear only for a run whose submitter named an arm, so every other smeltery record --
    every run outside a job -- is byte-for-byte what it was."""
    from smeltery.core_record import arm_metrics, core_fields

    legacy = {
        "campaign": "plain",
        "input_digest": "a" * 64,
        "results": {
            "cut": {"survivors": ["x"]},
            "funnel": [],
            "units": {"dE_int": "kcal/mol"},
            "paired_ddE": {"x": {"mean": -1.0, "sem": 0.1, "n": 3}},
        },
    }
    common = {
        "legacy_name": "run-aaaaaaaaaaaa.json",
        "legacy_sha256": "b" * 64,
        "manifest": {"name": "attestation/digests.json", "sha256": "c" * 64, "role": "manifest"},
        "producer": {"name": "smeltery", "version": "0"},
        "dependencies": [],
    }
    without = core_fields(legacy, **common)
    assert without["arm"] is None
    assert [m["name"] for m in without["metrics"] if not m["labels"]] == ["cut.survivors"]

    with_arm = core_fields(legacy, **common, arm="iter-007", extra_ext={"smeltery.al": {"loop": "l"}})
    assert with_arm["arm"] == "iter-007"
    assert with_arm["ext"]["smeltery.al"] == {"loop": "l"}
    assert with_arm["ext"]["smeltery"] == without["ext"]["smeltery"]
    # the only difference in metrics is the arm's own two, appended
    assert with_arm["metrics"] == without["metrics"] + arm_metrics(legacy["results"])
    assert [m["name"] for m in arm_metrics(legacy["results"])] == ["best_paired_ddE", "mean_paired_ddE"]


def test_the_finished_core_record_is_never_rewritten(workspace):
    """The record is the artifact of record: its bytes can already have been hashed by `settle`, a dataset's
    `source_runs[].record_sha256` or a ledger scan. The loop READS its sha256 for the manifest and never touches the
    file again -- so iteration 0's record is byte- and mtime-identical after later iterations have run."""
    backend = _backend(workspace)
    try:
        first = _run(workspace, backend, iterations=1, batch=2)
        record = workspace / "loop" / "iter-000" / "attestation" / "run.json"
        before_sha = hashlib.sha256(record.read_bytes()).hexdigest()
        before_mtime = record.stat().st_mtime_ns
        # the sha the manifest pins IS the bytes the job wrote
        assert first.iterations[0].core_record_sha256 == before_sha
        second = _run(workspace, _backend(workspace), iterations=3, batch=2, proposer=al.PoolProposer(POOL, seed=0))
    finally:
        backend.queue.stop_live("teardown")

    assert hashlib.sha256(record.read_bytes()).hexdigest() == before_sha
    assert record.stat().st_mtime_ns == before_mtime
    # and every iteration's pinned sha still matches its bytes on disk
    for it in second.iterations:
        path = workspace / "loop" / it.run_dir / "attestation" / "run.json"
        assert hashlib.sha256(path.read_bytes()).hexdigest() == it.core_record_sha256
    assert not hasattr(al, "restamp_core_record"), "the loop must not re-stamp a finished record"


def test_check_spec_validates_the_arm_and_the_ext_namespaces():
    """`jobs.check_spec` is where an arm becomes honest: a label, and never the run's own `smeltery` namespace."""
    from attestation_schema import JobSpec

    from smeltery import jobs

    cfg = {"campaign": "al:demo"}

    def spec(**kw):
        base = {"run_id": "r0", "family": "al:demo", "arm": None, "config": cfg, "run_dir": "/tmp/x"}
        return JobSpec(**{**base, **kw})

    jobs.check_spec(spec())  # no arm, no ext: as before
    jobs.check_spec(spec(arm="iter-000", ext={"smeltery.al": {"loop": "demo"}}))
    for bad in ("iter 000", "iter/000", "", "iter\n000"):
        with pytest.raises(ValueError, match="arm must be"):
            jobs.check_spec(spec(arm=bad))
    with pytest.raises(ValueError, match="the run's own"):
        jobs.check_spec(spec(ext={"smeltery": {"x": 1}}))
    with pytest.raises(ValueError, match="must map to an object"):
        jobs.check_spec(spec(ext={"other": 1}))


def test_proposer_identity_and_loading(tmp_path):
    prop = al.PoolProposer(POOL, seed=7)
    ident = al.proposer_identity(prop, "smeltery.al:PoolProposer")
    assert ident["ref"] == "smeltery.al:PoolProposer"
    assert ident["module"] == "smeltery.al" and ident["class"] == "PoolProposer"
    assert len(ident["source_sha256"]) == 64  # the module is a real file

    with pytest.raises(al.AlError):
        al.load_proposer("nomodulehere:obj")
    with pytest.raises(al.AlError):
        al.load_proposer("smeltery.al:does_not_exist")
