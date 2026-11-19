"""The campaign job backends (`smeltery.jobs`), one contract run against both: `local` and `temporal`.

Each test is parametrized over the two backends, so the local one cannot quietly drift from the contract the Temporal
one is held to (and the reverse). The campaign is a real `smeltery run` of a two-candidate config on the fake engine
(`tests/fake_ferric.py`: deterministic energies, hangs and crashes on demand), which finishes in about a second.

The `temporal` leg needs a Temporal server: the dev server at $SMELTERY_TEMPORAL_ADDRESS (default 127.0.0.1:7233),
or, with SMELTERY_TEST_TEMPORAL_LOCAL=1, `WorkflowEnvironment.start_local()` (which downloads the dev server on first
use, so it is opt-in: offline it fails). Without either it skips, like tests/test_temporal_run.py. Each temporal test
runs an in-process pipeline worker on its own task queue, its score children on the fake engine.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("attestation_schema", reason="campaign jobs need attestation-schema (the `attestation` extra)")

import attestation_schema as schema  # noqa: E402
import fake_ferric  # noqa: E402
from attestation_schema import state as schema_state  # noqa: E402
from attestation_schema.canonical import canonicalize  # noqa: E402

from smeltery import jobs  # noqa: E402
from smeltery.cli import main as smeltery_main  # noqa: E402

FAKE_RUNNER = [sys.executable, str(Path(fake_ferric.__file__).resolve()), "_job"]
FAKE_CHILD = [sys.executable, str(Path(fake_ferric.__file__).resolve())]
TEMPORAL_ADDRESS = os.environ.get("SMELTERY_TEMPORAL_ADDRESS", "127.0.0.1:7233")
S = schema.JobState

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
#: A config `smeltery run` refuses when it validates it (an unknown key): a run that fails fast, on both backends.
BROKEN = TINY + "\nnot_a_section = 1\n"


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with contextlib.suppress(OSError):  # a zombie is dead: it only waits to be reaped
        if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            return False
    return True


def _wait(pred, timeout: float = 60.0, what: str = "condition"):
    t0 = time.monotonic()
    while True:
        got = pred()
        if got:
            return got
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"{what} never held")
        time.sleep(0.1)


# ------------------------------------------------------------------------------------------------ the harnesses


class Fake:
    """The fake engine's controls (`rules[(label, leg)]`), saved where every child process reads them."""

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

    def hanging_pid(self) -> int | None:
        p = self.dir / "hanging"
        return int(p.read_text()) if p.is_file() else None


class LocalHarness:
    name = "local"

    def __init__(self, tmp: Path) -> None:
        self.backend = jobs.LocalJobBackend(tmp / "ws", runner=FAKE_RUNNER)

    @contextlib.contextmanager
    def running(self):
        yield  # the local backend starts a job's supervisor at submit: there is no worker to start

    @contextlib.contextmanager
    def held(self, tmp: Path, fake: Fake):
        """While this is open, a newly submitted job stays `submitted`: a hanging job holds the one run slot."""
        fake.set("ethanol pose 0", "vacuum", "hang")
        blocker = self.backend.submit(jobs.campaign_spec(_config(tmp, name="blocker.toml"), tmp / "blocker"))
        _wait(lambda: fake.hanging_pid(), what="the blocker hanging")
        try:
            yield
        finally:
            self.backend.cancel(blocker)

    def crash(self, handle) -> None:
        """Kill the job's supervisor (the process that owns it) with SIGKILL, as a crashed or OOM-killed worker."""
        task = _wait(lambda: self.backend.task_of(handle), what="the job's task")
        os.kill(task["supervisor_pid"], signal.SIGKILL)
        _wait(lambda: not _alive(task["supervisor_pid"]), what="the supervisor dying")

    def close(self) -> None:
        self.backend.queue.stop_live("test teardown")


def _temporal_server() -> bool:
    host, _, port = TEMPORAL_ADDRESS.rpartition(":")
    try:
        with socket.create_connection((host, int(port)), timeout=1):
            return True
    except (OSError, ValueError):
        return False


class TemporalHarness:
    """A pipeline worker on its own thread and event loop (as `smeltery worker` runs it), on this test's queue."""

    name = "temporal"
    _env = None  # a WorkflowEnvironment started for the session, when opted in

    def __init__(self, tmp: Path) -> None:
        pytest.importorskip("temporalio", reason="temporalio (the `temporal` extra) is not installed")
        self.address = self._server()
        self.queue = f"smeltery-jobs-test-{uuid.uuid4().hex[:8]}"
        self.backend = jobs.TemporalJobBackend(self.address, "default", self.queue, workers=2, heartbeat_s=3.0)
        self.started: list = []
        submit = self.backend.submit

        def tracking(spec):
            h = submit(spec)
            self.started.append(h)
            return h

        self.backend.submit = tracking  # type: ignore[method-assign]

    @classmethod
    def _server(cls) -> str:
        if _temporal_server():
            return TEMPORAL_ADDRESS
        if os.environ.get("SMELTERY_TEST_TEMPORAL_LOCAL") != "1":
            pytest.skip(f"no Temporal server at {TEMPORAL_ADDRESS} (SMELTERY_TEST_TEMPORAL_LOCAL=1 starts one)")
        if cls._env is None:
            from temporalio.testing import WorkflowEnvironment

            try:
                cls._env = asyncio.run(WorkflowEnvironment.start_local())
            except RuntimeError as e:
                pytest.skip(f"WorkflowEnvironment.start_local() failed: {e}")
        return cls._env.client.service_client.config.target_host

    @contextlib.contextmanager
    def running(self):
        from smeltery import temporal_run

        loop = asyncio.new_event_loop()
        ready, box = threading.Event(), {}

        async def run():
            box["stop"] = asyncio.Event()
            client = await temporal_run.connect(self.address)
            ready.set()
            await temporal_run.run_worker(client, self.queue, 2, 1, box["stop"], FAKE_CHILD)

        th = threading.Thread(target=loop.run_until_complete, args=(run(),), daemon=True)
        th.start()
        if not ready.wait(30):
            raise RuntimeError("the test worker did not start")
        try:
            yield
        finally:
            loop.call_soon_threadsafe(box["stop"].set)
            th.join(60)

    @contextlib.contextmanager
    def held(self, tmp: Path, fake: Fake):
        yield  # no worker polls this test's queue until `running()`: a submitted workflow waits

    def crash(self, handle) -> None:
        pytest.skip(
            "a crashed Temporal worker does not fail a job: its activities are retried on the next worker "
            "(tests/test_temporal_run.py::test_a_killed_worker_resumes_without_recomputing)"
        )

    def close(self) -> None:
        from smeltery import temporal_run

        async def go():
            client = await temporal_run.connect(self.address)
            for h in self.started:
                with contextlib.suppress(Exception):
                    await client.get_workflow_handle(h.job_id).terminate("test teardown")

        if self.started:
            asyncio.run(go())


@pytest.fixture
def fake(tmp_path, monkeypatch):
    d = tmp_path / "fake"
    d.mkdir()
    monkeypatch.setenv("FAKE_FERRIC", str(d / "cfg.json"))
    monkeypatch.setitem(sys.modules, "ferric", fake_ferric.make())  # a Temporal worker's prepare/finalize run here
    return Fake(d)


@pytest.fixture(params=[LocalHarness, TemporalHarness], ids=["local", "temporal"])
def harness(request, tmp_path, fake):
    h = request.param(tmp_path)
    try:
        yield h
    finally:
        h.close()


def _config(tmp: Path, text: str = TINY, name: str = "c.toml") -> Path:
    p = tmp / name
    p.write_text(text)
    return p


def _spec(tmp: Path, text: str = TINY, run: str = "run"):
    return jobs.campaign_spec(_config(tmp, text, f"{run}.toml"), tmp / run)


def _finish(backend, handle, timeout: float = 120.0):
    return _wait(lambda: (s := backend.status(handle)).state in schema.TERMINAL and s, timeout, "the job finishing")


def _state(handle) -> dict:
    """state.json as the bytes on disk say it (not through the reader under test)."""
    return json.loads(jobs.state_path(handle.run_dir).read_bytes())


def _history(handle) -> list[str]:
    return [h["state"] for h in _state(handle)["history"]]


def _check_state_file(handle, spec, backend_name: str) -> dict:
    """state.json as the spec defines it, for this job: its keys, identity, history and canonical bytes."""
    raw = jobs.state_path(handle.run_dir).read_bytes()
    st = json.loads(raw)
    assert canonicalize(st) == raw  # JCS bytes, like every file under attestation/
    assert set(st) == {"schema", "run_id", "backend", "job_id", "state", "updated_at", "message", "history"}
    assert st["schema"] == "attestation.state/1"
    assert (st["run_id"], st["backend"], st["job_id"]) == (spec.run_id, backend_name, handle.job_id)
    states = [S(h["state"]) for h in st["history"]]
    assert states[0] is S.SUBMITTED and states[-1] is S(st["state"])
    assert all(schema.can_transition(a, b) for a, b in zip(states, states[1:]))
    assert st["updated_at"] == st["history"][-1]["at"]
    ats = [h["at"] for h in st["history"]]
    assert ats == sorted(ats)  # fixed-width UTC: text order is time order
    parsed = jobs.read_state(handle.run_dir)  # attestation_schema.state's reader accepts what the writer wrote
    assert isinstance(parsed, schema_state.StateFile) and parsed.to_dict() == st
    assert (parsed.state, parsed.backend, parsed.job_id, parsed.run_id) == (
        S(st["state"]),
        backend_name,
        handle.job_id,
        spec.run_id,
    )
    assert parsed.status == schema.JobStatus(parsed.state, st["updated_at"], st["message"])
    assert parsed.started_at == next((h["at"] for h in st["history"] if h["state"] == "running"), None)
    return st


def _check_terminal_record(handle, spec, state: str):
    """Every terminal job has a core record whose status is its state, whose run_id is the job's and whose config is
    the campaign config the job was given, whoever wrote it (attestation-schema's job/record contract). A failed or
    cancelled job's is the minimal one, naming the job in ext["smeltery.job"], ending when its state.json says."""
    rec = schema.read(handle.run_dir)
    st = _state(handle)
    assert rec.status == state == st["state"]
    assert rec.family == spec.family == "tiny" and rec.arm is None
    assert rec.run_id == spec.run_id == st["run_id"]
    assert canonicalize(rec.config) == canonicalize(spec.config) and rec.config["campaign"] == "tiny"
    assert schema_state.spec_conflicts(spec, rec) == []
    assert rec.status is not None  # a job's record always says how it ended
    if state == "complete":  # the run's own record: smeltery's plan inputs in ext, never in config
        assert rec.producer.name == "smeltery" and "inputs" in rec.ext["smeltery"]
        assert "candidates" in rec.ext["smeltery"]["inputs"] and jobs.EXT not in rec.ext
        assert rec.finished_at <= st["history"][-1]["at"]  # the run ended, then the job was settled
    else:
        assert rec.producer.name == "smeltery jobs" and rec.metrics == []
        assert [d.role for d in rec.data] == ["manifest"]
        assert set(rec.ext) == {jobs.EXT}
        job = rec.ext[jobs.EXT]
        assert set(job) == {"backend", "job_id", "stages_completed"}  # the message lives in state.json only
        assert (job["backend"], job["job_id"]) == (st["backend"], handle.job_id)
        assert job["stages_completed"] is None or isinstance(job["stages_completed"], list)
        assert rec.finished_at == st["history"][-1]["at"] == st["updated_at"]  # one instant ends both
        assert rec.started_at == next((h["at"] for h in st["history"] if h["state"] == "running"), None)
    return rec


# ------------------------------------------------------------------------------------------------ the contract


def test_the_backend_is_a_job_backend(harness):
    assert isinstance(harness.backend, schema.JobBackend)
    assert harness.backend.name == harness.name


def test_submit_returns_at_once_and_has_written_submitted(harness, tmp_path, fake):
    fake.set("ethanol pose 0", "vacuum", "hang")  # were submit to wait for the run, it would wait 10 minutes
    spec = _spec(tmp_path)
    t0 = time.monotonic()
    h = harness.backend.submit(spec)
    assert time.monotonic() - t0 < 10
    assert (h.backend, h.run_id, Path(h.run_dir)) == (harness.name, spec.run_id, Path(spec.run_dir))
    st = _check_state_file(h, spec, harness.name)
    assert st["history"][0]["state"] == "submitted"
    harness.backend.cancel(h)


def test_a_complete_job_moves_submitted_running_complete_and_returns_its_record(harness, tmp_path):
    spec = _spec(tmp_path)
    with harness.running():
        h = harness.backend.submit(spec)
        status = _finish(harness.backend, h)
    assert status.state is S.COMPLETE and status.message is None
    st = _check_state_file(h, spec, harness.name)
    assert _history(h) == ["submitted", "running", "complete"] and status.updated_at == st["updated_at"]
    rec = harness.backend.result(h)
    assert rec == _check_terminal_record(h, spec, "complete")
    assert rec.run_id == spec.run_id  # the job's run_id, handed to `smeltery run --run-id`, not the input digest's
    assert len(rec.ext["smeltery"]["input_digest"]) == 64
    assert list(Path(h.run_dir).glob("run-*.json"))  # the run's own record, beside the core one


def test_a_job_that_fails_ends_failed_with_a_minimal_record(harness, tmp_path):
    spec = _spec(tmp_path, BROKEN)
    with harness.running():
        h = harness.backend.submit(spec)
        status = _finish(harness.backend, h)
    assert status.state is S.FAILED and "not_a_section" in (status.message or "")
    _check_state_file(h, spec, harness.name)
    assert _history(h) == ["submitted", "running", "failed"]
    rec = harness.backend.result(h)  # a failed job's record is a result too: the job is terminal
    assert rec == _check_terminal_record(h, spec, "failed")
    assert rec.started_at is not None and rec.finished_at is not None


def test_result_before_the_job_finishes_raises_not_terminal(harness, tmp_path, fake):
    fake.set("ethanol pose 0", "vacuum", "hang")
    spec = _spec(tmp_path)
    with harness.running():
        h = harness.backend.submit(spec)
        with pytest.raises(schema.JobNotTerminalError):
            harness.backend.result(h)
        harness.backend.cancel(h)
    assert not jobs.record_path(h.run_dir).exists() or schema.read(h.run_dir).status == "cancelled"


def test_cancelling_a_running_job_kills_it_and_a_second_cancel_changes_nothing(harness, tmp_path, fake):
    fake.set("propanol pose 1", "vacuum", "hang")
    spec = _spec(tmp_path)
    with harness.running():
        h = harness.backend.submit(spec)
        pid = _wait(fake.hanging_pid, what="the SCF hanging")
        _wait(lambda: harness.backend.status(h).state is S.RUNNING, what="the job running")
        first = harness.backend.cancel(h)
        assert first.state is S.CANCELLED
        again = harness.backend.cancel(h)
        assert again == first  # idempotent: the same status, not a new transition
        _wait(lambda: not _alive(pid), timeout=30, what="the cancelled SCF dying")
    _check_state_file(h, spec, harness.name)
    assert _history(h) == ["submitted", "running", "cancelled"]
    rec = _check_terminal_record(h, spec, "cancelled")
    assert rec.started_at == _state(h)["history"][1]["at"]
    assert harness.backend.status(h) == first


def test_cancelling_a_job_that_never_started_goes_straight_to_cancelled(harness, tmp_path, fake):
    spec = _spec(tmp_path)
    with harness.held(tmp_path, fake):
        h = harness.backend.submit(spec)
        assert harness.backend.status(h).state is S.SUBMITTED
        assert harness.backend.cancel(h).state is S.CANCELLED
    _check_state_file(h, spec, harness.name)
    assert _history(h) == ["submitted", "cancelled"]
    rec = _check_terminal_record(h, spec, "cancelled")
    assert rec.started_at is None  # it never ran
    with harness.running():  # a worker that turns up later does not run it
        time.sleep(1.0)
        assert harness.backend.status(h).state is S.CANCELLED
    assert not list(Path(h.run_dir).glob("run-*.json"))


def test_cancelling_a_finished_job_returns_its_status_and_touches_nothing(harness, tmp_path):
    spec = _spec(tmp_path)
    with harness.running():
        h = harness.backend.submit(spec)
        done = _finish(harness.backend, h)
    before = (jobs.state_path(h.run_dir).read_bytes(), jobs.record_path(h.run_dir).read_bytes())
    assert harness.backend.cancel(h) == done
    assert (jobs.state_path(h.run_dir).read_bytes(), jobs.record_path(h.run_dir).read_bytes()) == before


def test_a_crashed_worker_ends_the_job_failed_with_a_record(harness, tmp_path, fake):
    fake.set("ethanol pose 1", "field", "hang")
    spec = _spec(tmp_path)
    with harness.running():
        h = harness.backend.submit(spec)
        pid = _wait(fake.hanging_pid, what="the SCF hanging")
        harness.crash(h)
        status = harness.backend.status(h)
    assert status.state is S.FAILED and "supervisor" in (status.message or "")
    _wait(lambda: not _alive(pid), timeout=30, what="the orphaned run dying")
    _check_state_file(h, spec, harness.name)
    assert _history(h) == ["submitted", "running", "failed"]
    _check_terminal_record(h, spec, "failed")


def test_a_run_directory_holding_a_run_is_refused(harness, tmp_path):
    spec = _spec(tmp_path)
    with harness.running():
        h = harness.backend.submit(spec)
        _finish(harness.backend, h)
    with pytest.raises(schema.JobExistsError):
        harness.backend.submit(spec)
    with pytest.raises(schema.JobExistsError):  # nor is a finished run's config re-staged by building a new spec
        jobs.campaign_spec(_config(tmp_path, TINY + "\n# changed\n"), h.run_dir)
    assert "# changed" not in (Path(h.run_dir) / "config.toml").read_text()


def test_an_unknown_handle_is_not_found(harness, tmp_path):
    ghost = schema.JobHandle(harness.name, "0" * 12, "ghost", str(tmp_path / "nowhere"))
    for call in (harness.backend.status, harness.backend.cancel, harness.backend.result):
        with pytest.raises(schema.JobNotFoundError):
            call(ghost)
    with pytest.raises(schema.JobNotFoundError):  # a handle of the other backend is not this one's
        harness.backend.status(schema.JobHandle("elsewhere", "x", "ghost", str(tmp_path)))


@pytest.mark.parametrize(
    "change, match",
    [
        ({"family": "other"}, "family must be the config's campaign"),
        ({"arm": "a"}, "no arm"),
        ({"ext": {"x": {}}}, "no ext"),
        ({"run_id": ""}, "run_id"),
    ],
)
def test_a_spec_smeltery_cannot_record_honestly_is_refused_before_anything_is_written(harness, tmp_path, change, match):
    import dataclasses

    spec = dataclasses.replace(_spec(tmp_path), **change)
    with pytest.raises(ValueError, match=match):
        harness.backend.submit(spec)
    assert not jobs.state_path(spec.run_dir).exists()


# ------------------------------------------------------------------------------------------------ state.json itself


def _job_spec(run: Path, run_id: str = "r") -> schema.JobSpec:
    return schema.JobSpec(run_id, "tiny", None, {"campaign": "tiny"}, str(run))


def test_transitions_follow_the_state_machine_and_a_refused_one_writes_nothing(tmp_path):
    run = tmp_path / "run"
    jobs.init_state(run, run_id="r", backend="local", job_id="j")
    assert jobs.IllegalTransition is schema_state.IllegalTransitionError
    with pytest.raises(jobs.IllegalTransition):  # nothing completes without running
        jobs.transition(run, S.COMPLETE)
    jobs.transition(run, S.RUNNING)
    with pytest.raises(jobs.IllegalTransition):  # ending a job is settle's, which also writes its record
        jobs.transition(run, S.FAILED, "boom")
    jobs.settle(run, _job_spec(run), S.FAILED, "boom", observer=True)
    for new in S:
        before = jobs.state_path(run).read_bytes()
        with pytest.raises(jobs.IllegalTransition):  # terminal: no exits
            jobs.transition(run, new)
        assert jobs.state_path(run).read_bytes() == before
    st = jobs.read_state(run)
    assert [h.state for h in st.history] == [S.SUBMITTED, S.RUNNING, S.FAILED] and st.message == "boom"
    rec = schema.read(run)
    assert rec.finished_at == st.history[-1].at and rec.ext[jobs.EXT] == {
        "backend": "local",
        "job_id": "j",
        "stages_completed": None,
    }
    with pytest.raises(schema.JobExistsError):
        jobs.init_state(run, run_id="r", backend="local", job_id="j2")


@pytest.mark.parametrize(
    "edit",
    [
        lambda st: st["history"].insert(1, {"state": "complete", "at": st["history"][0]["at"]}),
        lambda st: st.update(state="running"),
        lambda st: st.update(extra=1),
        lambda st: st.update(schema="attestation.state/2"),
    ],
    ids=["illegal-history", "state-not-last", "unknown-key", "other-schema"],
)
def test_a_state_file_breaking_the_format_is_refused_not_guessed(tmp_path, edit):
    run = tmp_path / "run"
    jobs.init_state(run, run_id="r", backend="local", job_id="j")
    jobs.transition(run, S.RUNNING)
    jobs.settle(run, _job_spec(run), S.FAILED, "x", observer=True)
    st = json.loads(jobs.state_path(run).read_bytes())
    edit(st)
    jobs.state_path(run).write_text(json.dumps(st))
    with pytest.raises(schema_state.StateFormatError) as err:
        jobs.read_state(run)
    assert isinstance(err.value, ValueError)  # what callers catch


def _foreign_record(run: Path, spec: schema.JobSpec, status, **change) -> None:
    import dataclasses

    rec = schema_state.terminal_record(
        dataclasses.replace(spec, **change),
        status,
        producer=schema.Producer("elsewhere", None),
        started_at=None,
        finished_at=schema.format_timestamp(datetime.now(UTC)),
        data=[],
    )
    schema.write(rec, run)


def test_settle_follows_a_record_already_written_so_state_and_record_agree(tmp_path):
    """A cancel racing completion: the run wrote its record first, so the job ends as that record says."""
    run = tmp_path / "run"
    spec = _job_spec(run)
    jobs.init_state(run, run_id="r", backend="local", job_id="j")
    jobs.transition(run, S.RUNNING)
    _foreign_record(run, spec, S.FAILED)
    st = jobs.settle(run, spec, S.CANCELLED, "cancelled", observer=True)
    assert st.state is S.FAILED and "its core record says failed" in st.message
    assert jobs.settle(run, spec, S.CANCELLED, "again", observer=True) == st  # terminal: settled once


@pytest.mark.parametrize(
    "change, why",
    [({"run_id": "another"}, "run_id is 'another'"), ({"config": {"campaign": "tiny", "x": 1}}, "config is not")],
)
def test_a_record_that_is_not_the_jobs_ends_it_failed(tmp_path, change, why):
    """A run that wrote its record under its own run_id (or another config) is not this job's record: failed."""
    run = tmp_path / "run"
    spec = _job_spec(run)
    jobs.init_state(run, run_id="r", backend="local", job_id="j")
    jobs.transition(run, S.RUNNING)
    _foreign_record(run, spec, S.CANCELLED, **change)
    st = jobs.settle(run, spec, S.COMPLETE, None, observer=False)
    assert st.state is S.FAILED and "not this job's" in st.message and why in st.message


# ------------------------------------------------------------------------------------------------ local only


def test_the_local_worker_settles_a_crashed_job_nobody_asked_about(tmp_path, fake):
    from smeltery.services import local_worker

    h0 = LocalHarness(tmp_path)
    try:
        fake.set("ethanol pose 0", "vacuum", "hang")
        h = h0.backend.submit(_spec(tmp_path))
        _wait(fake.hanging_pid, what="the SCF hanging")
        h0.crash(h)
        assert _state(h)["state"] == "running"  # nothing has looked yet
        ticks = iter(range(3))
        local_worker(tmp_path / "ws", 1, interval_s=0.05, stop=lambda: next(ticks, None) is None)
        assert _state(h)["state"] == "failed" and schema.read(h.run_dir).status == "failed"
    finally:
        h0.close()


def test_local_jobs_share_benchs_task_queue_and_its_run_slots(tmp_path, fake):
    h0 = LocalHarness(tmp_path)
    try:
        fake.set("ethanol pose 0", "vacuum", "hang")
        h = h0.backend.submit(_spec(tmp_path))
        task = _wait(lambda: h0.backend.task_of(h), what="the job's task")
        from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

        bench = SubprocessTaskQueue(tmp_path / "ws" / ".smeltery" / "bench" / "tasks")
        assert bench.get(task["id"]).name == f"job {h.run_id}"
        h0.backend.cancel(h)
    finally:
        h0.close()


def test_the_cli_submits_and_reports_a_job_with_the_default_runner(tmp_path, capsys, monkeypatch):
    """`smeltery submit/status/result/cancel`, through `python -m smeltery.jobs _run` (no fake engine: the config
    fails validation before any SCF)."""
    monkeypatch.setenv("SMELTERY_ROOT", str(tmp_path / "ws"))
    monkeypatch.delenv("SMELTERY_BACKEND", raising=False)
    cfg, out = _config(tmp_path, BROKEN), tmp_path / "run"
    assert smeltery_main(["submit", str(cfg), "--out", str(out), "--run-id", "cli-job"]) == 0
    assert "submitted local job" in capsys.readouterr().out
    _wait(lambda: smeltery_main(["status", str(out)]) == 0 and "failed" in capsys.readouterr().out, what="failed")
    assert smeltery_main(["result", str(out)]) == 1
    assert capsys.readouterr().out.splitlines() == ["failed", str(out.resolve() / "attestation" / "run.json")]
    assert smeltery_main(["cancel", str(out), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["state"] == "failed"
    assert smeltery_main(["submit", str(cfg), "--out", str(out)]) == 2
    assert "already holds a run" in capsys.readouterr().err
    assert smeltery_main(["status", str(tmp_path)]) == 2  # no job there
