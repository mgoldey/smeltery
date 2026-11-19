"""`smeltery mcp`: the four screening tools, driven the way a client drives them.

Every tool is called through the server (`list_tools` / `call_tool`), not as a Python function, so the argument
schemas, the output schemas and the refusal path are exercised too. The campaign is a real `smeltery run` on the
fake engine (`tests/fake_ferric.py`, as `tests/test_job_backends.py` runs it) against the local job backend, so
"submitted", "complete" and "dropped" mean what they mean in production.

One screen is run to completion for the whole module (`finished`): three alcohols in a two-point-charge pocket,
one of which the cut resolves and drops.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver", reason="the MCP surface needs the `mcp` extra")
pytest.importorskip("attestation_schema", reason="campaign jobs need attestation-schema (the `attestation` extra)")

import fake_ferric  # noqa: E402

from smeltery import jobs  # noqa: E402
from smeltery.mcp import screening, targets  # noqa: E402
from smeltery.mcp.server import ToolContext, build_server  # noqa: E402
from smeltery.settings import resolve  # noqa: E402

FAKE_RUNNER = [sys.executable, str(Path(fake_ferric.__file__).resolve()), "_job"]
TOOLS = {"screen_candidates", "run_status", "explain_drop", "estimate_cost"}
#: The reference, one analogue the cut resolves and drops, and the one it keeps (the fake engine's field term
#: grows with atom count, so octanol separates from propanol by more than 2 x their combined standard error).
LIGANDS = {"ethanol": "CCO", "propanol": "CCCO", "octanol": "CCCCCCCCO"}
TARGET = """
schema_version = 1
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
"""


def _target(dir_: Path, text: str = TARGET, name: str = "target.toml") -> str:
    p = dir_ / name
    p.write_text(text)
    return str(p)


class Server:
    """A built server plus the fake-engine workspace its jobs run in."""

    def __init__(self, root: Path, env: dict | None = None) -> None:
        self.root = root
        self.backend = jobs.LocalJobBackend(root / "ws", runner=FAKE_RUNNER)
        self.ctx = ToolContext(resolve(env or {}), root=str(root / "ws"), backend=self.backend)
        self.server = build_server(self.ctx)

    def tools(self):
        return asyncio.run(self.server.list_tools())

    def call(self, name: str, **arguments):
        """Call a tool and return its structured content, as a client reads it off the wire."""
        return asyncio.run(self.server.call_tool(name, arguments)).structured_content

    def close(self) -> None:
        self.backend.queue.stop_live("test teardown")


@pytest.fixture
def fake(tmp_path, monkeypatch):
    d = tmp_path / "fake"
    d.mkdir()
    monkeypatch.setenv("FAKE_FERRIC", str(d / "cfg.json"))
    (d / "cfg.json").write_text(json.dumps({"rules": {}, "dir": str(d)}))
    return d


@pytest.fixture
def server(tmp_path, fake):
    s = Server(tmp_path)
    try:
        yield s
    finally:
        s.close()


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    """One screen, run to completion on the fake engine, shared by the tests that need a finished run."""
    root = tmp_path_factory.mktemp("finished")
    d = root / "fake"
    d.mkdir()
    (d / "cfg.json").write_text(json.dumps({"rules": {}, "dir": str(d)}))
    os.environ["FAKE_FERRIC"] = str(d / "cfg.json")
    s = Server(root)
    try:
        out = s.call("screen_candidates", target=_target(root), ligand_set=LIGANDS, keep=1)
        handle = out["handle"]
        deadline = time.monotonic() + 600
        while True:
            status = s.call("run_status", handle=handle)
            if status["finished"]:
                break
            assert time.monotonic() < deadline, f"the screen never finished: {status}"
            time.sleep(0.5)
        assert status["state"] == "complete", status["message"]
        yield s, handle, status
    finally:
        s.close()
        os.environ.pop("FAKE_FERRIC", None)


# ---- the surface -------------------------------------------------------------------------------------------


def test_the_surface_is_four_intent_tools_each_with_an_output_schema(server):
    tools = server.tools()
    assert {t.name for t in tools} == TOOLS
    for t in tools:
        assert t.output_schema and t.output_schema["type"] == "object", t.name
        assert t.description and len(t.description) > 200, t.name


def test_the_cut_quantity_matches_the_pipelines(server):
    from smeltery import cli

    assert screening.QUANTITY == cli.QUANTITY


# ---- submit, poll, explain ---------------------------------------------------------------------------------


def test_screen_candidates_returns_a_handle_without_waiting_for_the_calculations(server, tmp_path, fake):
    (fake / "cfg.json").write_text(json.dumps({"rules": {"ethanol pose 0|vacuum": "hang"}, "dir": str(fake)}))
    t0 = time.monotonic()
    out = server.call("screen_candidates", target=_target(tmp_path), ligand_set=LIGANDS, keep=1)
    assert time.monotonic() - t0 < 120  # the hung SCF sleeps for 10 minutes; nothing here waited for it
    assert out["submitted"] is True
    assert out["state"] in ("submitted", "running")
    assert out["plan"]["target_class"] == targets.default_name()
    assert out["plan"]["tier"] == "field_interaction"
    state = json.loads((Path(out["handle"]) / "attestation" / "state.json").read_bytes())
    assert state["job_id"] == out["job_id"] and state["history"][0]["state"] == "submitted"
    server.backend.cancel(jobs.handle_in(out["handle"]))


def test_polling_a_screen_to_completion_reports_every_stage(finished):
    _s, handle, status = finished
    assert (status["state"], status["finished"]) == ("complete", True)
    assert status["handle"] == handle and status["record"] is not None
    stages = {t["stage"]: t for t in status["tiers"]}
    assert {"poses", "pocket", "parity", "field_interaction", "cut"} <= set(stages)
    assert all(t["state"] == "done" for t in status["tiers"])
    assert "3 of 3 kept" in stages["poses"]["note"]


def test_a_running_screen_reports_the_calculations_it_has_planned_and_finished(server, tmp_path, fake):
    (fake / "cfg.json").write_text(json.dumps({"rules": {"octanol pose 2|field": "hang"}, "dir": str(fake)}))
    out = server.call("screen_candidates", target=_target(tmp_path), ligand_set=LIGANDS, keep=1)
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status = server.call("run_status", handle=out["handle"])
        measurement = next((t for t in status["tiers"] if t["stage"] == "measurement"), None)
        if measurement is not None and measurement["total"]:
            assert measurement["total"] == 36  # 3 candidates x 6 poses x (vacuum + field)
            assert 0 <= measurement["done"] <= measurement["total"]
            assert measurement["unit"] == "SCF legs"
            break
        time.sleep(0.5)
    else:
        pytest.fail("the run never reported its planned calculations")
    server.backend.cancel(jobs.handle_in(out["handle"]))


def test_explain_drop_reads_the_dropped_candidate_its_threshold_and_its_floor(finished):
    s, handle, _status = finished
    dropped = s.call("explain_drop", candidate="propanol", handle=handle)
    assert dropped["verdict"] == "dropped" and dropped["stage"] == "cut"
    assert dropped["candidate"] == "propanol" and dropped["quantity"] == "dE_int"
    assert dropped["unit"] == "kcal/mol" and dropped["n_poses"] == 6
    assert dropped["compared_with"] == "octanol"
    assert dropped["separation"] is not None and dropped["separation_needed"] is not None
    assert dropped["separation"] > dropped["separation_needed"]  # resolved, which is why it could be dropped
    assert dropped["z"] == 2.0 and dropped["keep"] == 1
    assert dropped["floor"] == 0.0 and dropped["floor_measured"] is False
    assert "config [cut] floor" in dropped["floor_source"]
    assert "declares no measured floor" in dropped["explanation"]


def test_explain_drop_on_the_survivor_and_on_the_reference(finished):
    s, handle, _status = finished
    assert s.call("explain_drop", candidate="octanol", handle=handle)["verdict"] == "survivor"
    reference = s.call("explain_drop", candidate="ethanol", handle=handle)
    assert reference["verdict"] == "reference" and reference["stage"] is None


def test_explain_drop_refuses_a_molecule_the_run_never_had(finished):
    s, handle, _status = finished
    with pytest.raises(Exception, match="is not a candidate of this run"):
        s.call("explain_drop", candidate="benzene", handle=handle)


# ---- estimate and dry run ----------------------------------------------------------------------------------


def test_estimate_cost_prices_the_screen_without_submitting_anything(server, tmp_path):
    out = server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS, target_class="fast-triage")
    cost = out["cost"]
    assert cost["candidates"] == 3 and cost["poses_per_candidate"] == 3
    assert cost["scf_legs"] == 18  # 3 candidates x 3 poses x (vacuum + field)
    assert cost["cpu_hours"] > 0 and "sto-3g" in cost["cpu_hours_basis"]
    assert out["plan"]["floor"] == 0.0 and "no systematic error is subtracted" in out["plan"]["floor_note"]
    assert out["ceiling_exceeded"] is False and out["max_cpu_hours"] is None
    assert not list((tmp_path / "ws" / ".smeltery" / "screens").glob("*")) if (tmp_path / "ws").exists() else True


def _unpriceable(monkeypatch):
    """Make `smeltery.cost`'s calibration not cover these settings, as another basis set really does.

    `FieldInteraction.estimate_cost` then returns `predicted: None` with the reason, which is the honest path
    this surface has to carry all the way to the caller."""
    import dataclasses

    from smeltery import cost, tiers

    other = dataclasses.replace(cost.rhf_calibration(), basis="def2-svp")
    monkeypatch.setattr(tiers, "rhf_calibration", lambda: other)


def test_an_unpriceable_configuration_says_so_instead_of_inventing_a_number(server, tmp_path, monkeypatch):
    """Settings the cost model does not cover give `cpu_hours` null and a basis saying why, never a number."""
    _unpriceable(monkeypatch)
    out = server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS)
    assert out["cost"]["cpu_hours"] is None
    assert "unmeasured" in out["cost"]["cpu_hours_basis"]
    assert out["cost"]["candidates"] == 3 and out["cost"]["scf_legs"] == 36  # the countable parts are still counted


def test_a_dry_run_returns_the_plan_and_submits_nothing(server, tmp_path):
    out = server.call(
        "screen_candidates",
        target=_target(tmp_path),
        ligand_set=LIGANDS,
        keep=1,
        dry_run=True,
        out_dir=str(tmp_path / "r"),
    )
    assert out["submitted"] is False
    assert (out["handle"], out["job_id"], out["state"]) == (None, None, None)
    assert out["plan"]["cost"]["scf_legs"] == 36
    assert "Nothing was submitted" in out["note"]
    assert not (tmp_path / "r").exists()


# ---- overriding a class default -----------------------------------------------------------------------------


def test_relax_unmapped_defaults_to_the_class_and_an_override_is_reported_with_its_risk(server, tmp_path):
    default = server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS)["plan"]
    assert default["relax_unmapped"] is True and default["overrides"] == []
    same = server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS, relax_unmapped=True)["plan"]
    assert same["relax_unmapped"] is True and same["overrides"] == []  # the class's own value is no override
    off = server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS, relax_unmapped=False)["plan"]
    assert off["relax_unmapped"] is False
    (o,) = off["overrides"]
    assert (o["setting"], o["value"], o["class_default"]) == ("relax_unmapped", False, True)
    assert "pose geometry check" in o["risk"] and "reject" in o["risk"]


@pytest.mark.parametrize("relax", [False, True])
def test_either_relax_unmapped_is_written_to_the_config_and_off_risks_the_pose_check(server, tmp_path, relax):
    out = server.call(
        "screen_candidates",
        target=_target(tmp_path),
        ligand_set=LIGANDS,
        keep=1,
        relax_unmapped=relax,
        out_dir=str(tmp_path / "r"),
    )
    assert out["plan"]["relax_unmapped"] is relax
    text = (tmp_path / "r" / "config.toml").read_text()
    assert f"relax_unmapped = {str(relax).lower()}" in text
    overridden = "screen_candidates override: relax_unmapped = false (target class default true)" in text
    assert overridden is (not relax)  # true is the class default: no override to record
    deadline = time.monotonic() + 300
    while not (status := server.call("run_status", handle=out["handle"]))["finished"]:
        assert time.monotonic() < deadline, status
        time.sleep(0.5)
    if not relax:  # the risk the description names: octanol's added chain is placed unrelaxed, and refused
        assert status["state"] == "failed"
        assert "not this molecule" in status["message"] and "relax_unmapped=True" in status["message"]
        return
    assert status["state"] == "complete", status["message"]
    (record,) = (tmp_path / "r").glob("run-*.json")
    poses = next(t for t in json.loads(record.read_text())["tiers"] if t["name"] == "paired_poses")
    assert poses["relax_unmapped"] is True


def test_relax_unmapped_must_be_a_boolean(server, tmp_path):
    with pytest.raises(Exception, match="relax_unmapped"):
        server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS, relax_unmapped="maybe")


# ---- the guardrails ----------------------------------------------------------------------------------------


def test_over_the_ceiling_submission_is_refused_and_says_how_to_confirm_then_confirm_submits(tmp_path, fake):
    s = Server(tmp_path, env={"SMELTERY_MAX_CANDIDATES": "2"})
    try:
        target = _target(tmp_path)
        with pytest.raises(Exception, match="SMELTERY_MAX_CANDIDATES ceiling of 2") as refused:
            s.call("screen_candidates", target=target, ligand_set=LIGANDS, keep=1)
        assert "confirm=True" in str(refused.value)
        # the refusal is a decision, not a crash: nothing was submitted
        assert (
            not list((tmp_path / "ws" / ".smeltery" / "screens").glob("*"))
            if (tmp_path / "ws" / ".smeltery" / "screens").exists()
            else True
        )
        planned = s.call("screen_candidates", target=target, ligand_set=LIGANDS, keep=1, dry_run=True)
        assert planned["ceiling"]["exceeded"] is True and planned["submitted"] is False
        out = s.call("screen_candidates", target=target, ligand_set=LIGANDS, keep=1, confirm=True)
        assert out["submitted"] is True and out["ceiling"]["exceeded"] is True
        assert "overridden with confirm=True" in out["note"]
        s.backend.cancel(jobs.handle_in(out["handle"]))
    finally:
        s.close()


def test_an_unpriceable_screen_cannot_pass_a_cpu_hour_ceiling_it_cannot_be_measured_against(
    tmp_path, fake, monkeypatch
):
    """A ceiling that passed whenever the estimator gave up would be no ceiling for exactly the uncosted runs."""
    _unpriceable(monkeypatch)
    s = Server(tmp_path, env={"SMELTERY_MAX_CPU_HOURS": "1000"})
    try:
        with pytest.raises(Exception, match="cannot be estimated"):
            s.call("screen_candidates", target=_target(tmp_path), ligand_set=LIGANDS, keep=1)
    finally:
        s.close()


def test_an_unknown_handle_is_refused_with_what_a_handle_is(server, tmp_path):
    with pytest.raises(Exception, match="unknown handle") as e:
        server.call("run_status", handle=str(tmp_path / "nowhere"))
    assert "screen_candidates returned" in str(e.value)
    with pytest.raises(Exception, match="unknown handle"):
        server.call("explain_drop", candidate="propanol", handle=str(tmp_path / "nowhere"))


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"ligand_set": {"only": "CCO"}}, "at least two ligands"),
        ({"ligand_set": {}}, "ligand_set is empty"),
        ({"ligand_set": LIGANDS, "reference_ligand": "nope"}, "is not in ligand_set"),
        ({"ligand_set": LIGANDS, "target_class": "kinase"}, "unknown target class"),
        ({"ligand_set": LIGANDS, "keep": 9}, "between 1 and 2"),
    ],
)
def test_every_refusal_reaches_the_caller_verbatim(server, tmp_path, kwargs, needle):
    with pytest.raises(Exception, match=re.escape(needle)):
        server.call("screen_candidates", target=_target(tmp_path), **kwargs)


def test_a_target_that_is_not_a_pocket_is_refused(server, tmp_path):
    with pytest.raises(Exception, match="not a file"):
        server.call("screen_candidates", target=str(tmp_path / "missing.pdb"), ligand_set=LIGANDS)
    ligands_in_target = _target(tmp_path, 'schema_version = 1\n[candidates]\nx = "CCO"\n', "wrong.toml")
    with pytest.raises(Exception, match="do not describe the target"):
        server.call("screen_candidates", target=ligands_in_target, ligand_set=LIGANDS)


def test_a_bug_is_logged_and_answered_generically_never_as_a_traceback(server, tmp_path, monkeypatch, caplog):
    from smeltery.mcp import server as server_module

    def boom(*a, **k):
        raise ZeroDivisionError("a secret internal detail")

    monkeypatch.setattr(server_module.screening, "plan", boom)
    with caplog.at_level("ERROR"):
        with pytest.raises(Exception, match=re.escape(server_module.GENERIC)) as e:
            server.call("estimate_cost", target=_target(tmp_path), ligand_set=LIGANDS)
    assert "secret internal detail" not in str(e.value)
    assert "ZeroDivisionError" in caplog.text  # the traceback went to the log, not to the caller


# ---- the product surface: defaults and words -----------------------------------------------------------------

#: Jargon a caller with no quantum chemistry cannot be expected to know. A tool description may use one only if
#: it also spells it out; the gloss is the substring that must appear beside it.
GLOSSES = {
    "SMILES": "plain text form of a molecule",
    "SCF": "calculation",
    "DFT": "density-functional",
    "RHF": "Hartree-Fock",
    "HF": "Hartree-Fock",
    "QM": "quantum",
    "MCS": "common core",
    "ETKDG": "shapes",
    "MMFF": "force field",
    "PBE": "density-functional",
    "ddE": "difference",
    "dE_int": "interaction energy",
    "STO-3G": "basis set",
    "RI": "integral",
    "AO": "orbital",
    "SEM": "standard error",
}
#: Not chemistry jargon: ordinary computing words an agent reads every day.
ALLOWED = {"CPU", "MCP", "API", "JSON", "PDB", "TOML", "PQR", "SDF"}


def test_tool_descriptions_define_every_acronym_they_use(server):
    """A description aimed at an agent with no quantum chemistry must not leave an acronym unexplained."""
    offences = []
    for tool in server.tools():
        text = tool.description or ""
        # 'CCO' and the like are quoted EXAMPLES of a value, not vocabulary the reader has to know.
        prose = re.sub(r"'[^']*'", "", text)
        for token in set(re.findall(r"\b[A-Za-z][A-Za-z0-9_-]*\b", prose)):
            bare = token.strip(".,;:()")
            if bare in ALLOWED:
                continue
            gloss = GLOSSES.get(bare)
            if gloss is None:
                # not a listed jargon term; an unlisted ALL-CAPS token is still suspicious
                if len(bare) > 1 and bare.isupper() and bare not in ALLOWED:
                    offences.append(f"{tool.name}: unexplained acronym {bare!r}")
                continue
            if gloss not in text:
                offences.append(f"{tool.name}: uses {bare!r} without saying it means {gloss!r}")
    assert offences == [], offences


def test_the_target_class_table_is_data_with_a_source_for_every_default():
    classes, default = targets.load_table()
    assert default in classes and len(classes) >= 2
    for name, cls in classes.items():
        assert cls.tier in targets.TIER_SECTIONS, name
        assert cls.n_poses >= 1 and cls.keep >= 1 and cls.z > 0, name
        assert cls.floor == 0.0, f"{name}: a non-zero floor would be a measured systematic error, and none exists"
        # every default is justified from a file in this repository, not from an assertion about chemistry
        assert re.search(r"(docs/|src/smeltery/)", cls.why), name
        assert cls.limits and len(cls.limits) > 40, name
        section, knobs = cls.tier_section()
        assert knobs["basis"] == targets.BASIS and section in targets.TIER_SECTIONS.values()


def test_a_class_picks_the_tier_knobs_so_the_caller_never_does(tmp_path):
    cfg, cls = screening.build_config(_target(tmp_path), LIGANDS, target_class="large-ligand-dft")
    assert cls.tier == "field_dft_interaction"
    assert cfg["field_dft_interaction"] == {
        "basis": "sto-3g",
        "functional": "PBE",
        "memory_budget_gb": 8.0,
        "require_free_gb": 12.0,
    }
    assert cfg["poses"] == {"n_poses": 4, "relax_unmapped": True}
    assert cfg["cut"] == {"keep": 1, "z": 2.0, "floor": 0.0}


# ---- stdio -----------------------------------------------------------------------------------------------------


def test_the_server_answers_over_stdio_as_a_client_spawns_it(tmp_path):
    """One subprocess test: `smeltery mcp` really is an MCP server on stdin/stdout."""
    from mcp import Client, StdioServerParameters

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "smeltery", "mcp", "--root", str(tmp_path / "ws")],
        env={**os.environ},
        cwd=str(Path(__file__).resolve().parents[1]),
    )

    async def go():
        async with Client(params) as client:
            tools = await client.list_tools()
            return [t.name for t in tools.tools], client.instructions

    names, instructions = asyncio.run(asyncio.wait_for(go(), 180))
    assert set(names) == TOOLS
    assert "smeltery" in (instructions or "")
