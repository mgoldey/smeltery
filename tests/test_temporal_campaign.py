"""`CampaignRunWorkflow`: one workflow per campaign, one activity per stage, heartbeats, retries, cancellation, and the
record on disk as the artifact of record (docs/superpowers/specs/2026-11-20-temporal-per-tier-design.md).

The campaign is the two-candidate config of tests/test_job_backends.py on the fake engine (tests/fake_ferric.py): its
score legs run in child processes the in-process worker spawns, which hang, crash or fail on demand. A stage that
should fail is made to by patching the module function its activity calls, which works because the worker runs in
this process. The workflow tests need a Temporal server (tests/temporal_server.py); the checkpoint and retry-class
tests do not.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import json
import os
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

pytest.importorskip("temporalio", reason="temporalio (the `temporal` extra) is not installed")

import fake_ferric  # noqa: E402
import temporal_server  # noqa: E402

from smeltery import cli, planning, temporal_run  # noqa: E402

FAKE_CHILD = [sys.executable, str(Path(fake_ferric.__file__).resolve())]

TINY = """
campaign = "tiny"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"

[poses]
n_poses = 2
relax_unmapped = true

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

[cut]
keep = 1
z = 2.0
floor = 0.0
"""
RECHECK = "\n[recheck]\nscale = 1.0\n"


def _wait(pred, timeout: float = 60.0, what: str = "condition"):
    t0 = time.monotonic()
    while True:
        got = pred()
        if got:
            return got
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"{what} never held")
        time.sleep(0.1)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with contextlib.suppress(OSError):  # a zombie is dead: it only waits to be reaped
        if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            return False
    return True


class Fake:
    def __init__(self, d: Path) -> None:
        self.dir, self.rules = d, {}
        self.save()

    def save(self) -> None:
        (self.dir / "cfg.json").write_text(
            json.dumps({"rules": {f"{k[0]}|{k[1]}": v for k, v in self.rules.items()}, "dir": str(self.dir)})
        )

    def set(self, label: str, leg: str, rule: str) -> None:
        self.rules[(label, leg)] = rule
        self.save()

    def calls(self) -> list[dict]:
        p = self.dir / "calls.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []

    def hanging_pid(self) -> int | None:
        p = self.dir / "hanging"
        return int(p.read_text()) if p.is_file() else None


@pytest.fixture
def fake(tmp_path, monkeypatch):
    d = tmp_path / "fake"
    d.mkdir()
    monkeypatch.setenv("FAKE_FERRIC", str(d / "cfg.json"))
    monkeypatch.setitem(sys.modules, "ferric", fake_ferric.make())  # the stage activities run in this process
    return Fake(d)


@pytest.fixture
def recheck():
    """`[recheck]`: an analysis that runs SCFs of its own (in the worker, on the fake engine)."""
    from smeltery.methods import unregister

    spec = fake_ferric.register_recheck()
    yield spec
    unregister("recheck")


@pytest.fixture
def server():
    return temporal_server.require()


@pytest.fixture
def queue():
    return f"smeltery-campaign-test-{uuid.uuid4().hex[:8]}"


@contextlib.contextmanager
def worker(address: str, task_queue: str, workers: int = 2):
    """A pipeline worker on its own thread and event loop, as `smeltery worker` runs it."""
    loop = asyncio.new_event_loop()
    ready, box = threading.Event(), {}

    async def run():
        box["stop"] = asyncio.Event()
        client = await temporal_run.connect(address)
        ready.set()
        await temporal_run.run_worker(client, task_queue, workers, 1, box["stop"], FAKE_CHILD)

    th = threading.Thread(target=loop.run_until_complete, args=(run(),), daemon=True)
    th.start()
    if not ready.wait(30):
        raise RuntimeError("the test worker did not start")
    try:
        yield
    finally:
        loop.call_soon_threadsafe(box["stop"].set)
        th.join(60)


def _staged(tmp: Path, text: str, out: Path, heartbeat_s: float = 3.0, **kw) -> temporal_run.RunInput:
    cfg = tmp / f"{out.name}.toml"
    cfg.write_text(text)
    _, info = cli._stage_config(cfg, out, temporal=True)
    staged = out / "config.toml"
    return temporal_run.RunInput(
        str(staged), str(out), 2, info, heartbeat_s=heartbeat_s, config_sha=temporal_run.sha256_file(staged), **kw
    )


def _run(address: str, fn):
    async def go():
        client = await temporal_run.connect(address)
        return await fn(client)

    return asyncio.run(go())


async def _start(client, inp, queue: str, workflow=None):
    return await client.start_workflow(
        workflow or temporal_run.CampaignRunWorkflow.run,
        inp,
        id=temporal_run.workflow_id(inp.out, Path(inp.config).read_text()),
        task_queue=queue,
    )


def _history(address: str, wid: str) -> list:
    async def go(client):
        return [ev async for ev in client.get_workflow_handle(wid).fetch_history_events()]

    return _run(address, go)


def _activities(events) -> list[str]:
    """The activity types the workflow scheduled, in order."""
    return [
        ev.activity_task_scheduled_event_attributes.activity_type.name
        for ev in events
        if ev.HasField("activity_task_scheduled_event_attributes")
    ]


def _attempts(events) -> dict[str, list[int]]:
    """Per activity type, the attempt number of every start (a retried activity starts again with attempt + 1)."""
    by_id = {
        ev.event_id: ev.activity_task_scheduled_event_attributes.activity_type.name
        for ev in events
        if ev.HasField("activity_task_scheduled_event_attributes")
    }
    out: dict[str, list[int]] = {}
    for ev in events:
        if ev.HasField("activity_task_started_event_attributes"):
            a = ev.activity_task_started_event_attributes
            out.setdefault(by_id[a.scheduled_event_id], []).append(a.attempt)
    return out


def _terminate(address: str, wid: str) -> None:
    async def go(client):
        with contextlib.suppress(Exception):
            await client.get_workflow_handle(wid).terminate("test teardown")

    _run(address, go)


def _state(run_dir) -> dict:
    return json.loads((Path(run_dir) / "attestation" / "state.json").read_bytes())


def _record(run_dir) -> dict:
    return json.loads((Path(run_dir) / "attestation" / "run.json").read_bytes())


# ------------------------------------------------------------------------------------------------ no server needed


def test_a_checkpoint_is_read_only_at_its_recorded_digest_and_only_by_its_own_build(tmp_path, monkeypatch):
    ref = temporal_run.save_checkpoint(tmp_path, {"plan": [1, 2, 3]})
    assert temporal_run.load_checkpoint(tmp_path, ref) == {"plan": [1, 2, 3]}
    assert temporal_run.save_checkpoint(tmp_path, {"plan": [1, 2, 3]}) == ref  # content-addressed
    p = tmp_path / "temporal" / "stages" / f"{ref}.pkl"
    p.write_bytes(p.read_bytes() + b"\0")
    with pytest.raises(temporal_run.CheckpointRefused, match="changed after it was recorded"):
        temporal_run.load_checkpoint(tmp_path, ref)
    ref = temporal_run.save_checkpoint(tmp_path, {"plan": "again"})
    from smeltery import reuse

    monkeypatch.setattr(reuse, "source_digest", lambda: "f" * 64)
    with pytest.raises(temporal_run.CheckpointRefused, match="another smeltery build"):
        temporal_run.load_checkpoint(tmp_path, ref)
    temporal_run.clear_checkpoints(tmp_path)
    with pytest.raises(temporal_run.CheckpointRefused, match="is gone; run the same command again"):
        temporal_run.load_checkpoint(tmp_path, ref)


@pytest.mark.parametrize(
    "error, retried",
    [
        (ConnectionResetError("peer reset"), True),  # a network fetch
        (OSError(5, "Input/output error"), True),  # a shared filesystem
        (MemoryError(), True),  # this worker; another may have room
        (FileNotFoundError("no such input"), False),  # the same on every attempt
        (PermissionError("denied"), False),
        (cli.ConfigError("unknown key"), False),
        (cli.StageError("stage 'gates' failed"), False),
        (ValueError("a log of another run"), False),
        (KeyError("a bug"), False),
    ],
)
def test_a_stage_retries_what_can_pass_and_never_what_repeats(error, retried):
    from temporalio.exceptions import ApplicationError

    got = temporal_run._stage_error(error, temporal_run.STAGE_FAILED)
    assert isinstance(got, ApplicationError)
    assert got.non_retryable is (not retried)
    assert got.type == (temporal_run.TRANSIENT if retried else temporal_run.STAGE_FAILED)
    assert type(error).__name__ in str(got)
    refused = temporal_run._stage_error(temporal_run.CheckpointRefused("x"), "k")
    settled = temporal_run._stage_error(temporal_run.JobSettled("x"), "k")
    assert isinstance(refused, ApplicationError) and refused.type == "CheckpointRefused" and refused.non_retryable
    assert isinstance(settled, ApplicationError) and settled.type == "JobSettled" and settled.non_retryable


def test_the_record_is_never_written_into_a_settled_job(tmp_path):
    """A cancel that lands while the record stage runs: the job is settled first, so the stage refuses rather than
    replace the cancelled record (state.json and run.json never disagree; a finished record is not rewritten)."""
    pytest.importorskip("attestation_schema")
    from attestation_schema.jobs import JobSpec, JobState
    from attestation_schema.state import init_state

    from smeltery import jobs

    init_state(tmp_path, run_id="r1", backend="temporal", job_id="w1")
    spec = JobSpec("r1", "tiny", None, {"campaign": "tiny"}, str(tmp_path))
    jobs.settle(tmp_path, spec, JobState.CANCELLED, "cancelled through smeltery jobs", observer=True)
    before = (tmp_path / "attestation" / "run.json").read_bytes()
    inp = temporal_run.RunInput(str(tmp_path / "config.toml"), str(tmp_path))
    with pytest.raises(temporal_run.JobSettled, match="already cancelled"):
        temporal_run._emit_record(tmp_path / "run-x.json", inp)
    with pytest.raises(temporal_run.JobSettled):
        temporal_run._refuse_settled(tmp_path)  # what the record stage checks before the legacy record, too
    assert (tmp_path / "attestation" / "run.json").read_bytes() == before


# ------------------------------------------------------------------------------------------------ the workflow


def test_a_campaign_is_one_workflow_with_one_activity_per_stage(fake, recheck, tmp_path, server, queue):
    out = tmp_path / "out"
    inp = _staged(tmp_path, TINY + RECHECK, out)
    wid = temporal_run.workflow_id(out, Path(inp.config).read_text())
    try:
        with worker(server, queue):

            async def go(client):
                return await temporal_run.start_and_wait(client, inp, queue, poll_s=0.5, log=lambda *_: None)

            summary = _run(server, go)
    finally:
        _terminate(server, wid)
    names = _activities(_history(server, wid))
    stages = [n for n in names if n not in ("pipeline_score",)]
    assert stages == [
        "campaign_begin",
        "campaign_plan",
        "campaign_cut",
        "campaign_analysis",  # [recheck]
        "campaign_record",
        "campaign_settle",
    ]
    assert names.count("pipeline_score") == 8  # the measurement tier: one activity per SCF leg
    assert names.index("campaign_plan") < names.index("pipeline_score") < names.index("campaign_cut")
    rec = json.loads(Path(summary.record).read_text())
    assert rec["results"]["recheck"]["n"] == 4 and rec["results"]["runner"] == "temporal"
    assert not (out / "temporal" / "stages").exists()  # stage checkpoints are scheduling state: gone with the run
    assert not (out / "stages.partial.jsonl").exists()  # the live stage log goes once the record is written


def test_a_running_leg_heartbeats_what_it_is_running(fake, tmp_path, server, queue):
    fake.set("propanol pose 1", "vacuum", "hang")
    out = tmp_path / "out"
    inp = _staged(tmp_path, TINY, out, heartbeat_s=2.0)
    wid = temporal_run.workflow_id(out, Path(inp.config).read_text())

    async def details(client):
        desc = await client.get_workflow_handle(wid).describe()
        got = []
        for pa in desc.raw_description.pending_activities:
            if pa.heartbeat_details.payloads:
                (d,) = await client.data_converter.decode(pa.heartbeat_details.payloads)
                got.append(d)
        return got

    try:
        with worker(server, queue):
            _run(server, lambda c: _start(c, inp, queue))
            _wait(fake.hanging_pid, what="the leg hanging")
            beats = _wait(
                lambda: [d for d in _run(server, details) if d.get("phase") == "running"], 30, "a running heartbeat"
            )
    finally:
        _terminate(server, wid)
    (beat,) = beats
    assert {k: beat[k] for k in ("stage", "candidate", "pose", "leg", "field_model", "rung", "attempt")} == {
        "stage": "measure",
        "candidate": "propanol",
        "pose": 1,
        "leg": "vacuum",
        "field_model": None,
        "rung": 0,
        "attempt": 1,
    }
    assert beat["elapsed_s"] > 0


def test_a_transient_failure_retries_its_stage_alone_reusing_every_finished_one(
    fake, recheck, tmp_path, server, queue, monkeypatch
):
    """An analysis stage fails once with an OSError (a shared filesystem hiccup): Temporal retries that stage only.
    The plan is not rebuilt, no SCF runs again, the cut is not redone: the retry starts from the cut's checkpoint."""
    builds = []
    real_build, real_analysis = planning.build_plan, temporal_run.analysis_sync
    # where the plan stage reads it (`temporal_run._prepare`: `from .planning import build_plan`)
    monkeypatch.setattr(planning, "build_plan", lambda *a, **k: builds.append(1) or real_build(*a, **k))
    failed = []

    def flaky(done, key):
        if not failed:
            failed.append(key)
            raise OSError(5, "Input/output error")
        return real_analysis(done, key)

    monkeypatch.setattr(temporal_run, "analysis_sync", flaky)
    out = tmp_path / "out"
    inp = _staged(tmp_path, TINY + RECHECK, out)
    wid = temporal_run.workflow_id(out, Path(inp.config).read_text())
    try:
        with worker(server, queue):

            async def go(client):
                return await temporal_run.start_and_wait(client, inp, queue, poll_s=0.5, log=lambda *_: None)

            summary = _run(server, go)
    finally:
        _terminate(server, wid)
    events = _history(server, wid)
    attempts = _attempts(events)
    assert failed == ["recheck"] and attempts["campaign_analysis"] == [2]  # one start event: its last attempt
    assert attempts["campaign_plan"] == [1] and attempts["campaign_cut"] == [1]
    assert len(builds) == 1  # the plan was built once, by the plan stage: not again by a later stage or the retry
    scores = [c for c in fake.calls() if "recheck" not in c["label"]]
    assert len(scores) == 8  # every SCF leg once
    assert Path(summary.record).is_file()


def _job_backend(server: str, queue: str, heartbeat_s: float = 2.0):
    pytest.importorskip("attestation_schema", reason="campaign jobs need the `attestation` extra")
    from smeltery import jobs

    return jobs, jobs.TemporalJobBackend(server, "default", queue, workers=2, heartbeat_s=heartbeat_s)


def test_a_failure_that_repeats_is_not_retried_and_the_workflow_settles_the_job_failed(
    fake, recheck, tmp_path, server, queue, monkeypatch
):
    """A deterministic failure (a ValueError: the same inputs fail the same way) in a stage: one attempt, and the
    WORKFLOW settles the job failed and writes its terminal record -- nobody calls status() first. The record carries
    the arm, and lists the stages the run had completed."""

    def broken(done, key):
        raise ValueError(f"{key}: the analysis refuses these inputs")

    monkeypatch.setattr(temporal_run, "analysis_sync", broken)
    jobs, backend = _job_backend(server, queue)
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY + RECHECK)
    spec = jobs.campaign_spec(cfg, tmp_path / "run", arm="arm-a", ext={"smeltery.test": {"k": 1}})
    try:
        with worker(server, queue):
            h = backend.submit(spec)
            st = _wait(lambda: _state(h.run_dir)["state"] in ("failed", "complete") and _state(h.run_dir), 120)
    finally:
        _terminate(server, h.job_id)
    assert st["state"] == "failed"
    assert st["message"].startswith("recheck failed: ") and "the analysis refuses these inputs" in st["message"]
    assert [x["state"] for x in st["history"]] == ["submitted", "running", "failed"]
    attempts = _attempts(_history(server, h.job_id))
    assert attempts["campaign_analysis"] == [1]  # not retried
    rec = _record(h.run_dir)
    assert rec["status"] == "failed" and rec["arm"] == "arm-a" and rec["producer"]["name"] == "smeltery jobs"
    done = [s["stage"] for s in rec["ext"]["smeltery.job"]["stages_completed"]]
    assert done[-2:] == ["field_interaction", "cut"] and "poses" in done  # it got as far as the cut
    assert rec["finished_at"] == st["updated_at"]
    assert backend.status(h).state.value == "failed"  # an observer agrees, and changes nothing
    assert _state(h.run_dir) == st


def test_a_cancel_reaches_the_running_tier_and_the_workflow_ends_cancelled(fake, tmp_path, server, queue):
    """`cancel` on a running job asks the workflow to cancel: the running score activity kills its child, the workflow
    settles the job cancelled and ends CANCELED (not terminated), and the cancel is not recorded as an SCF error."""
    fake.set("propanol pose 0", "field", "hang")
    jobs, backend = _job_backend(server, queue)
    cfg = tmp_path / "c.toml"
    cfg.write_text(TINY)
    spec = jobs.campaign_spec(cfg, tmp_path / "run", arm="arm-b")
    try:
        with worker(server, queue):
            h = backend.submit(spec)
            pid = _wait(fake.hanging_pid, what="the leg hanging")
            _wait(lambda: _state(h.run_dir)["state"] == "running", what="the job running")
            status = backend.cancel(h)
            assert status.state.value == "cancelled"
            _wait(lambda: not _alive(pid), timeout=30, what="the cancelled SCF dying")

            async def describe(client):
                return (await client.get_workflow_handle(h.job_id).describe()).status

            from temporalio.client import WorkflowExecutionStatus

            assert _run(server, describe) == WorkflowExecutionStatus.CANCELED
    finally:
        _terminate(server, h.job_id)
    st = _state(h.run_dir)
    assert [x["state"] for x in st["history"]] == ["submitted", "running", "cancelled"]
    rec = _record(h.run_dir)
    assert rec["status"] == "cancelled" and rec["arm"] == "arm-b"
    done = [s["stage"] for s in rec["ext"]["smeltery.job"]["stages_completed"]]
    assert "poses" in done and "field_interaction" not in done  # stopped mid-measurement: the plan's stages only
    rows = [json.loads(x) for x in (Path(h.run_dir) / "calcs.jsonl").read_text().splitlines() if x.strip()]
    assert not [r for r in rows if "outcome" in r], "a cancel is not an SCF outcome"
    assert not list(Path(h.run_dir).glob("run-*.json"))
    assert backend.cancel(h) == status  # idempotent


def test_an_execution_of_the_frozen_workflow_still_completes_and_carries_the_arm(fake, tmp_path, server, queue):
    """`PipelineRunWorkflow` stays registered for executions started before `CampaignRunWorkflow`: its activities
    keep their names and inputs (a `RunInput` payload without arm/ext still decodes), and its finalize now carries
    an arm too."""
    out = tmp_path / "out"
    inp = _staged(tmp_path, TINY, out, arm="legacy-arm", ext={"smeltery.test": {"old": True}})
    old = dataclasses.replace(inp, arm=None, ext=None)
    assert temporal_run.RunInput(**{k: v for k, v in dataclasses.asdict(old).items() if k not in ("arm", "ext")})
    wid = temporal_run.workflow_id(out, Path(inp.config).read_text())
    try:
        with worker(server, queue):

            async def go(client):
                h = await _start(client, inp, queue, temporal_run.PipelineRunWorkflow.run)
                return await h.result()

            summary = _run(server, go)
    finally:
        _terminate(server, wid)
    assert "campaign_plan" not in _activities(_history(server, wid))  # the old shape: prepare ... finalize
    rec = _record(out)
    assert rec["arm"] == "legacy-arm" and rec["ext"]["smeltery.test"] == {"old": True}
    assert Path(summary.record).is_file()
