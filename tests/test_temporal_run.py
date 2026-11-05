"""`smeltery run --temporal`: the pipeline as a durable Temporal workflow, and the RHF remedy ladder.

The SCFs are a fake `ferric` (no engine runs): its energies are a deterministic function of the geometry and the
field, and it fails to converge on demand, so both the Temporal run and the plain run it is compared with compute
the same numbers. The workflow tests need the Temporal dev server at 127.0.0.1:7233; each uses its own task queue
and an in-process worker (as `smeltery temporal-worker` runs it), and terminates what it started.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
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

from golden_path import results_without_timing  # noqa: E402

from smeltery import cli, temporal_run  # noqa: E402
from smeltery.cli import ConfigError, main, validate_config  # noqa: E402
from smeltery.methods import MethodSpec, register, unregister  # noqa: E402
from smeltery.model import HARTREE_TO_KCAL, PointCharge  # noqa: E402
from smeltery.tiers import FieldInteraction  # noqa: E402

HOST = "127.0.0.1:7233"

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


# ------------------------------------------------------------------------------------------------ a fake ferric


class FakeFerric(types.ModuleType):
    """`BasisSet.bundled`, `Molecule.from_xyz_string`, `run_rhf`: energies from the geometry, convergence on demand.

    `rules[(label, leg)]` is a predicate on the run_rhf kwargs saying whether that SCF converges (default: yes).
    `block_after`: the SCF call with that index (0-based) waits on `gate`, and raises if `killed` is set then."""

    def __init__(self):
        super().__init__("ferric")
        self.rules: dict = {}
        self.calls: list[tuple[str, str, dict]] = []  # completed SCFs
        self.lock = threading.Lock()
        self.block_after: int | None = None
        self.blocked, self.gate, self.killed = threading.Event(), threading.Event(), False
        self._started = 0
        fake = self

        class BasisSet:
            @staticmethod
            def bundled(name):
                return name

        class Molecule:
            def __init__(self, label, xyz):
                self.label, self.xyz = label, xyz

            @staticmethod
            def from_xyz_string(text, charge=0):
                lines = text.splitlines()
                xyz = np.array([[float(v) for v in ln.split()[1:4]] for ln in lines[2:] if ln.strip()])
                return Molecule(lines[1], xyz)

        def run_rhf(mol, bs, point_charges=None, **kw):
            leg = "vacuum" if point_charges is None else "field"
            with fake.lock:
                k = fake._started
                fake._started += 1
            if fake.block_after is not None and k == fake.block_after:
                fake.blocked.set()
                fake.gate.wait(60)
                if fake.killed:
                    raise RuntimeError("worker killed mid-SCF")
            e = -0.1 * len(mol.xyz) - 0.01 * float(np.abs(mol.xyz).sum())
            if point_charges:
                for q, x, y, z in point_charges:
                    d = np.linalg.norm(mol.xyz - np.array([x, y, z]) / 1.8897261246, axis=1)
                    e += -0.02 * q * float(np.sum(1.0 / (d + 1.0)))
            ok = fake.rules.get((mol.label, leg), lambda kw: True)(kw)
            with fake.lock:
                fake.calls.append((mol.label, leg, dict(kw)))
            return types.SimpleNamespace(converged=bool(ok), iterations=7, energy=e)

        self.BasisSet, self.Molecule, self.run_rhf = BasisSet, Molecule, run_rhf


@pytest.fixture
def ferric(monkeypatch):
    fake = FakeFerric()
    monkeypatch.setitem(sys.modules, "ferric", fake)
    temporal_run._CACHE.clear()
    return fake


def _config(tmp_path: Path, n_poses: int = 2, extra: str = "") -> Path:
    p = tmp_path / "c.toml"
    p.write_text(TINY.format(n_poses=n_poses) + extra)
    return p


def _record(out: Path) -> dict:
    (path,) = out.glob("run-*.json")
    return json.loads(path.read_text())


def _rows(out: Path) -> list[dict]:
    return [json.loads(x) for x in (out / "calcs.jsonl").read_text().splitlines() if x.strip()]


@contextlib.contextmanager
def worker(task_queue: str, workers: int = 2):
    """A pipeline worker on its own thread and event loop, as `smeltery temporal-worker` runs it."""
    loop = asyncio.new_event_loop()
    ready, box = threading.Event(), {}

    async def run():
        box["stop"] = asyncio.Event()
        client = await temporal_run.connect(HOST)
        ready.set()
        await temporal_run.run_worker(client, task_queue, workers, box["stop"])

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


@pytest.fixture
def queue():
    return f"smeltery-pipeline-test-{uuid.uuid4().hex[:8]}"


# ------------------------------------------------------------------------------------------------ the remedy ladder


def test_the_ladder_climbs_when_an_scf_does_not_converge(ferric):
    """Rung 0 does not converge, rung 1 (a level shift) does: the value is rung 1's, and the rung is recorded."""
    from smeltery.model import Candidate, Pose

    ferric.rules[("m pose 0", "field")] = lambda kw: "level_shift" in kw
    tier = FieldInteraction(remedies=[{"level_shift": 0.5}, {"max_iter": 500, "soscf": True}])
    c = Candidate("m", "O")
    c.poses = [Pose(("O", "H", "H"), np.array([[0.0, 0, 0], [0.96, 0, 0], [-0.24, 0.93, 0]]))]
    tier.run([c], {"field": [PointCharge(1.0, (3.0, 0.0, 0.0))]})
    assert c.per_pose["scf_rung"] == [1.0]
    legs = [(r["leg"], r["rung"], r["converged"]) for r in tier.calc_log]
    assert legs == [("vacuum", 0, True), ("field", 0, False), ("field", 1, True)]
    assert ferric.calls[-1][2]["level_shift"] == 0.5 and ferric.calls[-1][2]["energy_conv"] == 1e-10
    assert tier.produces() == {"dE_int": "kcal/mol", "scf_rung": "rung"}
    assert tier.settings()["remedies"] == [{"level_shift": 0.5}, {"max_iter": 500, "soscf": True}]


def test_every_rung_failing_still_raises_and_logs_every_scf(ferric):
    from smeltery.model import Candidate, Pose

    ferric.rules[("m pose 0", "vacuum")] = lambda kw: False
    tier = FieldInteraction(remedies=[{"level_shift": 0.5}])
    c = Candidate("m", "O")
    c.poses = [Pose(("O", "H", "H"), np.array([[0.0, 0, 0], [0.96, 0, 0], [-0.24, 0.93, 0]]))]
    with pytest.raises(RuntimeError, match="did not converge"):
        tier.run([c], {"field": [PointCharge(1.0, (3.0, 0.0, 0.0))]})
    assert [(r["leg"], r["rung"]) for r in tier.calc_log] == [("vacuum", 0), ("vacuum", 1), ("field", 0)]


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
        ({"diis": 1}, "not a valid bool"),
        ({}, "non-empty table"),
    ],
)
def test_a_bad_remedy_is_a_config_error(tmp_path, rung, match):
    import tomllib

    cfg = tomllib.loads(TINY.format(n_poses=2))
    with pytest.raises(ConfigError, match=match):
        validate_config({**cfg, "field_interaction": {"remedies": [rung]}})
    validate_config({**cfg, "field_interaction": {"remedies": [{"level_shift": 0.5, "diis_size": 12}]}})


def test_run_scf_timeout_is_validated():
    import tomllib

    cfg = tomllib.loads(TINY.format(n_poses=2))
    assert cli.scf_timeout_s(cfg) == 14400.0
    validate_config({**cfg, "run": {"scf_timeout_s": 60}})
    assert cli.scf_timeout_s({**cfg, "run": {"scf_timeout_s": 60}}) == 60.0
    for bad in (0, -1, "1h", True):
        with pytest.raises(ConfigError, match="scf_timeout_s"):
            validate_config({**cfg, "run": {"scf_timeout_s": bad}})
    with pytest.raises(ConfigError, match=r"unknown key\(s\) \['timeout'\] in \[run\]"):
        validate_config({**cfg, "run": {"timeout": 60}})


# ------------------------------------------------------------------------------------------------ the workflow


@needs_server
def test_a_temporal_run_records_what_a_plain_run_records(ferric, tmp_path, queue, capsys):
    cfg = _config(tmp_path)
    plain, durable = tmp_path / "plain", tmp_path / "durable"
    assert main(["run", str(cfg), "--out", str(plain)]) == 0
    n_plain = len(ferric.calls)
    try:
        with worker(queue):
            assert main(["run", str(cfg), "--out", str(durable), "--temporal", "--task-queue", queue]) == 0
    finally:
        _terminate(durable)
    assert len(ferric.calls) == 2 * n_plain  # the same SCFs, once each
    a, b = _record(plain), _record(durable)
    assert a["input_digest"] == b["input_digest"]
    assert results_without_timing(a["results"]) == results_without_timing(b["results"])
    rows = _rows(durable)
    assert len(rows) == 2 * 2 * 2 and {r["field_model"] for r in rows} == {"primary"}
    assert all(r["rung"] == 0 and r["converged"] for r in rows)
    assert "run record:" in capsys.readouterr().out


@needs_server
def test_a_pose_failing_every_rung_is_dropped_pairwise(ferric, tmp_path, queue):
    """The parent's pose 0 fails every rung: pose 0 goes for every analogue, though propanol's converged. Propanol's
    pose 1 fails: it goes for propanol only, and the parent's pose 1 is not in propanol's ddE. The parent's pose 2
    converges on rung 1."""
    cfg = _config(tmp_path, n_poses=4, extra="\n[field_interaction]\nremedies = [{level_shift = 0.5}]\n")
    ferric.rules[("ethanol pose 0", "field")] = lambda kw: False
    ferric.rules[("ethanol pose 2", "field")] = lambda kw: "level_shift" in kw
    ferric.rules[("propanol pose 1", "vacuum")] = lambda kw: False
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
    assert drops[("ethanol", 0)]["rungs_tried"] == [0, 1] and drops[("ethanol", 0)]["outcome"] == "not_converged"
    assert drops[("ethanol", 0)]["dropped_with"] == ["ethanol", "propanol"]
    assert drops[("propanol", 1)]["dropped_with"] == ["ethanol", "propanol"]
    assert res["per_pose"]["ethanol"]["scf_rung"] == [0.0, 1.0, 0.0]
    # every SCF is in the log with its rung; a converged leg was not recomputed on the next rung
    rows = _rows(out)
    scf = [r for r in rows if "outcome" not in r]
    e0 = [(r["leg"], r["rung"], r["converged"]) for r in scf if r["candidate"] == "ethanol" and r["pose"] == 0]
    assert sorted(e0) == [
        ("field", 0, False),
        ("field", 1, False),
        ("vacuum", 0, True),
    ]
    assert sum(1 for r in rows if r.get("outcome") == "not_converged") == 2
    # propanol's ddE pairs its poses 2, 3 with the parent's poses 2, 3
    e = {
        (r["candidate"], r["pose"], r["leg"]): r["energy_hartree"]
        for r in rows
        if "outcome" not in r and r["converged"]
    }

    def de(c, i):
        return (e[(c, i, "field")] - e[(c, i, "vacuum")]) * HARTREE_TO_KCAL

    want = [de("propanol", i) - de("ethanol", i) for i in (2, 3)]
    assert res["paired_ddE"]["propanol"]["n"] == 2
    assert res["paired_ddE"]["propanol"]["per_pose"] == pytest.approx(want, abs=1e-9)


@needs_server
def test_a_killed_worker_resumes_without_recomputing(ferric, tmp_path, queue):
    """The 4th SCF hangs; the worker is stopped mid-SCF and a new one takes over. Every SCF that had finished is
    not run again; a whole new workflow run on the same OUT reads everything back and runs nothing."""
    cfg = _config(tmp_path)
    out = tmp_path / "out"
    _, config_info = cli._stage_config(cfg, out)
    inp = temporal_run.RunInput(str(out / "config.toml"), str(out), 1, config_info, heartbeat_s=3.0)
    ferric.block_after = 3

    async def go():
        client = await temporal_run.connect(HOST)
        return await client.start_workflow(
            temporal_run.PipelineRunWorkflow.run,
            inp,
            id=temporal_run.workflow_id(out, (out / "config.toml").read_text()),
            task_queue=queue,
        )

    try:
        with worker(queue, workers=1):
            asyncio.run(go())
            assert ferric.blocked.wait(60), "the hanging SCF never started"
            done_before = list(ferric.calls)
        # worker A is gone, its SCF still hanging: let it die the way a killed process's work does
        ferric.killed = True
        ferric.gate.set()
        time.sleep(0.2)
        ferric.killed, ferric.block_after = False, None
        assert len(done_before) == 3
        with worker(queue, workers=1):

            async def finish():
                client = await temporal_run.connect(HOST)
                return await temporal_run.start_and_wait(client, inp, queue, poll_s=1, log=lambda *_: None)

            first = asyncio.run(finish())
            keys = [(label, leg) for label, leg, _ in ferric.calls]
            assert len(keys) == len(set(keys)) == 8  # every SCF once: none of the finished ones again
            assert keys[:3] == [(label, leg) for label, leg, _ in done_before]
            record = Path(first.record).read_text()
            again = asyncio.run(finish())  # a new run of the same workflow id: everything is read back
            assert len(ferric.calls) == 8 and again.n_resumed == 4
            assert (
                json.loads(Path(again.record).read_text())["results"]["per_pose"]
                == json.loads(record)["results"]["per_pose"]
            )
    finally:
        _terminate(out)


def test_the_server_down_exits_2_naming_the_docker_command(ferric, tmp_path, capsys):
    cfg = _config(tmp_path)
    rc = main(["run", str(cfg), "--out", str(tmp_path / "out"), "--temporal", "--host", "127.0.0.1:1"])
    err = capsys.readouterr().err
    assert rc == 2 and temporal_run.DOCKER_COMMAND in err and "never falls back" in err
    assert not (tmp_path / "out" / "calcs.jsonl").exists() and not ferric.calls  # nothing ran instead
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
    assert {r["field_model"] for r in rows} == {"primary", "HALF"} and len(rows) == 2 * 2 * 2 * 2
    # the half field's values differ from the primary's: the scores used their own field
    assert measured["HALF"]["ethanol"] != res["per_pose"]["ethanol"]["dE_int"]


def test_plan_counts_the_scf_work_including_extra_fields(ferric, tmp_path, half, capsys, monkeypatch):
    cfg = _config(tmp_path, extra="\n[half_field]\n")
    assert main(["run", str(cfg), "--plan"]) == 0
    out = capsys.readouterr().out
    assert "SCF work: 8 scores = 2 poses x 2 candidates x 2 field model(s) (16 SCFs: vacuum + field)" in out
    one = re.search(r"estimate ([0-9.]+) CPU-h", out)
    # the cheap `extra_models(cfg)`, when the hook has it, is what counts (no field is built for --plan)
    _, hook = half
    monkeypatch.setattr(hook, "extra_models", lambda cfg: ["HALF", "THIRD"], raising=False)
    monkeypatch.setattr(hook, "extra_fields", lambda plan, ctx: pytest.fail("--plan built a field"))
    assert main(["run", str(cfg), "--plan"]) == 0
    out = capsys.readouterr().out
    assert "SCF work: 12 scores = 2 poses x 2 candidates x 3 field model(s) (24 SCFs: vacuum + field)" in out
    three = re.search(r"estimate ([0-9.]+) CPU-h", out)
    if one is not None:  # the tier's calibration covers these elements: the hours scale with the models
        assert float(three.group(1)) == pytest.approx(1.5 * float(one.group(1)), rel=0.05, abs=0.011)


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
