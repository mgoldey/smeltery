"""`smeltery serve` / `smeltery worker` and the settings behind them: flag > SMELTERY_* env var > default, a bad value
named, and the old `task-worker`, `temporal-worker`, `run --temporal --host/--task-queue` spellings still working.
Nothing here starts a server, a worker or a Temporal connection: every entry point is replaced by a recorder."""

from __future__ import annotations

import asyncio

import pytest

from smeltery import cli, services, settings, tasks, temporal_run
from smeltery.settings import Settings, SettingsError, resolve

# ---- resolve: pure ---------------------------------------------------------------------------------------------


def test_defaults_need_no_environment():
    s = resolve({})
    assert s == Settings()
    assert (s.backend, s.temporal_address, s.namespace) == ("local", "localhost:7233", "default")
    assert (s.task_queue, s.pipeline_queue) == ("smeltery-tasks", "smeltery-pipeline")
    assert (s.host, s.port, s.root) == (None, None, None)  # Bench resolves these itself
    assert (s.workers, s.threads_per_worker, s.task_concurrency, s.max_runs) == (2, 1, 2, 1)


def test_env_overrides_defaults():
    s = resolve(
        {
            "SMELTERY_BACKEND": "Temporal",
            "SMELTERY_TEMPORAL_ADDRESS": "temporal:7233",
            "SMELTERY_TEMPORAL_NAMESPACE": "lab",
            "SMELTERY_TASK_QUEUE": "t",
            "SMELTERY_PIPELINE_QUEUE": "p",
            "SMELTERY_HOST": "0.0.0.0",
            "SMELTERY_PORT": "9000",
            "SMELTERY_ROOT": "/data",
            "SMELTERY_WORKERS": "6",
            "SMELTERY_THREADS_PER_WORKER": "2",
            "SMELTERY_TASK_CONCURRENCY": "4",
            "SMELTERY_MAX_RUNS": "3",
        }
    )
    assert s == Settings("temporal", "temporal:7233", "lab", "t", "p", "0.0.0.0", 9000, "/data", 6, 2, 4, 3)


def test_flags_override_env_and_none_means_not_given():
    env = {"SMELTERY_BACKEND": "temporal", "SMELTERY_PORT": "9000", "SMELTERY_WORKERS": "6"}
    s = resolve(env, backend="local", port="9100", workers=None)
    assert (s.backend, s.port, s.workers) == ("local", 9100, 6)


def test_resolve_does_not_read_the_process_environment(monkeypatch):
    monkeypatch.setenv("SMELTERY_BACKEND", "temporal")
    assert resolve({}).backend == "local"


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"SMELTERY_BACKEND": "postgres"}, "SMELTERY_BACKEND"),
        ({"SMELTERY_PORT": "eighty"}, "SMELTERY_PORT"),
        ({"SMELTERY_PORT": "70000"}, "SMELTERY_PORT"),
        ({"SMELTERY_WORKERS": "0"}, "SMELTERY_WORKERS"),
        ({"SMELTERY_MAX_RUNS": "-1"}, "SMELTERY_MAX_RUNS"),
        ({"SMELTERY_TEMPORAL_ADDRESS": "nohostport"}, "SMELTERY_TEMPORAL_ADDRESS"),
        ({"SMELTERY_TASK_QUEUE": "  "}, "SMELTERY_TASK_QUEUE"),
    ],
)
def test_a_bad_env_value_fails_naming_the_variable(env, name):
    with pytest.raises(SettingsError, match=name):
        resolve(env)


def test_a_bad_flag_value_names_the_flag():
    with pytest.raises(SettingsError, match="--backend"):
        resolve({}, backend="nope")


def test_an_unknown_setting_is_a_caller_bug():
    with pytest.raises(TypeError):
        resolve({}, bogus=1)


def test_every_env_var_is_documented():
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[1] / "docs" / "temporal-runs.md").read_text()
    assert [v for v in settings.ENV_VARS if v not in doc] == []


# ---- serve -----------------------------------------------------------------------------------------------------


@pytest.fixture
def bench_calls(monkeypatch):
    calls = []
    import smeltery.bench.app as bench_app

    monkeypatch.setattr(bench_app, "main", lambda argv=None, *, task_queue=None: calls.append((argv, task_queue)) or 0)
    return calls


def test_serve_local_is_bench_with_flags_from_env(bench_calls):
    assert services.serve_main(["--demo"], env={"SMELTERY_HOST": "0.0.0.0", "SMELTERY_PORT": "9000"}) == 0
    argv, queue = bench_calls[0]
    assert queue is None  # the local SQLite + subprocess queue, built by Bench itself
    assert argv[0] == "--demo" and argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--port") + 1] == "9000"


def test_serve_flag_beats_env(bench_calls):
    services.serve_main(["--port", "9100"], env={"SMELTERY_PORT": "9000"})
    argv, _ = bench_calls[0]
    assert argv[argv.index("--port") + 1] == "9100"


def test_serve_without_settings_leaves_host_and_port_to_bench(bench_calls):
    services.serve_main([], env={})
    argv, _ = bench_calls[0]
    assert "--host" not in argv and "--port" not in argv


@pytest.mark.skipif(not tasks.TEMPORAL_AVAILABLE, reason="the temporal extra is not installed")
def test_serve_temporal_hands_bench_a_temporal_queue(bench_calls):
    env = {"SMELTERY_BACKEND": "temporal", "SMELTERY_TEMPORAL_ADDRESS": "t:7233", "SMELTERY_TASK_QUEUE": "q"}
    services.serve_main([], env=env)
    queue = bench_calls[0][1]
    assert (queue._m.host, queue._m.task_queue, queue._m.namespace) == ("t:7233", "q", "default")


def test_serve_bad_backend_exits_2_naming_the_variable(bench_calls, capsys):
    assert services.serve_main([], env={"SMELTERY_BACKEND": "redis"}) == 2
    assert "SMELTERY_BACKEND" in capsys.readouterr().err and not bench_calls


def test_serve_is_a_subcommand(bench_calls):
    assert cli.main(["serve", "--port", "9100"]) == 0
    assert "9100" in bench_calls[0][0]


# ---- worker ----------------------------------------------------------------------------------------------------


def test_worker_local_supervises_the_root(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(services, "local_worker", lambda root, max_runs: seen.append((root, max_runs)) or 0)
    assert services.worker_main([], env={"SMELTERY_ROOT": str(tmp_path), "SMELTERY_MAX_RUNS": "3"}) == 0
    assert seen == [(tmp_path, 3)]


def test_local_worker_reconciles_and_starts_queued_tasks(tmp_path):
    n = []
    services.local_worker(tmp_path, 1, interval_s=0, stop=lambda: n.append(1) or len(n) > 2)
    assert (tmp_path / ".smeltery" / "bench" / "tasks" / "tasks.sqlite").exists()


@pytest.mark.skipif(not tasks.TEMPORAL_AVAILABLE, reason="the temporal extra is not installed")
def test_worker_temporal_polls_both_queues_in_one_process(monkeypatch):
    got = {}

    async def connect(host, namespace="default"):
        got["connect"] = (host, namespace)
        return "client"

    async def task_worker(client, queue, concurrency, stop=None):
        got["tasks"] = (client, queue, concurrency)

    async def pipeline_worker(client, queue, workers, threads, stop=None, command=None):
        got["pipeline"] = (client, queue, workers, threads)

    monkeypatch.setattr(temporal_run, "connect", connect)
    monkeypatch.setattr(tasks, "run_task_worker", task_worker)
    monkeypatch.setattr(temporal_run, "run_worker", pipeline_worker)
    env = {"SMELTERY_BACKEND": "temporal", "SMELTERY_TEMPORAL_ADDRESS": "t:1", "SMELTERY_WORKERS": "5"}
    assert services.worker_main([], env=env) == 0
    assert got["connect"] == ("t:1", "default")
    assert got["tasks"] == ("client", "smeltery-tasks", 2)
    assert got["pipeline"] == ("client", "smeltery-pipeline", 5, 1)


@pytest.mark.skipif(not tasks.TEMPORAL_AVAILABLE, reason="the temporal extra is not installed")
def test_worker_temporal_server_down_exits_2(capsys):
    assert services.worker_main(["--backend", "temporal", "--temporal-address", "127.0.0.1:1"], env={}) == 2
    assert "cannot reach the Temporal server" in capsys.readouterr().err


def test_worker_bad_value_exits_2_naming_it(capsys):
    assert services.worker_main([], env={"SMELTERY_WORKERS": "many"}) == 2
    assert "SMELTERY_WORKERS" in capsys.readouterr().err


# ---- the old spellings keep working, and now honour the environment ------------------------------------------------


def test_temporal_worker_alias_flags_and_env(monkeypatch):
    seen = []
    monkeypatch.setattr(temporal_run, "worker_cli", lambda *a: seen.append(a) or 0)
    monkeypatch.delenv("SMELTERY_TEMPORAL_ADDRESS", raising=False)
    monkeypatch.delenv("SMELTERY_PIPELINE_QUEUE", raising=False)
    assert cli.main(["temporal-worker"]) == 0
    assert seen[-1] == ("localhost:7233", "smeltery-pipeline", 2, 1, "default")  # defaults
    monkeypatch.setenv("SMELTERY_TEMPORAL_ADDRESS", "env:1")
    monkeypatch.setenv("SMELTERY_WORKERS", "4")
    cli.main(["temporal-worker", "--task-queue", "mine", "--threads-per-worker", "3"])
    assert seen[-1] == ("env:1", "mine", 4, 3, "default")  # env fills what flags leave; flags win
    cli.main(["temporal-worker", "--host", "flag:2", "--workers", "1"])
    assert seen[-1][0] == "flag:2" and seen[-1][2] == 1


def test_run_temporal_host_and_task_queue_flags(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(temporal_run, "run_cli", lambda *a, **k: seen.append((a, k)) or 0)
    monkeypatch.setattr(cli, "_stage_config", lambda config, out, temporal=False: (config, None))
    monkeypatch.setattr(cli, "load_config", lambda p: {})
    monkeypatch.setenv("SMELTERY_TEMPORAL_NAMESPACE", "lab")
    monkeypatch.setenv("SMELTERY_WORKERS", "5")
    out = str(tmp_path / "o")
    cfg = tmp_path / "c.toml"
    cfg.write_text("")  # run re-reads the file verbatim after load_config (stubbed), so it must exist
    assert cli.main(["run", str(cfg), "--out", out, "--temporal", "--host", "h:1", "--task-queue", "q"]) == 0
    a, k = seen[-1]
    # (config, out, workers, host, queue, config_info): flags win, env fills the rest
    assert a[2:5] == (5, "h:1", "q") and k == {"namespace": "lab", "run_id": None, "arm": None, "ext": None}
    monkeypatch.delenv("SMELTERY_WORKERS")
    monkeypatch.delenv("SMELTERY_TEMPORAL_NAMESPACE")
    assert cli.main(["run", str(cfg), "--out", out, "--temporal"]) == 0
    a, k = seen[-1]
    assert a[2:5] == (2, "localhost:7233", "smeltery-pipeline") and k == {
        "namespace": "default",
        "run_id": None,
        "arm": None,
        "ext": None,
    }


def test_run_temporal_carries_the_arm_and_ext_to_the_workflow(monkeypatch, tmp_path):
    """`--arm` / `--ext-json` combine with `--temporal`: they reach the workflow's input, whose record stage writes
    them into the core record at its first write (they were refused before the workflow carried them)."""
    seen = []
    monkeypatch.setattr(temporal_run, "run_cli", lambda *a, **k: seen.append((a, k)) or 0)
    monkeypatch.setattr(cli, "_stage_config", lambda config, out, temporal=False: (config, None))
    monkeypatch.setattr(cli, "load_config", lambda p: {})
    cfg = tmp_path / "c.toml"
    cfg.write_text("")
    argv = ["run", str(cfg), "--out", str(tmp_path / "o"), "--temporal", "--arm", "iter-001"]
    assert cli.main([*argv, '--ext-json={"smeltery.al": {"loop": "x"}}']) == 0
    _, k = seen[-1]
    assert (k["arm"], k["ext"]) == ("iter-001", {"smeltery.al": {"loop": "x"}})


def test_task_worker_alias_runs_the_tasks_queue(monkeypatch):
    seen = []

    async def connect(host, namespace="default"):
        seen.append(("connect", host, namespace))
        return "c"

    async def run_task_worker(client, queue, concurrency, stop=None):
        seen.append(("worker", queue, concurrency))

    monkeypatch.setattr(temporal_run, "connect", connect)
    monkeypatch.setattr(tasks, "run_task_worker", run_task_worker)
    # the command refuses without temporalio before it connects; the connect and worker above are fakes, so the
    # extra is not needed to test the alias (the `test` CI job does not install it)
    monkeypatch.setattr(tasks, "TEMPORAL_AVAILABLE", True)
    monkeypatch.setenv("SMELTERY_TASK_CONCURRENCY", "7")
    assert cli.main(["task-worker", "--host", "h:9", "--namespace", "ns", "--task-queue", "tq"]) == 0
    assert seen == [("connect", "h:9", "ns"), ("worker", "tq", 7)]


def test_a_bad_env_value_stops_the_old_commands_too(monkeypatch, capsys):
    monkeypatch.setenv("SMELTERY_WORKERS", "0")
    assert cli.main(["temporal-worker"]) == 2
    assert "SMELTERY_WORKERS" in capsys.readouterr().err


def test_asyncio_import_is_used():  # keeps the module's asyncio import honest for the async fakes above
    assert asyncio.iscoroutinefunction(services.temporal_worker)
