"""`scripts/temporal_ddE.py`: the two guarantees the durable run rests on.

Both of these were broken in the run that motivated them (`ddE_ladder.jsonl`: 19 scored, 68 failed,
67 of those heartbeat timeouts), so each test fails on the code as it was.

The script is not a package module, so it is loaded by path. It needs `temporalio`, which is the
`temporal` extra, and these tests skip without it.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

temporalio = pytest.importorskip("temporalio", reason="needs the temporal extra")

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "temporal_ddE.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("temporal_ddE_under_test", SCRIPT)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- resume


def _write(tmp_path: Path, rows: list[dict]) -> None:
    (tmp_path / "out.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))


def _completed(mod, tmp_path: Path, monkeypatch) -> list[str]:
    monkeypatch.setenv(mod.STUDY_DIR_ENV, str(tmp_path))
    inp = mod.CellInput(cell="c", out="out.jsonl")
    return asyncio.run(mod.completed_names(inp))


def test_a_failed_molecule_is_not_treated_as_done(mod, tmp_path, monkeypatch):
    # The bug this fixes: a failure row counted as completed, so a restart skipped exactly the
    # molecules that still needed scoring and the failures were permanent without editing the file.
    _write(
        tmp_path,
        [
            {"cell": "c", "name": "scored", "dE_int": -1.5},
            {"cell": "c", "name": "timed_out", "error": "ActivityError: Activity task timed out"},
        ],
    )
    assert _completed(mod, tmp_path, monkeypatch) == ["scored"]


def test_a_molecule_that_failed_then_scored_is_done(mod, tmp_path, monkeypatch):
    _write(
        tmp_path,
        [
            {"cell": "c", "name": "m", "error": "transient"},
            {"cell": "c", "name": "m", "dE_int": -2.0},
        ],
    )
    assert _completed(mod, tmp_path, monkeypatch) == ["m"]


def test_a_torn_row_blocks_nothing_and_does_not_crash_the_resume(mod, tmp_path, monkeypatch):
    # A kill during an append can leave half a line; it names nothing reliably.
    (tmp_path / "out.jsonl").write_text('{"cell": "c", "name": "ok", "dE_int": -1.0}\n{"cell": "c", "na\n')
    assert _completed(mod, tmp_path, monkeypatch) == ["ok"]


def test_no_output_file_yet_is_an_empty_resume(mod, tmp_path, monkeypatch):
    assert _completed(mod, tmp_path, monkeypatch) == []


# ---------------------------------------------------------------- heartbeat


def test_the_heartbeat_keeps_beating_through_a_step_longer_than_the_timeout(mod, monkeypatch):
    """A slow SCF must not look like a dead worker.

    The science step is replaced by a sleep longer than one heartbeat interval: what is under test
    is that beats arrive WHILE it runs, which is what `heartbeat_timeout` measures. Beating only at
    stage boundaries (the bug) sends none during the step, and Temporal kills the activity.
    """
    from temporalio.testing import ActivityEnvironment

    beats: list[str] = []
    monkeypatch.setattr(mod, "HEARTBEAT", mod.timedelta(seconds=0.05))

    def fake_score(inp, name, set_stage, attempt):
        set_stage("scf")
        # Four heartbeat intervals inside one step, with no stage change: the old code sent nothing.
        import time

        time.sleep(0.25)
        return {"name": name, "dE_int": -1.0, "attempt": attempt}

    monkeypatch.setattr(mod, "_score_sync", fake_score)
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *a: beats.append(a[0] if a else "")
    out = asyncio.run(env.run(mod.score_molecule, mod.CellInput(cell="c"), "m"))

    assert out["dE_int"] == -1.0
    assert len(beats) >= 2, f"expected repeated beats during the step, got {beats}"
    assert beats[-1] == "scf", "the beat carries the stage the step reached"


def test_the_ticker_stops_when_the_step_does(mod, monkeypatch):
    """A leaked ticker would beat forever and keep a finished activity looking busy."""
    from temporalio.testing import ActivityEnvironment

    beats: list[str] = []
    monkeypatch.setattr(mod, "HEARTBEAT", mod.timedelta(seconds=0.02))
    monkeypatch.setattr(mod, "_score_sync", lambda inp, name, set_stage, attempt: {"name": name, "dE_int": 0.0})
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *a: beats.append(a[0] if a else "")

    async def run_then_settle() -> int:
        await env.run(mod.score_molecule, mod.CellInput(cell="c"), "m")
        after = len(beats)
        await asyncio.sleep(0.1)  # the ticker, if it leaked, would beat several times in here
        return after

    after = asyncio.run(run_then_settle())
    assert len(beats) == after, "the ticker kept beating after the activity returned"


def test_a_failing_step_also_stops_the_ticker_and_still_raises(mod, monkeypatch):
    from temporalio.testing import ActivityEnvironment

    beats: list[str] = []
    monkeypatch.setattr(mod, "HEARTBEAT", mod.timedelta(seconds=0.02))

    def boom(inp, name, set_stage, attempt):
        raise mod.ApplicationError("protein clash", type="ClashRefusal", non_retryable=True)

    monkeypatch.setattr(mod, "_score_sync", boom)
    env = ActivityEnvironment()
    env.on_heartbeat = lambda *a: beats.append(a[0] if a else "")

    async def run_then_settle() -> int:
        with pytest.raises(mod.ApplicationError, match="protein clash"):
            await env.run(mod.score_molecule, mod.CellInput(cell="c"), "m")
        after = len(beats)
        await asyncio.sleep(0.1)
        return after

    after = asyncio.run(run_then_settle())
    assert len(beats) == after, "a failing step leaked its ticker"
