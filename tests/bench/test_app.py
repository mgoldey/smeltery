"""The app shell: health, the run list, independent failure, verdicts, composition, and the built UI."""

import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from smeltery.bench.app import BUILD_HINT, Settings, build_app, create_app, main


class BrokenTaskQueue:
    def list(self, status=None):
        raise RuntimeError("temporal down")

    def cancel(self, tid):
        raise RuntimeError("temporal down")


def test_healthz_answers_200(client_for):
    c, _ = client_for()
    assert c.get("/healthz").status_code == 200


def test_livez_and_readyz(client_for, tmp_path):
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    (tmp_path / "bench-root").mkdir()
    c, _ = client_for(queue=SubprocessTaskQueue(tmp_path / "q"))
    assert c.get("/livez").json() == {"status": "ok"}
    r = c.get("/readyz")
    assert r.status_code == 200 and r.json()["checks"]["task_queue"]["ok"] is True


def test_readyz_is_503_when_the_task_db_cannot_open(client_for, tmp_path):
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    q = SubprocessTaskQueue(tmp_path / "q")
    q.db.unlink()
    c, _ = client_for(queue=q)
    r = c.get("/readyz")
    assert r.status_code == 503 and r.json()["checks"]["task_queue"]["ok"] is False


def test_runs_list_is_served_from_the_run_store(client_for, run_dir):
    c, _ = client_for(run_dir)
    (row,) = c.get("/api/runs").json()
    assert row["idx"] == 0 and row["parent"] == "benzoic" and row["verdict"]["state"] == "RESOLVED"
    assert len(row["digest"]) == 64


def test_a_dead_task_backend_is_503_while_runs_still_answer(client_for, run_dir):
    c, _ = client_for(run_dir, queue=BrokenTaskQueue())
    r = c.get("/api/tasks")
    assert r.status_code == 503 and "temporal down" in r.json()["reason"]
    assert c.get("/api/runs").status_code == 200


def test_the_task_failure_is_fast_not_a_hang(client_for, run_dir):
    c, _ = client_for(run_dir, queue=BrokenTaskQueue())
    t0 = time.monotonic()
    c.get("/api/tasks")
    assert time.monotonic() - t0 < 2.0


@pytest.mark.parametrize(
    ("fixture", "state", "winner", "missing"),
    [
        ("run_dir", "RESOLVED", "4-F", []),
        ("unranked_run_dir", "UNRANKED", None, []),
        ("incomplete_run_dir", "INCOMPLETE", None, ["cut"]),
    ],
)
def test_verdict_endpoint_for_each_state(request, client_for, fixture, state, winner, missing):
    c, _ = client_for(request.getfixturevalue(fixture))
    v = c.get("/api/runs/0/verdict").json()
    assert (v["state"], v["winner"], v["missing"]) == (state, winner, missing)


def test_an_incomplete_run_is_409_naming_what_it_lacks(client_for, incomplete_run_dir):
    c, _ = client_for(incomplete_run_dir)
    r = c.get("/api/runs/0/ddE")
    assert r.status_code == 409 and "cut" in r.json()["detail"]


def test_unknown_paths_are_404(client_for):
    c, _ = client_for()
    assert c.get("/nope").status_code == 404
    assert c.get("/api/nope").status_code == 404


def test_whoami_is_identity_current(client_for):
    c, _ = client_for(identity="alice")
    assert c.get("/api/whoami").json()["user"] == "alice"


def test_local_stores_live_under_the_runs_root(tmp_path, monkeypatch):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    root = tmp_path / "root"
    root.mkdir()
    build_app(root)
    base = root / ".smeltery" / "bench"
    assert (base / "comments.sqlite").exists() and (base / "projects.sqlite").exists()
    assert not list(cwd.iterdir())


def test_web_index_is_served_with_spa_fallback(tmp_path, run_dir):
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><div id=root></div>")
    (web / "assets" / "app.js").write_text("console.log(1)")
    from bench.fakes import FakeIdentity, FakeRunStore, FakeTaskQueue, FixedClock

    app = create_app(
        run_store=FakeRunStore(run_dir),
        task_queue=FakeTaskQueue(),
        identity=FakeIdentity(),
        clock=FixedClock(),
        settings=Settings(root=tmp_path / "r", web_dir=web),
    )
    c = TestClient(app)
    for path in ("/", "/run/0/results", "/report/0"):
        r = c.get(path)
        assert r.status_code == 200 and "id=root" in r.text, path
    assert c.get("/assets/app.js").text == "console.log(1)"
    assert c.get("/api/nope").status_code == 404
    assert c.get("/api/runs").status_code == 200


def test_bench_refuses_without_a_built_ui(monkeypatch, tmp_path, capsys):
    import smeltery.bench.app as appmod

    monkeypatch.setattr(appmod, "WEB_DIR", tmp_path / "absent")
    assert main([str(tmp_path)]) == 2
    assert "npm run build" in capsys.readouterr().err
    assert "npm run build" in BUILD_HINT


# ---------------------------------------------------------------------------------------------- demo and defaults


def _fake_web(monkeypatch, tmp_path):
    import smeltery.bench.app as appmod

    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    (web / "index.html").write_text("<div id=root></div>")
    monkeypatch.setattr(appmod, "WEB_DIR", web)
    for env in ("SMELTERY_BENCH_ROOT", "SMELTERY_BENCH_HOST", "SMELTERY_BENCH_PORT", "SMELTERY_BENCH_TOKEN"):
        monkeypatch.delenv(env, raising=False)
    return appmod


def _serve_spy(monkeypatch, appmod):
    """Replace uvicorn.run and build_app with recorders; main() then returns without serving."""
    import uvicorn

    seen: dict = {}
    real_build = appmod.build_app
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port, log_level: seen.update(host=host, port=port))
    monkeypatch.setattr(appmod, "build_app", lambda root, **kw: seen.update(root=Path(root)) or real_build(root, **kw))
    return seen


def test_demo_seeds_every_prebuilt_and_lists_five_runs_without_scf(tmp_path):
    from fastapi.testclient import TestClient

    from smeltery.bench.app import build_app
    from smeltery.bench.use_cases.seed_demo import seed_demo

    root = tmp_path / "demo"
    added = seed_demo(root)
    assert added == ["benzoic-pbe", "benzoic-rhf", "danuglipron-halogen", "docking", "structure"]
    c = TestClient(build_app(root, web_dir=tmp_path / "no-web"), base_url="http://localhost")
    runs = c.get("/api/runs").json()
    assert len(runs) >= 5
    assert sorted(c.get("/api/pipelines").json()) == added
    assert c.get("/api/tasks").json() == []  # nothing was submitted: finished runs only


def test_a_second_demo_neither_duplicates_nor_overwrites(tmp_path):
    from smeltery.bench.adapters.run_store import FilesystemRunStore
    from smeltery.bench.use_cases.seed_demo import seed_demo

    root = tmp_path / "demo"
    seed_demo(root)
    before = len(FilesystemRunStore(root).list_runs())
    edited = root / "pipelines" / "benzoic-rhf" / "config.toml"
    edited.write_text(edited.read_text() + "# mine\n")
    assert seed_demo(root) == []
    assert len(FilesystemRunStore(root).list_runs()) == before
    assert edited.read_text().endswith("# mine\n")


def test_main_demo_twice_is_fine_and_opens_a_run(monkeypatch, tmp_path, capsys):
    appmod = _fake_web(monkeypatch, tmp_path)
    seen = _serve_spy(monkeypatch, appmod)
    root = tmp_path / "d"
    for _ in range(2):
        assert appmod.main([str(root), "--demo", "--port", "0"]) == 0
    err = capsys.readouterr().err
    assert "already seeded" in err and "/run/" in err and seen["root"] == root


def test_root_host_and_port_precedence(monkeypatch, tmp_path):
    appmod = _fake_web(monkeypatch, tmp_path)
    seen = _serve_spy(monkeypatch, appmod)
    data = tmp_path / "xdg"
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    assert appmod.main(["--port", "0"]) == 0  # nothing set: the per-user data directory, created on first use
    assert seen["root"] == data / "smeltery" / "bench" and seen["root"].is_dir()
    assert (seen["host"], seen["port"]) == ("127.0.0.1", 0)
    monkeypatch.delenv("XDG_DATA_HOME")
    assert appmod.default_root() == Path.home() / ".local" / "share" / "smeltery" / "bench"
    monkeypatch.setenv("SMELTERY_BENCH_ROOT", str(tmp_path / "env-root"))
    monkeypatch.setenv("SMELTERY_BENCH_HOST", "localhost")
    monkeypatch.setenv("SMELTERY_BENCH_PORT", "0")
    assert appmod.main([]) == 0  # env beats the default
    assert seen["root"] == tmp_path / "env-root" and (seen["host"], seen["port"]) == ("localhost", 0)
    assert appmod.main([str(tmp_path / "arg-root"), "--host", "127.0.0.1", "--port", "0"]) == 0  # flags beat env
    assert seen["root"] == tmp_path / "arg-root" and seen["host"] == "127.0.0.1"


def test_a_bad_port_env_exits_2_and_names_it(monkeypatch, tmp_path, capsys):
    appmod = _fake_web(monkeypatch, tmp_path)
    monkeypatch.setenv("SMELTERY_BENCH_PORT", "eighty")
    assert appmod.main([str(tmp_path / "r")]) == 2
    assert "SMELTERY_BENCH_PORT" in capsys.readouterr().err


def test_a_taken_port_prints_a_message_naming_it_and_exits_nonzero(monkeypatch, tmp_path, capsys):
    import socket

    appmod = _fake_web(monkeypatch, tmp_path)
    with socket.create_server(("127.0.0.1", 0)) as held:
        port = held.getsockname()[1]
        assert appmod.main([str(tmp_path / "r"), "--port", str(port)]) == 1
    err = capsys.readouterr().err
    assert f"port {port}" in err and "--port" in err and "Traceback" not in err
