"""Stage reuse (`smeltery.reuse`): a resubmitted campaign reuses the stages an identical earlier run completed.

Everything runs on the fake engine (`tests/fake_ferric.py`): every SCF it completes is a line in `calls.jsonl`, so
"nothing was recomputed" is a count of zero, not an inference from timings. Its build stamp (`ferric.__build__`) is
the test's to set, which is how a different or a dirty ferric build is simulated.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import sys
import time
from pathlib import Path

import fake_ferric
import pytest
from golden_path import results_without_timing
from test_cli import SMALL

from smeltery import reuse
from smeltery.cli import main

BUILD = {"git_sha": "a" * 40, "dirty": False}
RECHECK = "\n[recheck]\nscale = 1.0\n"
N_SCFS = 18  # SMALL: 3 candidates x 3 poses x (vacuum + field)


class Engine:
    """The fake engine's controls and its call log."""

    def __init__(self, d: Path) -> None:
        self.dir = d
        self.cfg: dict = {"rules": {}, "dir": str(d), "build": dict(BUILD), "register_recheck": True}
        self.save()

    def save(self) -> None:
        (self.dir / "cfg.json").write_text(json.dumps(self.cfg))

    def set(self, **kw) -> None:
        self.cfg.update(kw)
        self.save()

    def calls(self) -> list[dict]:
        p = self.dir / "calls.jsonl"
        return [json.loads(x) for x in p.read_text().splitlines()] if p.is_file() else []

    def reset(self) -> None:
        (self.dir / "calls.jsonl").unlink(missing_ok=True)

    def measurement_calls(self) -> int:
        return sum(1 for c in self.calls() if not c["label"].endswith("recheck"))

    def recheck_calls(self) -> int:
        return sum(1 for c in self.calls() if c["label"].endswith("recheck"))


@pytest.fixture
def engine(tmp_path, monkeypatch):
    d = tmp_path / "engine"
    d.mkdir()
    monkeypatch.setenv("FAKE_FERRIC", str(d / "cfg.json"))
    monkeypatch.setitem(sys.modules, "ferric", fake_ferric.make())
    return Engine(d)


@pytest.fixture
def recheck():
    from smeltery.methods import unregister

    spec = fake_ferric.register_recheck()
    yield spec
    unregister(spec.key)


def _config(tmp_path: Path, text: str, name: str = "c.toml") -> Path:
    p = tmp_path / name
    p.write_text(text)
    return p


def _run(cfg: Path, out: Path, store: Path, *extra: str) -> int:
    return main(["run", str(cfg), "--out", str(out), "--stage-cache", str(store), *extra])


def _record(out: Path) -> dict:
    (path,) = out.glob("run-*.json")
    return json.loads(path.read_text())


def _stages(out: Path) -> dict[str, dict]:
    return {e["stage"]: e for e in _record(out)["results"]["reuse"]["stages"]}


def _without_provenance(rec: dict) -> dict:
    """The record without what a reused run may differ in: reuse provenance, timestamps, wall-clock seconds."""
    rec = {k: v for k, v in rec.items() if k not in ("record_sha256", "started_utc")}
    results = results_without_timing(rec["results"])
    results["reuse"] = {
        **results["reuse"],
        "reused": None,
        "stages": [
            {k: v for k, v in e.items() if k not in ("reused", "reused_from")} for e in results["reuse"]["stages"]
        ],
    }
    return {**rec, "results": results}


# ---- identical resubmission ------------------------------------------------------------------------------------


def test_an_identical_resubmission_reuses_every_stage_and_runs_no_scf(tmp_path, engine, capsys):
    cfg, store = _config(tmp_path, SMALL.format(floor=0.0)), tmp_path / "store"
    assert _run(cfg, tmp_path / "a", store) == 0
    assert engine.measurement_calls() == N_SCFS
    engine.reset()
    capsys.readouterr()

    assert _run(cfg, tmp_path / "b", store) == 0
    assert engine.calls() == []  # not one SCF
    printed = capsys.readouterr().out
    assert "reused stage field_interaction" in printed and "stages reused: 1 of 1 reusable" in printed

    a, b = _record(tmp_path / "a"), _record(tmp_path / "b")
    assert a["input_digest"] == b["input_digest"]
    stage = _stages(tmp_path / "b")["field_interaction"]
    assert stage["reused"] is True and stage["cacheable"] is True
    assert stage["reused_from"]["run_dir"] == str(tmp_path / "a") and stage["reused_from"]["digest"] == stage["digest"]
    assert _stages(tmp_path / "a")["field_interaction"]["reused"] is False
    # identical except the reuse provenance and the clock
    assert _without_provenance(a) == _without_provenance(b)
    assert (tmp_path / "a" / "calcs.jsonl").read_bytes() == (tmp_path / "b" / "calcs.jsonl").read_bytes()
    for sdf in (tmp_path / "a" / "poses").iterdir():
        assert sdf.read_bytes() == (tmp_path / "b" / "poses" / sdf.name).read_bytes()
    # nothing of the run's progress is left behind once the record is written
    assert not (tmp_path / "b" / reuse.PROGRESS).exists()


def test_the_core_record_and_its_step_say_which_stage_was_reused_and_from_where(tmp_path, engine):
    schema = pytest.importorskip("attestation_schema")
    cfg, store = _config(tmp_path, SMALL.format(floor=0.0)), tmp_path / "store"
    assert _run(cfg, tmp_path / "a", store) == 0
    assert _run(cfg, tmp_path / "b", store) == 0
    ca, cb = schema.read(tmp_path / "a"), schema.read(tmp_path / "b")
    assert ca.metrics == cb.metrics  # core metrics unchanged
    assert (ca.run_id, ca.status, ca.config) == (cb.run_id, cb.status, cb.config)
    ra, rb = ca.ext["smeltery"]["reuse"], cb.ext["smeltery"]["reuse"]
    assert (ra["reused"], rb["reused"]) == (0, 1)
    steps = sorted((tmp_path / "b" / "steps").glob("*-field_interaction/stage.json"))
    body = json.loads(steps[0].read_text())
    assert body["reuse"]["reused"] is True and body["reuse"]["reused_from"]["run_dir"] == str(tmp_path / "a")
    pre = json.loads(next((tmp_path / "b" / "steps").glob("*-poses/stage.json")).read_text())
    assert pre["reuse"]["reused"] is False and "recomputed every run" in pre["reuse"]["why"]


def test_without_a_stage_store_the_record_keeps_its_shape(tmp_path, engine):
    cfg = _config(tmp_path, SMALL.format(floor=0.0))
    assert main(["run", str(cfg), "--out", str(tmp_path / "a")]) == 0
    assert "reuse" not in _record(tmp_path / "a")["results"]


# ---- invalidation ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "change, measured, rechecked",
    [
        # a later setting: the measurement is reused; the cut and everything after it are not
        (lambda t: t.replace("keep = 1", "keep = 2"), False, True),
        # the analysis's own setting: only it is recomputed
        (lambda t: t.replace("scale = 1.0", "scale = 2.0"), False, True),
        # the measurement's own setting: it and everything after it
        (lambda t: t + "\n[field_interaction]\nenergy_conv = 1e-9\n", True, True),
        # a stage before the measurement (the poses): the measurement's upstream changed
        (lambda t: t.replace("n_poses = 3", "n_poses = 2"), True, True),
        # nothing: everything reused
        (lambda t: t, False, False),
    ],
    ids=["cut", "analysis", "measurement", "poses", "nothing"],
)
def test_a_changed_setting_invalidates_its_stage_and_every_stage_after_it(
    tmp_path, engine, recheck, change, measured, rechecked
):
    text = SMALL.format(floor=0.0) + RECHECK
    store = tmp_path / "store"
    assert _run(_config(tmp_path, text), tmp_path / "a", store) == 0
    engine.reset()
    assert _run(_config(tmp_path, change(text), "d.toml"), tmp_path / "b", store) == 0
    assert (engine.measurement_calls() > 0) is measured
    assert (engine.recheck_calls() > 0) is rechecked
    a, b = _stages(tmp_path / "a"), _stages(tmp_path / "b")
    assert b["field_interaction"]["reused"] is (not measured)
    assert b["recheck"]["reused"] is (not rechecked)
    assert (a["field_interaction"]["digest"] == b["field_interaction"]["digest"]) is (not measured)
    assert (a["recheck"]["digest"] == b["recheck"]["digest"]) is (not rechecked)
    if not measured:  # what the measurement produced is the same bytes either way
        assert a["field_interaction"]["output_digest"] == b["field_interaction"]["output_digest"]


def test_a_different_ferric_build_invalidates(tmp_path, engine):
    cfg, store = _config(tmp_path, SMALL.format(floor=0.0)), tmp_path / "store"
    assert _run(cfg, tmp_path / "a", store) == 0
    engine.reset()
    engine.set(build={"git_sha": "b" * 40, "dirty": False})
    assert _run(cfg, tmp_path / "b", store) == 0
    assert engine.measurement_calls() == N_SCFS
    assert _stages(tmp_path / "b")["field_interaction"]["reused"] is False
    assert len(list(store.rglob("*.json"))) == 2  # one measurement per build


def test_a_dirty_build_is_never_reused_nor_stored(tmp_path, engine, capsys):
    engine.set(build={"git_sha": "a" * 40, "dirty": True})
    cfg, store = _config(tmp_path, SMALL.format(floor=0.0)), tmp_path / "store"
    assert _run(cfg, tmp_path / "a", store) == 0
    engine.reset()
    assert _run(cfg, tmp_path / "b", store) == 0
    assert engine.measurement_calls() == N_SCFS
    assert not list(store.rglob("*.json"))
    rec = _record(tmp_path / "b")["results"]["reuse"]
    assert rec["enabled"] is False and "dirty" in rec["why_not"]
    assert "stage reuse off" in capsys.readouterr().out


# ---- the store -------------------------------------------------------------------------------------------------


def _entries(store: Path) -> list[Path]:
    return sorted(p for p in store.rglob("*.json"))


@pytest.mark.parametrize("damage", ["output", "truncated", "key"])
def test_a_corrupted_entry_is_detected_by_digest_and_recomputed(tmp_path, engine, capsys, damage):
    cfg, store = _config(tmp_path, SMALL.format(floor=0.0)), tmp_path / "store"
    assert _run(cfg, tmp_path / "a", store) == 0
    (entry,) = _entries(store)
    doc = json.loads(entry.read_text())
    if damage == "output":  # one energy edited, its sha256 left as it was
        doc["output"]["calc_log"][0]["energy_hartree"] += 1.0
        entry.write_text(json.dumps(doc))
    elif damage == "key":
        doc["key_doc"]["settings"]["basis"] = "6-31g"
        entry.write_text(json.dumps(doc))
    else:
        entry.write_bytes(entry.read_bytes()[:200])
    engine.reset()
    capsys.readouterr()
    assert _run(cfg, tmp_path / "b", store) == 0
    assert engine.measurement_calls() == N_SCFS  # recomputed, never trusted
    assert "is corrupt" in capsys.readouterr().err
    assert list(store.rglob("*.corrupt-*"))  # set aside, not deleted: someone may want to see it
    assert _stages(tmp_path / "b")["field_interaction"]["reused"] is False
    assert _record(tmp_path / "a")["results"]["per_pose"] == _record(tmp_path / "b")["results"]["per_pose"]
    engine.reset()
    assert _run(cfg, tmp_path / "c", store) == 0  # the recomputed entry replaced it, and is reused
    assert engine.calls() == []


def _hammer(root: str, key: str, doc: dict, n: int, seen) -> None:
    store = reuse.StageStore(root)
    for i in range(n):
        store.put(key, doc, "s", {"v": list(range(200)), "i": 0}, {"run_id": str(os.getpid()), "i": i})
        got = store.get(key)
        seen.put(got is not None and got.output["v"] == list(range(200)))


def test_concurrent_writers_never_corrupt_the_store(tmp_path):
    doc = {"stage": "s", "kind": "measure", "upstream": "u", "settings": {}, "identity": {}}
    key = reuse.sha(doc)
    ctx = multiprocessing.get_context("fork")
    seen = ctx.Queue()
    procs = [ctx.Process(target=_hammer, args=(str(tmp_path), key, doc, 25, seen)) for _ in range(6)]
    for p in procs:
        p.start()
    for p in procs:
        p.join(60)
        assert p.exitcode == 0
    results = [seen.get(timeout=5) for _ in range(6 * 25)]
    assert all(results)  # every read, during the race, saw a complete entry or none, never a torn one
    files = [p for p in tmp_path.rglob("*") if p.is_file()]
    assert files == [reuse.StageStore(tmp_path).path(key)]  # one entry, no temporary file left behind
    assert reuse.StageStore(tmp_path).get(key) is not None


def test_a_store_entry_must_be_keyed_by_its_own_document(tmp_path):
    with pytest.raises(ValueError):
        reuse.StageStore(tmp_path).put("0" * 64, {"x": 1}, "s", {}, {})


# ---- the two layers: per-SCF checkpoint inside one OUT, stages across runs ---------------------------------------


def test_an_interrupted_measurement_resumes_from_its_checkpoint_then_is_stored_for_the_next_run(
    tmp_path, engine, monkeypatch
):
    from smeltery.tiers import FieldInteraction

    cfg, store, out = _config(tmp_path, SMALL.format(floor=0.0)), tmp_path / "store", tmp_path / "a"
    real, calls = FieldInteraction.scf, [0]

    def dies_after_7(self, *a):
        if calls[0] >= 7:
            raise RuntimeError("killed")
        calls[0] += 1
        return real(self, *a)

    with monkeypatch.context() as m:
        m.setattr(FieldInteraction, "scf", dies_after_7)
        assert _run(cfg, out, store) == 3
    assert not _entries(store)  # a measurement that did not finish is not a stage anyone may reuse
    logged = [e["stage"] for e in reuse.read_progress(out)]
    assert "poses" in logged and "field_interaction" not in logged
    engine.reset()
    assert _run(cfg, out, store) == 0  # same OUT: the checkpoint gives back the 7 finished SCFs
    assert engine.measurement_calls() == N_SCFS - 7
    assert len(_entries(store)) == 1
    engine.reset()
    assert _run(cfg, tmp_path / "b", store) == 0  # a new OUT: the stored stage, no SCF at all
    assert engine.calls() == []


# ---- cancellation, through the local job backend ---------------------------------------------------------------


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

[cut]
keep = 1
z = 2.0
floor = 0.0

[recheck]
scale = 1.0
"""


def _wait(pred, timeout: float = 120.0, what: str = "condition"):
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
    try:
        return Path(f"/proc/{pid}/stat").read_text().split()[2] != "Z"
    except OSError:
        return True


def test_a_cancel_mid_run_leaves_the_completed_stages_reusable_and_the_interrupted_one_not(tmp_path, engine, capsys):
    schema = pytest.importorskip("attestation_schema")
    from smeltery import jobs

    runner = [sys.executable, str(Path(fake_ferric.__file__).resolve()), "_job"]
    backend = jobs.LocalJobBackend(tmp_path / "ws", runner=runner)
    try:
        engine.set(rules={"ethanol pose 0 recheck|field": "hang"})  # the analysis hangs, after the measurement
        h = backend.submit(jobs.campaign_spec(_config(tmp_path, TINY), tmp_path / "first"))
        pid = _wait(lambda: (engine.dir / "hanging").is_file() and int((engine.dir / "hanging").read_text()))
        assert backend.cancel(h).state is schema.JobState.CANCELLED
        _wait(lambda: not _alive(pid), timeout=30, what="the cancelled run dying")  # SIGTERM reached the run
        rec = schema.read(h.run_dir)
        assert rec.status == "cancelled"
        done = rec.ext[jobs.EXT]["stages_completed"]
        names = [e["stage"] for e in done]
        assert {"poses", "pocket", "parity", "field_interaction", "cut"} <= set(names)
        assert "recheck" not in names  # interrupted: not completed, not stored
        capsys.readouterr()
        assert main(["status", h.run_dir, "--root", str(tmp_path / "ws")]) == 0
        printed = capsys.readouterr().out
        assert printed.startswith("cancelled") and "field_interaction" in printed

        engine.set(rules={})
        engine.reset()
        h2 = backend.submit(jobs.campaign_spec(_config(tmp_path, TINY, "again.toml"), tmp_path / "second"))
        _wait(lambda: backend.status(h2).state in schema.TERMINAL, what="the resubmission finishing")
        assert backend.status(h2).state is schema.JobState.COMPLETE
        assert engine.measurement_calls() == 0  # the measurement the cancelled job finished
        assert engine.recheck_calls() == 4  # the analysis it did not: 2 candidates x 2 poses
        stages = _stages(Path(h2.run_dir))
        assert stages["field_interaction"]["reused"] is True
        assert stages["field_interaction"]["reused_from"]["run_dir"] == h.run_dir
        assert stages["recheck"]["reused"] is False
    finally:
        backend.queue.stop_live("test teardown")


def test_a_temporal_cancel_lists_its_completed_stages():
    pytest.skip(
        "needs a Temporal server: a Temporal job is cancelled by terminating its workflow, which is "
        "tests/test_job_backends.py's temporal leg; the workflow keeps no stage log, so its cancelled record says "
        "stages_completed null, and it neither reads nor writes the stage store"
    )


# ---- through MCP: screen_candidates and run_status ---------------------------------------------------------------


POCKET = """
[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0
"""


def test_resubmitting_a_screen_reports_the_stages_it_reuses(tmp_path, engine):
    pytest.importorskip("mcp.server.mcpserver", reason="the MCP surface needs the `mcp` extra")
    pytest.importorskip("attestation_schema")
    import asyncio

    from smeltery import jobs
    from smeltery.mcp.server import ToolContext, build_server
    from smeltery.settings import resolve

    runner = [sys.executable, str(Path(fake_ferric.__file__).resolve()), "_job"]
    backend = jobs.LocalJobBackend(tmp_path / "ws", runner=runner)
    server = build_server(ToolContext(resolve({}), root=str(tmp_path / "ws"), backend=backend))

    def call(name, **kw):
        return asyncio.run(server.call_tool(name, kw)).structured_content

    target = _config(tmp_path, POCKET, "target.toml")
    ligands = {"ethanol": "CCO", "propanol": "CCCO"}

    def screen_and_finish():
        out = call("screen_candidates", target=str(target), ligand_set=ligands, keep=1)
        _wait(lambda: call("run_status", handle=out["handle"])["finished"], what="the screen finishing")
        return out, call("run_status", handle=out["handle"])

    try:
        first, done = screen_and_finish()
        assert (first["reused_stages"], first["reusable_stages"]) == (0, 1)
        assert done["state"] == "complete" and {t["state"] for t in done["tiers"]} == {"done"}
        engine.reset()
        again, done = screen_and_finish()
        assert (again["reused_stages"], again["reusable_stages"]) == (1, 1)
        assert "Reused 1 of 1 stages" in again["note"]
        assert done["state"] == "complete" and engine.calls() == []
        measured = next(t for t in done["tiers"] if t["stage"] == "field_interaction")
        assert measured["state"] == "reused" and "identical earlier run" in measured["note"]
    finally:
        backend.queue.stop_live("test teardown")
