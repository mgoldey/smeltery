"""Resuming a run that stopped before it finished.

The task queue reports a dead task failed and never retries it -- "re-running a command is your call"
(`WorkPanel.tsx`, and the queue's own contract). But `smeltery run` is resumable: it reads the checkpoint in the
out directory and recomputes only what is missing, which is what `RunProgress.reused` counts. The directory holds
its own `config.toml` with every input staged by hash, so it "can be redone from that directory alone"
(`use_cases/launch_run.py`).

Until now nothing exposed that. Every route that submits a run went through a *pipeline*, and a run records no
pipeline name, so a user whose overnight run died at candidate 9 of 12 had to leave Bench, read the `--out` path
out of the task's command and re-run it by hand -- or press Run and pay for the nine finished SCFs again.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bench.fakes import FakeTaskQueue

from .conftest import record_path


@pytest.fixture
def resumable(tmp_path):
    """A half-finished run directory where a real Bench puts one: inside the workspace root, under `out/`.

    `client_for` roots the app at `tmp_path/"bench-root"`, and `confined_out` measures every requested out
    directory against that root, so a run dir built anywhere else is refused before the route's own checks run.
    """
    from gui_fixture import make_run_dir

    root = tmp_path / "bench-root"
    (root / "out").mkdir(parents=True, exist_ok=True)
    return Path(make_run_dir(root / "out"))


def _half_finished(out: Path) -> Path:
    """A run directory as an interrupted run leaves it: a config, a checkpoint, and no run record."""
    record_path(out).unlink()  # the record is written at the end; an interrupted run has none
    (out / "config.toml").write_text('[campaign]\nname = "Danuglipron"\n')
    (out / "checkpoint.jsonl").write_text('{"candidate": "an1"}\n')
    return out


def test_resuming_submits_smeltery_run_on_the_directorys_own_config(client_for, resumable):
    """The whole point: the same out directory, so the finished SCFs in it are reused rather than repeated."""
    out = _half_finished(resumable)
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    r = c.post("/api/runs/resume", json={"out": str(out)})
    assert r.status_code == 201, r.text
    (name, command, cwd, _note) = q.submitted[-1]
    assert command[1] == "run"
    assert command[2] == str(out / "config.toml")  # the directory's own staged config, not a pipeline's
    assert command[command.index("--out") + 1] == str(out)  # and back into the same directory
    assert "Danuglipron" in name


def test_a_finished_run_is_not_resumed(client_for, resumable):
    """A directory holding a run record is a result, not a half-finished job. Re-running it would overwrite a
    conclusion someone may already have cited."""
    out = resumable
    (out / "config.toml").write_text('[campaign]\nname = "Danuglipron"\n')
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    r = c.post("/api/runs/resume", json={"out": str(out)})
    assert r.status_code == 409, r.text
    assert "record" in r.json()["reason"]
    assert q.submitted == []  # nothing was launched


def test_a_directory_with_no_config_cannot_be_resumed(client_for, resumable):
    """Without the staged config there is nothing to re-run, and guessing one from a pipeline would run different
    inputs under the same directory's name."""
    out = _half_finished(resumable)
    (out / "config.toml").unlink()
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    r = c.post("/api/runs/resume", json={"out": str(out)})
    assert r.status_code == 409, r.text
    assert "config.toml" in r.json()["reason"]
    assert q.submitted == []


def test_resume_cannot_be_pointed_outside_the_workspace(client_for, resumable):
    """The same confinement every launch gets: a request must never make the server write where it likes."""
    out = _half_finished(resumable)
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    r = c.post("/api/runs/resume", json={"out": "../../etc"})
    assert r.status_code == 400, r.text
    assert "workspace" in r.json()["reason"]
    assert q.submitted == []


def test_an_empty_out_is_refused_rather_than_defaulted(client_for, resumable, tmp_path):
    """`confined_out` reads an empty request as "use the default", and it refuses the workspace root only when the
    root is named explicitly. Resume has no sensible default, so an empty `out` must be a bad request: letting it
    fall through launched a run at ROOT itself, on whatever config happened to sit there."""
    out = _half_finished(resumable)
    root = tmp_path / "bench-root"
    (root / "config.toml").write_text('[campaign]\nname = "Danuglipron"\n')
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    r = c.post("/api/runs/resume", json={"out": ""})
    assert r.status_code == 400, r.text
    assert q.submitted == [], "an empty out launched a run at the workspace root"


def test_a_symlink_to_a_finished_run_is_still_refused(client_for, tmp_path):
    """`confined_out` resolves the path before returning it, so the route's own checks see the real directory. A
    symlink under ROOT pointing at a finished run therefore cannot smuggle it past the "it finished" refusal."""
    from gui_fixture import make_run_dir

    root = tmp_path / "bench-root"
    (root / "out").mkdir(parents=True, exist_ok=True)
    finished = Path(make_run_dir(root / "out"))  # holds a run-*.json
    (finished / "config.toml").write_text('[campaign]\nname = "Danuglipron"\n')
    link = root / "out" / "shortcut"
    link.symlink_to(finished, target_is_directory=True)
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=finished, queue=q)
    r = c.post("/api/runs/resume", json={"out": str(link)})
    assert r.status_code == 409, r.text
    assert q.submitted == [], "a symlink hid a finished run from the refusal"


# Read-only is not re-tested here: `test_read_only.py` walks every route the app exposes and asserts each unsafe
# operation is 403 with no safelist, so this POST is covered there the moment it exists.


def test_a_resumed_run_gets_the_memory_ceiling_a_launch_gets(client_for, resumable, monkeypatch):
    """`docs/bench.md`: every launched run gets a memory ceiling. A resumed one used to be submitted with none. It has
    no pipeline preview to predict from, so it gets what a launch without a preview gets: the host share."""
    from smeltery.bench.use_cases import launch_run

    monkeypatch.setattr(launch_run, "host_memory_gb", lambda: 10.0)
    out = _half_finished(resumable)
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    assert c.post("/api/runs/resume", json={"out": str(out)}).status_code == 201
    assert q.options == [
        {"memory_limit_gb": pytest.approx(10.0 * launch_run.HOST_RAM_SHARE), "timeout_s": None, "capped": True}
    ]
    assert "--temporal" not in q.submitted[-1][1]


def test_a_run_resumed_on_a_durable_queue_is_resumed_as_one(client_for, resumable):
    """On the Temporal queue a fresh launch is `smeltery run --temporal` with no limits (the queue refuses them);
    a resumed run used to be the plain run. Both now come from the same code."""

    class Durable(FakeTaskQueue):
        def run_flags(self):
            return ["--temporal"]

    out = _half_finished(resumable)
    q = Durable()
    c, _ = client_for(run_dir=out, queue=q)
    assert c.post("/api/runs/resume", json={"out": str(out)}).status_code == 201
    assert q.submitted[-1][1][-1] == "--temporal"
    assert q.options == [{"memory_limit_gb": None, "timeout_s": None, "capped": True}]


def test_a_real_config_names_the_task_after_its_campaign(client_for, resumable):
    """A config says `campaign = "..."`. Reading it as a `[campaign]` table raised AttributeError: a 500."""
    out = _half_finished(resumable)
    (out / "config.toml").write_text('campaign = "benzoic"\nparent = "benzoic"\n')
    q = FakeTaskQueue()
    c, _ = client_for(run_dir=out, queue=q)
    r = c.post("/api/runs/resume", json={"out": str(out)})
    assert r.status_code == 201, r.text
    assert q.submitted[-1][0] == "run benzoic"
