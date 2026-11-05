"""`smeltery run --temporal`: the pipeline as a durable Temporal workflow, and the RHF remedy ladder.

The SCFs are a fake `ferric` (`tests/fake_ferric.py`; no engine runs): its energies are a deterministic function of
the geometry and the field, it fails to converge or hangs on demand, and it serves both this process (a plain run)
and the score child processes the Temporal worker spawns, so both compute the same numbers. The workflow tests need
the Temporal dev server at 127.0.0.1:7233; each uses its own task queue and an in-process worker (as
`smeltery temporal-worker` runs it), and terminates what it started.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import socket
import sys
import threading
import time
import types
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("temporalio", reason="temporalio (the `temporal` extra) is not installed")

import fake_ferric  # noqa: E402
from golden_path import results_without_timing  # noqa: E402

from smeltery import cli, temporal_run  # noqa: E402
from smeltery.cli import ConfigError, main, validate_config  # noqa: E402
from smeltery.methods import MethodSpec, register, unregister  # noqa: E402
from smeltery.model import HARTREE_TO_KCAL, PointCharge  # noqa: E402
from smeltery.tiers import FieldInteraction  # noqa: E402

HOST = "127.0.0.1:7233"
FAKE_CHILD = [sys.executable, str(Path(fake_ferric.__file__).resolve())]

TINY = """
campaign = "tiny"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"

[poses]
n_poses = {n_poses}
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


def _server_up() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", 7233), timeout=1):
            return True
    except OSError:
        return False


needs_server = pytest.mark.skipif(not _server_up(), reason="no Temporal dev server at 127.0.0.1:7233")


# ------------------------------------------------------------------------------------------------ the fake engine


class Fake:
    """The fake ferric's controls: `rules[(label, leg)]`, saved for the child processes; what ran, read back."""

    def __init__(self, d: Path):
        self.dir = d
        self.rules: dict[tuple[str, str], str] = {}
        self.save()

    def save(self) -> None:
        cfg = {"rules": {f"{k[0]}|{k[1]}": v for k, v in self.rules.items()}, "dir": str(self.dir)}
        (self.dir / "cfg.json").write_text(json.dumps(cfg))

    def set(self, label: str, leg: str, rule: str | None) -> None:
        if rule is None:
            self.rules.pop((label, leg), None)
        else:
            self.rules[(label, leg)] = rule
        self.save()

    def calls(self) -> list[dict]:
        p = self.dir / "calls.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []

    def pids(self) -> list[int]:
        p = self.dir / "pids"
        return [int(x) for x in p.read_text().split()] if p.is_file() else []


@pytest.fixture
def ferric(tmp_path, monkeypatch):
    d = tmp_path / "fake"
    d.mkdir()
    monkeypatch.setenv("FAKE_FERRIC", str(d / "cfg.json"))
    fake = Fake(d)
    monkeypatch.setitem(sys.modules, "ferric", fake_ferric.install())
    return fake


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    with contextlib.suppress(OSError):  # a zombie is dead: it only waits to be reaped
        if Path(f"/proc/{pid}/stat").read_text().split()[2] == "Z":
            return False
    return True


def _config(tmp_path: Path, n_poses: int = 2, extra: str = "", name: str = "c.toml") -> Path:
    p = tmp_path / name
    p.write_text(TINY.format(n_poses=n_poses) + extra)
    return p


def _record(out: Path) -> dict:
    (path,) = out.glob("run-*.json")
    return json.loads(path.read_text())


def _rows(out: Path) -> list[dict]:
    return [json.loads(x) for x in (out / "calcs.jsonl").read_text().splitlines() if x.strip()]


@contextlib.contextmanager
def worker(task_queue: str, workers: int = 2, threads: int = 1):
    """A pipeline worker on its own thread and event loop, as `smeltery temporal-worker` runs it, its score children
    running the fake engine."""
    loop = asyncio.new_event_loop()
    ready, box = threading.Event(), {}

    async def run():
        box["stop"] = asyncio.Event()
        client = await temporal_run.connect(HOST)
        ready.set()
        await temporal_run.run_worker(client, task_queue, workers, threads, box["stop"], FAKE_CHILD)

    th = threading.Thread(target=loop.run_until_complete, args=(run(),), daemon=True)
    th.start()
    if not ready.wait(30):
        raise RuntimeError("the test worker did not start")
    try:
        yield
    finally:
        loop.call_soon_threadsafe(box["stop"].set)
        th.join(60)


def _terminate(out: Path) -> None:
    """Terminate the run's workflow if it is still running (a failed test must not leave one behind)."""

    async def go():
        client = await temporal_run.connect(HOST)
        wid = temporal_run.workflow_id(out, (out / "config.toml").read_text())
        with contextlib.suppress(Exception):
            await client.get_workflow_handle(wid).terminate("test teardown")

    if (out / "config.toml").is_file():
        asyncio.run(go())


def _staged(cfg: Path, out: Path, heartbeat_s: float = 3.0, workers: int = 1) -> temporal_run.RunInput:
    _, info = cli._stage_config(cfg, out)
    staged = out / "config.toml"
    return temporal_run.RunInput(
        str(staged), str(out), workers, info, heartbeat_s=heartbeat_s, config_sha=temporal_run.sha256_file(staged)
    )


async def _start(inp, queue):
    client = await temporal_run.connect(HOST)
    return await client.start_workflow(
        temporal_run.PipelineRunWorkflow.run,
        inp,
        id=temporal_run.workflow_id(inp.out, Path(inp.config).read_text()),
        task_queue=queue,
    )


def _finish(inp, queue):
    async def go():
        client = await temporal_run.connect(HOST)
        return await temporal_run.start_and_wait(client, inp, queue, poll_s=1, log=lambda *_: None)

    return asyncio.run(go())


def _wait_for(path: Path, timeout: float = 60.0) -> None:
    t0 = time.monotonic()
    while not path.exists():
        if time.monotonic() - t0 > timeout:
            raise TimeoutError(f"{path} never appeared")
        time.sleep(0.1)


@pytest.fixture
def queue():
    return f"smeltery-pipeline-test-{uuid.uuid4().hex[:8]}"


# ------------------------------------------------------------------------------------------------ the remedy ladder


def _water():
    from smeltery.model import Candidate, Pose

    c = Candidate("m", "O")
    c.poses = [Pose(("O", "H", "H"), np.array([[0.0, 0, 0], [0.96, 0, 0], [-0.24, 0.93, 0]]))]
    return c


def test_the_ladder_climbs_when_an_scf_does_not_converge(ferric):
    """Rung 0 does not converge, rung 1 (a level shift) does: the value is rung 1's, and the rung is recorded."""
    ferric.set("m pose 0", "field", "needs:level_shift")
    tier = FieldInteraction(remedies=[{"level_shift": 0.5}, {"max_iter": 500, "soscf": True, "diis": "adiis"}])
    c = _water()
    tier.run([c], {"field": [PointCharge(1.0, (3.0, 0.0, 0.0))]})
    assert c.per_pose["scf_rung"] == [1.0]
    legs = [(r["leg"], r["rung"], r["converged"]) for r in tier.calc_log]
    assert legs == [("vacuum", 0, True), ("field", 0, False), ("field", 1, True)]
    last = ferric.calls()[-1]["kw"]
    assert last["level_shift"] == 0.5 and last["energy_conv"] == 1e-10
    assert tier.produces() == {"dE_int": "kcal/mol", "scf_rung": "rung"}
    assert tier.settings()["remedies"] == [{"level_shift": 0.5}, {"max_iter": 500, "soscf": True, "diis": "adiis"}]


def test_every_rung_failing_still_raises_and_logs_every_scf(ferric):
    ferric.set("m pose 0", "vacuum", "never")
    tier = FieldInteraction(remedies=[{"level_shift": 0.5}])
    with pytest.raises(RuntimeError, match="did not converge"):
        tier.run([_water()], {"field": [PointCharge(1.0, (3.0, 0.0, 0.0))]})
    assert [(r["leg"], r["rung"]) for r in tier.calc_log] == [("vacuum", 0), ("vacuum", 1), ("field", 0)]


@pytest.mark.needs_ferric
def test_every_remedy_knob_is_one_real_ferric_accepts():
    """Against the real engine on H2 in a field: a rung setting every allowed knob, with each DIIS flavour, is
    accepted by `run_rhf` and converges to the base settings' energy (both legs)."""
    import ferric

    from smeltery.model import Pose

    rungs = [
        {"max_iter": 100, "diis": d, "diis_size": 8, "level_shift": 0.0, "soscf": False}
        for d in ("pulay", "adiis", "ediis")
    ]
    tier = FieldInteraction(remedies=rungs)
    pose = Pose(("H", "H"), np.array([[0.0, 0.0, 0.0], [0.0, 0.0, 0.74]]))
    mol = ferric.Molecule.from_xyz_string(pose.to_xyz("h2"), charge=0)
    bs = ferric.BasisSet.bundled(tier.basis)
    charges = [PointCharge(0.5, (0.0, 3.0, 0.0)).as_ferric_bohr()]
    for q in (None, charges):
        energies = []
        for rung in range(tier.n_rungs):
            res, _ = tier.scf(ferric, mol, bs, q, rung)
            assert res.converged, (rung, q)
            energies.append(res.energy)
        assert max(energies) - min(energies) < 1e-8


def test_no_ladder_is_the_tier_unchanged():
    """An empty ladder adds nothing to the settings (so the digest) or the record."""
    t = FieldInteraction()
    assert "remedies" not in t.settings() and t.produces() == {"dE_int": "kcal/mol"} and t.n_rungs == 1


@pytest.mark.parametrize(
    "rung, match",
    [
        ({"energy_conv": 1e-6}, "changes the precision"),
        ({"density_conv": 1e-5}, "changes the precision"),
        ({"smearing": 0.01}, "fractional occupations"),
        ({"max_iterz": 5}, "unknown override"),
        ({"max_iter": 0}, "not a valid int"),
        ({"diis": True}, "is not one of"),
        ({"diis": "anderson"}, "is not one of"),
        ({"soscf": 1}, "not a valid bool"),
        ({}, "non-empty table"),
    ],
)
def test_a_bad_remedy_is_a_config_error(tmp_path, rung, match):
    import tomllib

    cfg = tomllib.loads(TINY.format(n_poses=2))
    with pytest.raises(ConfigError, match=match):
        validate_config({**cfg, "field_interaction": {"remedies": [rung]}})
    ok = [{"level_shift": 0.5, "diis_size": 12}, {"diis": "adiis", "max_iter": 300, "soscf": True}]
    validate_config({**cfg, "field_interaction": {"remedies": ok}})


def test_run_scf_timeout_is_validated_and_survives_the_pipeline_round_trip():
    import tomllib

    from smeltery.gui.pipeline import Pipeline

    cfg = tomllib.loads(TINY.format(n_poses=2))
    assert cli.scf_timeout_s(cfg) == 14400.0
    validate_config({**cfg, "run": {"scf_timeout_s": 60}})
    assert cli.scf_timeout_s({**cfg, "run": {"scf_timeout_s": 60}}) == 60.0
    for bad in (0, -1, "1h", True):
        with pytest.raises(ConfigError, match="scf_timeout_s"):
            validate_config({**cfg, "run": {"scf_timeout_s": bad}})
    with pytest.raises(ConfigError, match=r"unknown key\(s\) \['timeout'\] in \[run\]"):
        validate_config({**cfg, "run": {"timeout": 60}})
    p = Pipeline.from_toml(TINY.format(n_poses=2) + "\n[run]\nscf_timeout_s = 60\n")
    assert p.to_config()["run"] == {"scf_timeout_s": 60}


# ------------------------------------------------------------------------------------------------ the log


def test_a_torn_last_line_is_skipped_and_reported_and_the_next_row_is_whole(tmp_path):
    row = {"candidate": "a", "pose": 0, "leg": "vacuum", "field_model": None, "rung": 0, "digest": "d" * 12}
    (tmp_path / "calcs.jsonl").write_text(json.dumps(row) + "\n" + '{"candidate": "a", "po')
    rows, torn = temporal_run.read_rows(tmp_path, "d" * 64)
    assert rows == [row] and torn == 1
    temporal_run.append_row(tmp_path, {**row, "pose": 1})
    rows, torn = temporal_run.read_rows(tmp_path, "d" * 64)
    assert rows == [row, {**row, "pose": 1}] and torn == 1


def test_duplicate_scf_lines_are_dropped_keeping_the_first():
    a = {"candidate": "a", "pose": 0, "leg": "vacuum", "field_model": None, "rung": 0, "energy_hartree": -1.0}
    b = {**a, "energy_hartree": -2.0}
    o = {"candidate": "a", "pose": 0, "leg": "vacuum", "field_model": None, "outcome": "error"}
    assert temporal_run.dedupe([a, b, o, o, {**a, "rung": 1}]) == ([a, o, o, {**a, "rung": 1}], 1)


def test_a_changed_config_is_refused_by_the_activities(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("a = 1\n")
    sha = temporal_run.sha256_file(p)
    temporal_run.check_config(str(p), sha)
    p.write_text("a = 2\n")
    with pytest.raises(ValueError, match="changed under a running workflow"):
        temporal_run.check_config(str(p), sha)


# ------------------------------------------------------------------------------------------------ the workflow


@needs_server
def test_a_temporal_run_records_what_a_plain_run_records(ferric, tmp_path, queue, capsys):
    cfg = _config(tmp_path)
    plain, durable = tmp_path / "plain", tmp_path / "durable"
    assert main(["run", str(cfg), "--out", str(plain)]) == 0
    n_plain = len(ferric.calls())
    try:
        with worker(queue):
            assert main(["run", str(cfg), "--out", str(durable), "--temporal", "--task-queue", queue]) == 0
    finally:
        _terminate(durable)
    assert len(ferric.calls()) == 2 * n_plain == 16  # the same SCFs, once each (one field model: nothing shared)
    assert all(c["pid"] != os.getpid() for c in ferric.calls()[n_plain:])  # each in a child process
    a, b = _record(plain), _record(durable)
    assert a["input_digest"] == b["input_digest"]
    ra, rb = results_without_timing(a["results"]), results_without_timing(b["results"])
    ca, cb = ra.pop("calcs"), rb.pop("calcs")
    assert ra == rb and ca["n"] == cb["n"] == 8
    assert cb["by_model"] == {"primary": 4, "shared_vacuum": 4}
    rows = _rows(durable)
    assert {(r["leg"], r["field_model"]) for r in rows} == {("vacuum", None), ("field", "primary")}
    assert all(r["rung"] == 0 and r["converged"] for r in rows)
    assert "run record:" in capsys.readouterr().out


@needs_server
def test_a_pose_failing_every_rung_is_dropped_pairwise(ferric, tmp_path, queue):
    """The parent's pose 0 fails every rung: pose 0 goes for every analogue, though propanol's converged. Propanol's
    pose 1 fails: it goes for propanol only, and the parent's pose 1 is not in propanol's ddE. The parent's pose 2
    converges on rung 1."""
    cfg = _config(tmp_path, n_poses=4, extra="\n[field_interaction]\nremedies = [{level_shift = 0.5}]\n")
    ferric.set("ethanol pose 0", "field", "never")
    ferric.set("ethanol pose 2", "field", "needs:level_shift")
    ferric.set("propanol pose 1", "vacuum", "never")
    out = tmp_path / "out"
    try:
        with worker(queue):
            assert main(["run", str(cfg), "--out", str(out), "--temporal", "--task-queue", queue]) == 0
    finally:
        _terminate(out)
    res = _record(out)["results"]
    assert res["pose_index"] == {"ethanol": [1, 2, 3], "propanol": [2, 3]}
    drops = {(d["candidate"], d["pose"]): d for d in res["dropped_poses"]}
    assert set(drops) == {("ethanol", 0), ("propanol", 1)}
    assert drops[("ethanol", 0)]["rungs_tried"] == [0, 1] and drops[("ethanol", 0)]["leg"] == "field"
    assert drops[("ethanol", 0)]["outcome"] == "not_converged"
    assert drops[("ethanol", 0)]["dropped_with"] == ["ethanol", "propanol"]
    assert drops[("propanol", 1)]["dropped_with"] == ["ethanol", "propanol"]
    assert drops[("propanol", 1)]["rungs_tried"] == [0, 1] and drops[("propanol", 1)]["leg"] == "vacuum"
    assert drops[("propanol", 1)]["field_model"] is None
    assert res["per_pose"]["ethanol"]["scf_rung"] == [0.0, 1.0, 0.0]
    # every SCF is in the log with its rung; a converged leg was not recomputed on the next rung
    scf = [r for r in _rows(out) if "outcome" not in r]
    e0 = sorted((r["leg"], r["rung"], r["converged"]) for r in scf if r["candidate"] == "ethanol" and r["pose"] == 0)
    assert e0 == [("field", 0, False), ("field", 1, False), ("vacuum", 0, True)]
    assert sum(1 for r in _rows(out) if r.get("outcome") == "not_converged") == 2
    e = {(r["candidate"], r["pose"], r["leg"]): r["energy_hartree"] for r in scf if r["converged"]}

    def de(c, i):
        return (e[(c, i, "field")] - e[(c, i, "vacuum")]) * HARTREE_TO_KCAL

    want = [de("propanol", i) - de("ethanol", i) for i in (2, 3)]
    assert res["paired_ddE"]["propanol"]["n"] == 2
    assert res["paired_ddE"]["propanol"]["per_pose"] == pytest.approx(want, abs=1e-9)


@needs_server
def test_a_killed_worker_resumes_without_recomputing(ferric, tmp_path, queue):
    """An SCF hangs; its worker is stopped mid-SCF (the child is killed with it) and a new worker takes over. No
    SCF that had finished runs again; a whole new workflow run on the same OUT reads everything back."""
    out = tmp_path / "out"
    inp = _staged(_config(tmp_path), out)
    ferric.set("propanol pose 0", "vacuum", "hang_once")
    try:
        with worker(queue, workers=1):
            asyncio.run(_start(inp, queue))
            _wait_for(ferric.dir / "hanging")
            hung = int((ferric.dir / "hanging").read_text())
            done_before = [(c["label"], c["leg"]) for c in ferric.calls()]
        time.sleep(0.5)
        assert not _alive(hung), "stopping the worker left its SCF child running"
        with worker(queue, workers=1):
            first = _finish(inp, queue)
            keys = [(c["label"], c["leg"]) for c in ferric.calls()]
            assert len(keys) == len(set(keys)) == 8  # every SCF once: none of the finished ones again
            assert keys[: len(done_before)] == done_before and ("propanol pose 0", "vacuum") not in done_before
            again = _finish(inp, queue)  # a new run of the same workflow id: everything is read back
            assert len(ferric.calls()) == 8 and again.n_resumed == 8
            per_pose = json.loads(Path(first.record).read_text())["results"]["per_pose"]
            assert json.loads(Path(again.record).read_text())["results"]["per_pose"] == per_pose
    finally:
        _terminate(out)


@needs_server
def test_a_timed_out_leg_is_killed_recorded_not_retried_and_rescheduled_by_a_rerun(ferric, tmp_path, queue):
    """A hanging SCF is ended at `[run] scf_timeout_s`: its child killed, no row written, not retried (outcome
    "timeout" with its rung and seconds). finalize refuses, naming the remedy; a rerun with a longer timeout (same
    input digest) reschedules that leg only."""
    from temporalio.client import WorkflowFailureError

    out = tmp_path / "out"
    inp = _staged(_config(tmp_path, extra="\n[run]\nscf_timeout_s = 2\n"), out, heartbeat_s=2.0, workers=2)
    ferric.set("propanol pose 1", "field", "hang")
    try:
        with worker(queue, workers=2):
            with pytest.raises(WorkflowFailureError) as e:
                _finish(inp, queue)
            msg = str(e.value.cause.cause)
            assert "propanol pose 1 field (primary) timed out after 2 s at rung 0" in msg
            assert "Raise [run] scf_timeout_s (it does not change the input digest) and run the same command" in msg
            time.sleep(0.5)
            hung = [c for c in ferric.pids() if not any(x["pid"] == c for x in ferric.calls())]
            assert len(hung) == 1 and not _alive(hung[0])  # one attempt, its child killed
            assert ("propanol pose 1", "field") not in {(c["label"], c["leg"]) for c in ferric.calls()}
            assert not list(out.glob("run-*.json"))
            (t,) = [r for r in _rows(out) if "outcome" in r]
            assert t["outcome"] == "timeout" and t["rung"] == 0 and t["seconds"] == 2.0
            n_before = len(ferric.calls())
            ferric.set("propanol pose 1", "field", None)
            longer = _staged(_config(tmp_path, extra="\n[run]\nscf_timeout_s = 60\n", name="c2.toml"), out)
            s = _finish(longer, queue)
            assert len(ferric.calls()) == n_before + 1  # only the timed-out leg ran again
            assert s.digest == _record(out)["input_digest"] and "dropped_poses" not in _record(out)["results"]
    finally:
        _terminate(out)


@needs_server
def test_a_leg_that_keeps_failing_is_an_error_after_three_attempts_naming_its_last_line(ferric, tmp_path, queue):
    from temporalio.client import WorkflowFailureError

    out = tmp_path / "out"
    inp = _staged(_config(tmp_path), out, workers=2)
    ferric.set("ethanol pose 1", "vacuum", "crash")
    try:
        with worker(queue, workers=2):
            with pytest.raises(WorkflowFailureError) as e:
                _finish(inp, queue)
        msg = str(e.value.cause.cause)
        assert "ethanol pose 1 vacuum:" in msg and "RuntimeError: fake engine crashed" in msg
        assert "rescheduled (chemistry failures are not)" in msg
        assert sum(1 for p in ferric.pids()) - len(ferric.calls()) == 3  # three attempts
        # its pose's field leg was not run: the vacuum leg failed
        assert ("ethanol pose 1", "field") not in {(c["label"], c["leg"]) for c in ferric.calls()}
        (o,) = [r for r in _rows(out) if "outcome" in r]
        assert o["outcome"] == "error" and o["leg"] == "vacuum"
    finally:
        _terminate(out)


@needs_server
def test_a_vacuum_leg_failing_every_rung_skips_its_field_legs_and_is_one_drop(ferric, tmp_path, queue, half):
    out = tmp_path / "out"
    cfg = _config(tmp_path, n_poses=3, extra="\n[half_field]\n")
    ferric.set("propanol pose 1", "vacuum", "never")
    try:
        with worker(queue):
            assert main(["run", str(cfg), "--out", str(out), "--temporal", "--task-queue", queue]) == 0
    finally:
        _terminate(out)
    assert ("propanol pose 1", "field") not in {(c["label"], c["leg"]) for c in ferric.calls()}
    res = _record(out)["results"]
    (d,) = res["dropped_poses"]
    assert (d["candidate"], d["pose"], d["leg"], d["field_model"]) == ("propanol", 1, "vacuum", None)
    assert res["pose_index"] == {"ethanol": [0, 1, 2], "propanol": [0, 2]}
    (measured,) = half[1].summaries
    assert measured["HALF"]["propanol"][1] is None


def test_a_log_from_before_per_leg_scores_is_refused_cleanly(tmp_path):
    old = {"candidate": "a", "pose": 0, "field_model": "primary", "outcome": "not_converged", "digest": "d" * 12}
    (tmp_path / "calcs.jsonl").write_text(json.dumps(old) + "\n")
    with pytest.raises(ValueError, match=r"before per-leg scores \(7bdaebc\).*use a fresh --out"):
        temporal_run.read_rows(tmp_path, "d" * 64)


def test_restaging_an_unchanged_config_writes_nothing(tmp_path):
    cfg, out = _config(tmp_path), tmp_path / "out"
    cli._stage_config(cfg, out)
    stamps = {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()}
    time.sleep(0.05)
    cli._stage_config(cfg, out)
    assert {p: p.stat().st_mtime_ns for p in out.rglob("*") if p.is_file()} == stamps


@needs_server
def test_restaging_over_a_running_workflow_is_refused(ferric, tmp_path, queue, capsys):
    out = tmp_path / "out"
    cfg = _config(tmp_path)
    ferric.set("ethanol pose 0", "vacuum", "hang")
    try:
        with worker(queue, workers=1):

            def run_temporal():
                main(["run", str(cfg), "--out", str(out), "--temporal", "--task-queue", queue])

            th = threading.Thread(target=run_temporal, daemon=True)
            th.start()
            _wait_for(ferric.dir / "hanging")
            capsys.readouterr()
            changed = _config(tmp_path, extra="\n[run]\nscf_timeout_s = 99\n", name="c2.toml")
            staged = (out / "config.toml").read_text()
            for argv in (["--plan"], []):
                assert main(["run", str(changed), "--out", str(out), *argv]) == 2
                assert "is still running" in capsys.readouterr().err
            assert main(["run", str(cfg), "--out", str(out)]) == 2  # a plain run of the same config too
            assert "is still running" in capsys.readouterr().err
            assert (out / "config.toml").read_text() == staged
    finally:
        _terminate(out)


def test_the_server_down_exits_2_naming_the_docker_command(ferric, tmp_path, capsys):
    cfg = _config(tmp_path)
    rc = main(["run", str(cfg), "--out", str(tmp_path / "out"), "--temporal", "--host", "127.0.0.1:1"])
    err = capsys.readouterr().err
    assert rc == 2 and temporal_run.DOCKER_COMMAND in err and "never falls back" in err
    assert not (tmp_path / "out" / "calcs.jsonl").exists() and not ferric.calls()  # nothing ran instead
    assert main(["temporal-worker", "--host", "127.0.0.1:1"]) == 2
    assert temporal_run.DOCKER_COMMAND in capsys.readouterr().err


def test_temporal_needs_an_out_dir(tmp_path, capsys):
    assert main(["run", str(_config(tmp_path)), "--temporal"]) == 2
    assert "--temporal needs --out" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ extra fields


@dataclass
class HalfKnobs:
    scale: float = 0.5


class HalfField:
    """An analysis that re-measures under the field with every charge scaled (`extra_fields`), and summarizes."""

    def __init__(self):
        self.run_calls, self.summaries = 0, []

    def run(self, plan, rec, ctx):
        self.run_calls += 1
        return {"ran": True}

    def extra_fields(self, plan, ctx):
        s = plan.cfg["half_field"].get("scale", 0.5)
        return {"HALF": [PointCharge(c.q * s, c.xyz_ang) for c in plan.field]}

    def summarize(self, plan, rec, measured):
        self.summaries.append(measured)
        return {"models": sorted(measured), "parent_mean": float(np.mean(measured["HALF"][plan.parent.name]))}


@pytest.fixture
def half():
    hook = HalfField()
    spec = register(
        MethodSpec(
            "half_field",
            "Half field",
            "ab_initio",
            "measure",
            step="measure",
            slot="analysis",
            section="half_field",
            knobs=HalfKnobs,
            order=151,
            needs=("dE_int",),
            hook=hook,
        )
    )
    yield spec, hook
    unregister("half_field")


@needs_server
def test_an_extra_fields_analysis_is_scored_and_summarized(ferric, tmp_path, queue, half):
    _, hook = half
    cfg = _config(tmp_path, extra="\n[half_field]\nscale = 0.5\n")
    out = tmp_path / "out"
    try:
        with worker(queue):
            assert main(["run", str(cfg), "--out", str(out), "--temporal", "--task-queue", queue]) == 0
    finally:
        _terminate(out)
    res = _record(out)["results"]
    assert hook.run_calls == 0 and len(hook.summaries) == 1  # summarized from the scores, not re-run
    (measured,) = hook.summaries
    assert set(measured) == {"HALF"} and set(measured["HALF"]) == {"ethanol", "propanol"}
    assert all(len(v) == 2 and all(isinstance(x, float) for x in v) for v in measured["HALF"].values())
    assert res["half_field"] == {"models": ["HALF"], "parent_mean": pytest.approx(np.mean(measured["HALF"]["ethanol"]))}
    rows = _rows(out)
    assert {(r["leg"], r["field_model"]) for r in rows} == {("vacuum", None), ("field", "primary"), ("field", "HALF")}
    # the vacuum leg is shared by the two models: 4 vacuum SCFs, 8 field SCFs, not 8 + 8
    legs = [c["leg"] for c in ferric.calls()]
    assert legs.count("vacuum") == 4 and legs.count("field") == 8 and len(rows) == 12
    assert _record(out)["results"]["calcs"]["by_model"] == {"shared_vacuum": 4, "primary": 4, "HALF": 4}
    # the half field's values differ from the primary's: the scores used their own field
    assert measured["HALF"]["ethanol"] != res["per_pose"]["ethanol"]["dE_int"]


def test_plan_counts_the_scf_work_including_extra_fields(ferric, tmp_path, half, capsys, monkeypatch):
    cfg = _config(tmp_path, extra="\n[half_field]\n")
    assert main(["run", str(cfg), "--plan"]) == 0
    out = capsys.readouterr().out
    assert (
        "SCF work: 8 scores = 2 poses x 2 candidates x 2 field model(s); "
        "12 SCFs under --temporal (one vacuum leg per pose, shared), 16 in a plain run"
    ) in out
    one = re.search(r"estimate ([0-9.]+) CPU-h under --temporal, ([0-9.]+) CPU-h plain", out)
    # the cheap `extra_models(cfg)`, when the hook has it, is what counts (no field is built for --plan)
    _, hook = half
    monkeypatch.setattr(hook, "extra_models", lambda cfg: ["HALF", "THIRD"], raising=False)
    monkeypatch.setattr(hook, "extra_fields", lambda plan, ctx: pytest.fail("--plan built a field"))
    assert main(["run", str(cfg), "--plan"]) == 0
    out = capsys.readouterr().out
    assert "SCF work: 12 scores = 2 poses x 2 candidates x 3 field model(s); 16 SCFs under --temporal" in out
    three = re.search(r"estimate ([0-9.]+) CPU-h under --temporal, ([0-9.]+) CPU-h plain", out)
    assert (one is None) == (three is None)
    if one is not None:  # the tier's calibration covers these elements: the hours scale with the SCF count
        assert float(three.group(1)) == pytest.approx(4 / 3 * float(one.group(1)), rel=0.05, abs=0.011)
        assert float(three.group(2)) == pytest.approx(1.5 * float(one.group(2)), rel=0.05, abs=0.011)


def test_a_serial_analysiss_scf_lines_move_into_the_one_calc_log(ferric, tmp_path, half, monkeypatch):
    """calcs.jsonl is the single per-SCF log: an analysis's per-model `calcs` go there, the record keeps a count."""
    _, hook = half
    extra = [{"candidate": "ethanol", "pose": 0, "leg": "field", "converged": True, "energy_hartree": -1.0}]
    monkeypatch.setattr(hook, "run", lambda plan, rec, ctx: {"HALF": {"value": 1.0, "calcs": list(extra)}})
    out = tmp_path / "out"
    assert main(["run", str(_config(tmp_path, extra="\n[half_field]\n")), "--out", str(out)]) == 0
    res = _record(out)["results"]
    assert res["half_field"] == {"HALF": {"value": 1.0, "calcs": {"n": 1}}}
    assert res["calcs"] == {"file": "calcs.jsonl", "n": 9, "primary": "primary", "by_model": {"primary": 8, "HALF": 1}}
    rows = _rows(out)
    assert rows[-1] == {**extra[0], "field_model": "HALF", "rung": 0}
    assert all("field_model" in r and "rung" in r for r in rows)


def test_a_remeasuring_analysis_is_refused_for_partial_and_combine(ferric, tmp_path, half, capsys):
    cfg = _config(tmp_path, extra="\n[half_field]\n")
    out = tmp_path / "out"
    assert main(["run", str(cfg), "--out", str(out), "--only-candidate", "propanol"]) == 2
    assert "re-measures every pose under other fields" in capsys.readouterr().err
    assert main(["run", str(cfg), "--out", str(out), "--combine"]) == 2
    assert "re-measures every pose under other fields" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------------ hook clashes


class _Poses:
    def parent_poses(self, cfg, parent, ctx):
        raise AssertionError("never reached: validate_config refuses first")


class _Field:
    def field(self, cfg, parent, ctx):
        raise AssertionError("never reached: validate_config refuses first")


@pytest.fixture
def hooks():
    specs = [
        MethodSpec(
            "given_poses",
            "Given poses",
            "geometry",
            "search",
            step="poses",
            slot="pose_source",
            section="given_poses",
            order=26,
            params=(),
            hook=_Poses(),
        ),
        MethodSpec(
            "given_field",
            "Given field",
            "input",
            "field",
            step="active_site",
            slot="field_source",
            section="given_field",
            order=46,
            params=(),
            hook=_Field(),
        ),
    ]
    for s in specs:
        register(s)
    yield
    for s in specs:
        unregister(s.key)


def test_a_pose_or_field_source_beside_structure_or_docking_is_refused_before_anything_runs(hooks):
    import tomllib

    base = tomllib.loads(TINY.format(n_poses=2))
    structure = {"spec": "x.pdb", "center": [0.0, 0.0, 0.0], "pocket_cutoff": 10.0}
    no_pocket = {k: v for k, v in base.items() if k not in ("pocket", "poses")}
    with pytest.raises(ConfigError, match=r"\[given_poses\] gives the parent's poses, and \[structure\] implies"):
        validate_config({**no_pocket, "structure": structure, "given_poses": {}})
    docking = {"receptor": "r.pdbqt", "box_center": [0.0, 0.0, 0.0]}
    with pytest.raises(ConfigError, match=r"\[given_poses\] gives the parent's poses, and \[docking\] gives"):
        validate_config({**base, "docking": docking, "given_poses": {}, "poses": {}})
    with pytest.raises(ConfigError, match=r"\[given_field\] gives the point-charge field, and \[structure\] supplies"):
        validate_config({**no_pocket, "structure": structure, "given_field": {}})


# ------------------------------------------------------------------------------------------------ Bench


def test_bench_launches_with_temporal_only_on_the_temporal_queue(tmp_path):
    from datetime import datetime

    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue, TemporalTaskQueue
    from smeltery.bench.use_cases.launch_run import LaunchRun
    from smeltery.gui.pipeline import Pipeline

    class Temporal(TemporalTaskQueue):
        def __init__(self):  # no server: only the command it is handed is looked at
            self.commands = []

        def submit(self, name, command, **kw):
            self.commands.append(command)
            return "t1"

    class Subprocess(SubprocessTaskQueue):
        def submit(self, name, command, **kw):
            self.commands = [command]
            return "t2"

    who = types.SimpleNamespace(current=lambda: "mg")
    clock = types.SimpleNamespace(now=lambda: datetime(2026, 11, 5))
    pipeline = Pipeline.from_toml(TINY.format(n_poses=2))
    for q, temporal in ((Temporal(), True), (Subprocess(tmp_path / "q"), False)):
        LaunchRun(q, None, None, who, clock, root=tmp_path).run(pipeline, out_dir=tmp_path / f"o{temporal}")
        (cmd,) = q.commands
        assert cmd[-1] == "--temporal" if temporal else "--temporal" not in cmd


@needs_server
def test_with_no_worker_polling_the_wait_says_so(ferric, tmp_path, queue):
    out = tmp_path / "out"
    inp = _staged(_config(tmp_path), out)
    said: list[str] = []

    async def go():
        client = await temporal_run.connect(HOST)
        await asyncio.wait_for(
            temporal_run.start_and_wait(client, inp, queue, poll_s=0.5, log=said.append, no_worker_after_s=0), 5
        )

    try:
        with pytest.raises(TimeoutError):
            asyncio.run(go())
    finally:
        _terminate(out)
    assert any("no worker is polling" in s and "smeltery temporal-worker" in s for s in said)
