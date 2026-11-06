"""What an edit costs without running it: live estimates from RDKit poses, a source run's poses, or "cannot say"."""

from __future__ import annotations

import json
import re
import shutil
import tomllib
from pathlib import Path

import pytest

from smeltery import cli
from smeltery.run_inputs import stage_inputs

ROOT = Path(__file__).resolve().parents[2]
BENZOIC = (ROOT / "examples" / "mwe_benzoic.toml").read_text()


def _docking_toml() -> str:
    """examples/mwe_docking.toml with its relative inputs made absolute (the test sends text, not a file)."""
    text = (ROOT / "examples" / "mwe_docking.toml").read_text()
    return re.sub(r'"\.\./tests/data/', f'"{ROOT}/tests/data/', text)


def _post(c, toml, baseline=None, **kw):
    r = c.post("/api/pipelines/impact", json={"toml": toml, "baseline_toml": baseline or toml, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _source_run(root: Path, toml: str) -> Path:
    """A run directory as `smeltery run` leaves one: staged config, its pose SDFs, a record naming both. The poses
    come from the docking-free twin of `toml` (docking itself is never run in these tests)."""
    out = root / "src-run"
    out.mkdir(parents=True)
    staged, _ = stage_inputs(toml, ROOT, out)
    (out / "config.toml").write_text(staged)
    free = tomllib.loads(toml)
    for k in ("docking", "pocket"):
        free.pop(k, None)
    free["pocket"] = {"site": [{"q": 1.0, "atom": 0, "from_atom": 1, "distance": 3.0}]}
    free.setdefault("poses", {}).pop("relax_unmapped", None)
    free.pop("gates", None)
    cfg = cli.validate_config(free)
    cfg["_dir"] = str(ROOT)
    plan = cli.build_plan(cfg)
    poses = cli.write_pose_sdfs(plan, {"per_pose": {c.name: {} for c in plan.candidates}}, out)
    rec = {"campaign": "mwe-docking", "results": {"config": {"file": "config.toml"}, "poses": poses}}
    (out / "run-src.json").write_text(json.dumps(rec))
    return out


def test_a_hand_placed_pipeline_gets_live_estimates(client_for, monkeypatch):
    monkeypatch.setattr(cli, "available_memory_gb", lambda: 12.5)
    c, _ = client_for()
    toml = BENZOIC
    got = _post(c, toml)
    assert got["poses_from"] == "computed"
    assert got["seconds"]["value"] > 0
    cfg = cli.validate_config(tomllib.loads(toml))
    cfg["_dir"] = str(ROOT)
    assert got["memory"] == cli.memory_estimate(cli.build_plan(cfg))
    assert got["memory"]["free_gb"] == 12.5 and got["memory"]["verdict"] == "no model"  # RHF has no memory model
    assert got["changes"] == [] and got["new_digest"] is False


def test_a_changed_step_is_named_and_the_digest_is_new(client_for):
    c, _ = client_for()
    edited = BENZOIC.replace("z = 2.0", "z = 3.0")
    got = _post(c, edited, BENZOIC)
    assert got["changes"] == ["cut"] and got["new_digest"] is True
    comment_only = _post(c, BENZOIC + "\n# a note\n", BENZOIC)
    assert comment_only["new_digest"] is False


def test_a_docking_edit_upstream_of_poses_cannot_say(client_for, monkeypatch):
    monkeypatch.setattr(cli, "_dock_parent", lambda *a, **k: pytest.fail("impact ran docking"))
    c, _ = client_for()
    base = _docking_toml()
    got = _post(c, base.replace("seeds = [7]", "seeds = [8]"), base)
    assert got["poses_from"] == "none"
    assert got["seconds"]["value"] is None and "cannot say" in got["seconds"]["basis"]
    assert got["memory"]["verdict"] == "cannot say"
    assert "docking" in got["changes"] or "active_site" in got["changes"]


def test_a_docking_edit_downstream_reuses_the_source_runs_poses(client_for, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "_dock_parent", lambda *a, **k: pytest.fail("impact ran docking"))
    base = _docking_toml()
    root = tmp_path / "runs"
    _source_run(root, base)
    c, _ = client_for(run_dir=root)
    downstream = _post(c, base.replace("z = 2.0", "z = 3.0"), base, source_run=0)
    assert downstream["poses_from"] == "source run"
    assert downstream["seconds"]["value"] > 0 and downstream["memory"]["verdict"] != "cannot say"
    assert downstream["changes"] == ["cut"]
    upstream = _post(c, base.replace("seeds = [7]", "seeds = [8]"), base, source_run=0)
    assert upstream["poses_from"] == "none" and upstream["seconds"]["value"] is None


def test_impact_never_runs_docking(client_for, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "_dock_parent", lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(AssertionError))
    base = _docking_toml()
    root = tmp_path / "runs"
    _source_run(root, base)
    c, _ = client_for(run_dir=root)
    for kw in ({}, {"source_run": 0}):
        got = _post(c, base, base, **kw)
        assert got["poses_from"] in ("none", "source run")
    _post(c, BENZOIC)
    assert calls == []


def test_a_bad_edit_is_an_issue_not_a_crash(client_for):
    c, _ = client_for()
    got = _post(c, BENZOIC.replace('"OC(=O)c1ccccc1"', '"not a smiles"'), BENZOIC)
    assert got["poses_from"] == "none" and got["seconds"]["value"] is None
    assert any(i["level"] == "error" for i in got["issues"])
    r = c.post("/api/pipelines/impact", json={"toml": "x = = 1", "baseline_toml": BENZOIC})
    assert r.status_code == 400


def _named(c, tmp_path, toml):
    made = c.post(
        "/api/pipelines",
        json={"name": "p1", "blank": {"campaign": "x", "parent": "a", "candidates": {"a": "CCO", "b": "CCN"}}},
    )
    assert made.status_code == 201
    r = c.put("/api/pipelines/p1", json={"toml": toml, "base_sha256": made.json()["sha256"]})
    assert r.status_code == 200, r.text
    return Path(r.json()["folder"])


def test_preview_submits_a_plan_task_and_impact_reuses_a_finished_one(client_for, tmp_path):
    c, ports = client_for()
    base = _docking_toml()
    folder = _named(c, tmp_path, base)
    r = c.post("/api/pipelines/p1/preview")
    assert r.status_code == 201
    name, command, cwd, _ = ports["task_queue"].submitted[-1]
    assert (
        name == "preview p1"
        and command[-5:] == ["run", str(folder / "config.toml"), "--plan", "--out", str(folder / "preview")][-5:]
    )
    assert _post(c, base, base, name="p1")["preview_task"] == r.json()["task"]
    assert _post(c, base, base, name="p1")["poses_from"] == "none"
    # a finished preview whose poses_key matches: `smeltery run --plan --out` writes plan.json and the SDFs
    src = _source_run(tmp_path / "scratch", base)
    prev = folder / "preview"
    shutil.copytree(src, prev)
    plan = {
        "digest": "d",
        "poses_key": cli.poses_key(tomllib.loads(base), folder),
        "poses": json.loads((src / "run-src.json").read_text())["results"]["poses"],
    }
    (prev / "plan.json").write_text(json.dumps(plan))
    got = _post(c, base, base, name="p1")
    assert got["poses_from"] == "computed" and got["seconds"]["value"] > 0
    plan["poses_key"] = "stale"
    (prev / "plan.json").write_text(json.dumps(plan))
    assert _post(c, base, base, name="p1")["poses_from"] == "none"


def test_plan_out_writes_plan_json_and_pose_sdfs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "available_memory_gb", lambda: 8.0)
    cfg = tmp_path / "c.toml"
    cfg.write_text(BENZOIC)
    out = tmp_path / "prev"
    assert cli.main(["run", str(cfg), "--plan", "--out", str(out)]) == 0
    text = capsys.readouterr().out
    plan = json.loads((out / "plan.json").read_text())
    digest = re.search(r"input_digest (\w+)", text)
    assert digest and plan["digest"] == digest.group(1)
    assert plan["seconds"]["predicted"] > 0 and plan["memory"]["verdict"] == "no model"
    assert set(plan["poses"]) == {"benzoic", "4-F", "4-Cl"}
    assert all((out / e["file"]).is_file() for e in plan["poses"].values())
    assert plan["poses_key"] == cli.poses_key(tomllib.loads(BENZOIC), tmp_path)


def test_every_memory_result_has_one_shape(client_for):
    from smeltery.bench.api.pipelines import Impact

    c, _ = client_for()
    base = _docking_toml()
    computed = _post(c, BENZOIC)
    cannot = _post(c, base.replace("seeds = [7]", "seeds = [8]"), base)
    assert cannot["memory"]["verdict"] == "cannot say" and cannot["memory"]["estimate"] is None
    assert cannot["memory"]["basis"] and set(cannot["memory"]) == set(computed["memory"])
    for body in (computed, cannot):
        Impact.model_validate(body)


def test_an_estimation_crash_is_an_issue_not_a_500(client_for, monkeypatch, tmp_path):
    c, _ = client_for()

    def boom(*a, **k):
        raise RuntimeError("embedding failed")

    monkeypatch.setattr(cli, "build_plan", boom)
    got = _post(c, BENZOIC)
    assert got["poses_from"] == "none" and got["seconds"]["value"] is None
    assert got["memory"]["verdict"] == "cannot say"
    assert any("RuntimeError" in i["message"] and "embedding failed" in i["message"] for i in got["issues"])
    monkeypatch.undo()
    unreadable = tmp_path / "pocket.pdb"
    unreadable.mkdir()  # a path that exists as a file-like name but cannot be read as a file
    toml = _docking_toml().replace(f'{ROOT}/tests/data/pocket_pep5.pdb"', f'{unreadable}"')
    monkeypatch.setattr(cli.hashlib, "sha256", lambda *a, **k: (_ for _ in ()).throw(OSError("unreadable input")))
    got = _post(c, toml, toml)
    assert got["poses_from"] == "none" and any("OSError" in i["message"] for i in got["issues"])


def test_a_second_preview_returns_the_running_one(client_for):
    c, ports = client_for()
    _named(c, None, _docking_toml())
    first = c.post("/api/pipelines/p1/preview").json()["task"]
    second = c.post("/api/pipelines/p1/preview").json()["task"]
    assert second == first and len(ports["task_queue"].submitted) == 1
    for t in ports["task_queue"].tasks:
        t.status = "done"
    third = c.post("/api/pipelines/p1/preview").json()["task"]
    assert third != first and len(ports["task_queue"].submitted) == 2
