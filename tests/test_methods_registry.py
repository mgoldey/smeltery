"""The method registry (`smeltery.methods`): one registration declares a method, and everything that used to list
methods by hand (the catalog, the execution order, the steps, the CLI's keys and validation, input staging) derives
from it. Existing configs keep their input digests."""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

from smeltery import cli, run_inputs
from smeltery.cli import ConfigError, build_plan, load_config, run_plan, validate_config
from smeltery.gui import pipeline as pipeline_mod
from smeltery.gui import steps as steps_mod
from smeltery.gui.catalog import catalog
from smeltery.gui.pipeline import Pipeline
from smeltery.gui.steps import steps
from smeltery.methods import MethodSpec, by_slot, register, registry, unregister
from smeltery.model import PointCharge
from smeltery.record import RunRecord
from smeltery.run_inputs import stage_inputs
from smeltery.tiers import PairedPoses

ROOT = Path(__file__).resolve().parent.parent

BENZOIC = """
campaign = "t"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"
4-Cl = "OC(=O)c1ccc(Cl)cc1"

[poses]
n_poses = 3

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def fill(plan):
    """Stand-in for the SCFs: fixed dE_int per pose (kcal/mol); no ferric."""
    shift = {"benzoic": 0.0, "4-F": -1.0, "4-Cl": -3.0}
    for c in plan.candidates:
        c.per_pose["dE_int"] = [-60.0 + 0.5 * i + shift[c.name] for i in range(len(c.poses))]


# ---------------------------------------------------------------------------------------------- a dummy analysis


@dataclass
class DummyKnobs:
    scale: float = 1.0
    notes: str | None = None  # an input file: staged, and digested by content


class DummyAnalysis:
    def __init__(self):
        self.calls = []

    def run(self, plan, rec, ctx):
        self.calls.append((plan, rec, ctx))
        scale = plan.cfg["dummy_analysis"].get("scale", 1.0)
        parent = plan.parent.per_pose["dE_int"]
        return {"scaled_parent_mean": scale * sum(parent) / len(parent), "saw_cut": "cut" in rec.results}


@pytest.fixture
def dummy():
    hook = DummyAnalysis()
    spec = register(
        MethodSpec(
            "dummy_analysis",
            "Dummy analysis",
            "ab_initio",
            "measure",
            step="measure",
            slot="analysis",
            section="dummy_analysis",
            knobs=DummyKnobs,
            file_knobs=("notes",),
            order=150,
            needs=("dE_int",),
            hook=hook,
            summary="A test registration; it averages the parent's dE_int.",
        )
    )
    yield spec, hook
    unregister("dummy_analysis")


def test_a_new_registration_appears_everywhere(dummy, tmp_path):
    spec, hook = dummy
    # the catalog, the execution order, the CLI's top-level keys and input staging
    assert "dummy_analysis" in catalog()
    assert {p.name for p in catalog()["dummy_analysis"].params} == {"scale", "notes"}
    order = pipeline_mod.EXECUTION_ORDER
    assert order.index("field_dft_interaction") < order.index("dummy_analysis") < order.index("cut")
    assert "dummy_analysis" in cli.TOP_KEYS
    assert ("dummy_analysis", "notes") in run_inputs.INPUT_PATHS
    assert spec in by_slot("analysis")

    # its section validates in validate_config
    cfg = tomllib.loads(BENZOIC)
    validate_config({**cfg, "dummy_analysis": {"scale": 2.0}})
    with pytest.raises(ConfigError, match=r"unknown key\(s\) \['scael'\] in \[dummy_analysis\]"):
        validate_config({**cfg, "dummy_analysis": {"scael": 2.0}})
    with pytest.raises(ConfigError, match=r"\[dummy_analysis\] must be a table"):
        validate_config({**cfg, "dummy_analysis": 2.0})

    # offered by Pipeline.addable(), placed where smeltery run executes it, round-trips through the config
    p = Pipeline.from_config(cfg)
    assert "dummy_analysis" in p.addable()
    p.add("dummy_analysis", {"scale": 2.0})
    assert p.keys().index("dummy_analysis") == p.keys().index("cut") - 1
    assert p.to_config()["dummy_analysis"] == {"scale": 2.0}
    assert "dummy_analysis" in Pipeline.from_config(p.to_config()).keys()
    assert "dummy_analysis" not in p.addable()

    # it lands in its step in steps()
    assert "dummy_analysis" in dict((k, m) for k, _, m in steps_mod.STEPS)["measure"]
    view = {v.key: v for v in steps(p)}["measure"]
    assert "dummy_analysis" in view.methods
    assert {x["name"] for x in view.params if x["method"] == "dummy_analysis"} == {"scale", "notes"}

    # its file knob is staged by stage_inputs
    (tmp_path / "notes.txt").write_text("a note\n")
    text = BENZOIC + '\n[dummy_analysis]\nscale = 2.0\nnotes = "notes.txt"\n'
    staged, inputs = stage_inputs(text, tmp_path, tmp_path / "out")
    assert set(inputs) == {"dummy_analysis.notes"}
    copy = inputs["dummy_analysis.notes"]["copy"]
    assert (tmp_path / "out" / copy).read_text() == "a note\n"
    assert tomllib.loads(staged)["dummy_analysis"]["notes"] == copy

    # run_plan calls its hook after the measurement and merges what it returns into results[key]
    (tmp_path / "c.toml").write_text(text)
    plan = build_plan(load_config(tmp_path / "c.toml"))
    rec, _ = run_plan(plan, measure=fill)
    assert len(hook.calls) == 1
    _, seen_rec, ctx = hook.calls[0]
    assert seen_rec is rec and ctx["measure"] is fill
    parent = plan.parent.per_pose["dE_int"]
    assert rec.results["dummy_analysis"] == {"scaled_parent_mean": 2.0 * sum(parent) / len(parent), "saw_cut": True}
    # its knobs are inputs: the file by content, so the staged copy digests the same as the original
    assert plan.inputs["dummy_analysis"]["scale"] == 2.0
    assert plan.inputs["dummy_analysis"]["notes"]["name"] == "notes.txt"
    (tmp_path / "out" / "config.toml").write_text(staged)
    staged_plan = build_plan(load_config(tmp_path / "out" / "config.toml"))
    assert RunRecord("t", staged_plan.inputs, staged_plan.tiers, {}).input_digest == rec.input_digest

    # a partial run (--only-candidate) does not run it: the combine does
    hook.calls.clear()
    run_plan(build_plan(load_config(tmp_path / "c.toml")), measure=fill, partial=True)
    assert hook.calls == []


# ---------------------------------------------------------------------------------- the other three slot hooks


class TwoPoses:
    """A pose source: the parent's first two ETKDG poses, by another route than [poses]."""

    def parent_poses(self, cfg, parent, ctx):
        PairedPoses(n_poses=2, seed=11).run([parent], {"parent": parent})
        return list(parent.poses)


@dataclass
class OneChargeKnobs:
    q: float = 1.0


class OneCharge:
    def field(self, cfg, parent, ctx):
        return [PointCharge(float(cfg["one_charge"]["q"]), (4.0, 0.0, 0.0))]


@dataclass
class CheapTier:
    """A measurement tier that only declares itself; the test injects the numbers."""

    width: int = 1
    name: str = "cheap_tier"

    def settings(self):
        return {"width": self.width}

    def produces(self):
        return {"dE_int": "kcal/mol"}

    def systematic_floor(self, quantity):
        return None

    def estimate_cost(self, candidates):
        return {"quantity": "wall_time", "unit": "s", "predicted": None, "basis": "a test tier"}

    def run(self, candidates, ctx):
        raise AssertionError("the test injects the measurement")


class Cheap:
    def tier(self, cfg):
        return CheapTier(**cfg["cheap"])


@pytest.fixture
def slot_dummies():
    specs = [
        MethodSpec(
            "two_poses",
            "Two given poses",
            "geometry",
            "search",
            step="poses",
            slot="pose_source",
            section="two_poses",
            order=25,
            provides=("parent_poses",),
            exclusive=("docking",),
            params=(),
            hook=TwoPoses(),
        ),
        MethodSpec(
            "one_charge",
            "One charge",
            "input",
            "field",
            step="active_site",
            slot="field_source",
            section="one_charge",
            knobs=OneChargeKnobs,
            order=45,
            provides=("field",),
            exclusive=("pocket", "structure"),
            hook=OneCharge(),
        ),
        MethodSpec(
            "cheap",
            "Cheap measurement",
            "ab_initio",
            "measure",
            step="measure",
            slot="measure",
            section="cheap",
            knobs=CheapTier,
            order=95,
            needs=("poses", "field"),
            provides=("dE_int",),
            exclusive=("field_interaction", "field_dft_interaction"),
            hook=Cheap(),
        ),
    ]
    for s in specs:
        register(s)
    yield
    for s in specs:
        unregister(s.key)


def test_pose_field_and_measure_hooks_are_consulted(slot_dummies):
    cfg = tomllib.loads(BENZOIC)
    del cfg["pocket"]
    cfg.pop("poses")
    cfg.update(two_poses={}, one_charge={"q": -0.5}, cheap={"width": 3})
    validate_config(cfg)
    plan = build_plan(cfg)
    assert len(plan.parent.poses) == 2 and all(len(c.poses) == 2 for c in plan.candidates)
    assert plan.poses.parent_poses_given
    assert [(c.q, c.xyz_ang) for c in plan.field] == [(-0.5, (4.0, 0.0, 0.0))]
    assert isinstance(plan.tier, CheapTier) and plan.tier.width == 3
    assert {"name": "cheap_tier", "width": 3} in plan.tiers
    assert plan.inputs["one_charge"] == {"q": -0.5} and plan.inputs["two_poses"] == {}
    assert [e["stage"] for e in plan.stage_log][:2] == ["two_poses", "poses"]
    rec, out = run_plan(plan, measure=fill)
    assert out["cut"] is not None and rec.results["units"]["dE_int"] == "kcal/mol"

    # the Pipeline sees the measurement's alternative as satisfying the required RHF tier
    p = Pipeline.from_config(cfg)
    assert "field_interaction" not in p.keys() and "cheap" in p.keys()
    assert not [i for i in p.check() if i.level == "error" and "add 'field_interaction'" in i.message]

    # declared exclusions are refused, from either side
    with pytest.raises(ConfigError, match="cannot be combined"):
        validate_config({**cfg, "field_interaction": {}})
    with pytest.raises(ConfigError, match="pocket"):
        validate_config({**cfg, "pocket": {"file": "x.pdb"}})


# ------------------------------------------------------------------------------------- registering and removing


def _derived():
    return (
        list(catalog()),
        pipeline_mod.EXECUTION_ORDER,
        steps_mod.STEPS,
        cli.TOP_KEYS,
        cli.GATE_KEYS,
        run_inputs.INPUT_PATHS,
        cli.POSE_SECTIONS,
    )


def test_a_duplicate_key_is_refused_and_unregister_restores_state():
    before = _derived()
    spec = MethodSpec("tmp_method", "Tmp", "check", "gate", step="checks", order=65, gate_key="tmp_gate")
    register(spec)
    try:
        assert _derived() != before
        with pytest.raises(ValueError, match="already registered"):
            register(spec)
        with pytest.raises(ValueError, match="already registered"):
            register(MethodSpec("cut", "Another cut", "decision", "rank", order=300))
        with pytest.raises(ValueError, match=r"section \[poses\] is already 'paired_poses'"):
            register(MethodSpec("poses2", "Poses again", "geometry", "pair", section="poses", order=31))
        with pytest.raises(ValueError, match="unknown rung"):
            register(MethodSpec("bad", "Bad", "nowhere", "gate", order=1))
    finally:
        unregister("tmp_method")
    assert _derived() == before
    assert "bad" not in registry()
    with pytest.raises(KeyError):
        unregister("tmp_method")


def test_a_hook_must_implement_its_slot_protocol():
    with pytest.raises(TypeError, match="Analysis"):
        MethodSpec("x", "X", "ab_initio", "measure", slot="analysis", section="x", order=150, hook=object())
    with pytest.raises(ValueError, match="execution order"):
        MethodSpec("x", "X", "ab_initio", "measure")  # wired, but nowhere in the order


# ---------------------------------------------------------------------------------- existing configs, same digests

# `--plan` input digests at 7389aba, before the registry existed: computed in process (`build_plan`, as here) from
# a copy of 7389aba's src with the project venv's bin first on PATH, as scripts/build_prebuilt.py runs (ruling R12),
# so pdb2pqr30 is the venv's 3.8.0. The docking and structure values equal the committed prebuilt run folders
# (run-1a841ad89f2e, run-bc3482ef4170). A pocket read through pdb2pqr30 digests its PQR charges, so another
# pdb2pqr version gives other digests; those configs are skipped then, naming both versions. danuglipron-halogen is
# planned as `--only-candidate bnz-C34-F` would (the parent and one analogue): its full plan runs a 900 s MCS per
# analogue; the one-analogue plan takes about a minute.
PDB2PQR_AT_7389ABA = "pdb2pqr 3.8.0"
DIGESTS_AT_7389ABA = {
    "examples/mwe_benzoic.toml": "5f7df3db8c7b797ab6931b0b64ca8e201ec07a1792928f0be9bb856efe4f4aa6",
    "examples/mwe_docking.toml": "1a841ad89f2e0f2e7abb495aa0126e562209abea7013596f58d89856a7c891c6",
    "examples/mwe_structure.toml": "bc3482ef4170810fced726a207ac434bcec15b2f59e886d922600719bf7642b2",
    "src/smeltery/prebuilt/benzoic-pbe/pipeline.toml": (
        "6a37df69178bdfa9f9b294a3078736ab3d2fca1857de0d57f13d5f9d4938d72c"
    ),
    "src/smeltery/prebuilt/benzoic-rhf/pipeline.toml": (
        "5f7df3db8c7b797ab6931b0b64ca8e201ec07a1792928f0be9bb856efe4f4aa6"
    ),
    "src/smeltery/prebuilt/docking/pipeline.toml": "1a841ad89f2e0f2e7abb495aa0126e562209abea7013596f58d89856a7c891c6",
    "src/smeltery/prebuilt/structure/pipeline.toml": "bc3482ef4170810fced726a207ac434bcec15b2f59e886d922600719bf7642b2",
    "src/smeltery/prebuilt/danuglipron-halogen/pipeline.toml": (
        "67618b4d3edee7dc31e222e3afd880ca7030115f2d9c130950fdb606d50230f8"
    ),
}
ONLY_CANDIDATE = {"src/smeltery/prebuilt/danuglipron-halogen/pipeline.toml": "bnz-C34-F"}


def test_every_example_and_prebuilt_config_has_a_recorded_digest():
    found = {p.relative_to(ROOT).as_posix() for p in ROOT.glob("examples/*.toml")}
    found |= {p.relative_to(ROOT).as_posix() for p in ROOT.glob("src/smeltery/prebuilt/*/pipeline.toml")}
    assert found == set(DIGESTS_AT_7389ABA)


VENV_BIN = Path(sys.executable).parent


def _pdb2pqr_version() -> str | None:
    exe = shutil.which("pdb2pqr30", path=str(VENV_BIN) + os.pathsep + os.environ.get("PATH", ""))
    if exe is None:
        return None
    out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=120)
    lines = [x for x in (out.stdout + out.stderr).splitlines() if x.startswith("pdb2pqr ")]
    return lines[-1].strip() if lines else "an unknown version"


def _needs(text: str) -> str | None:
    """Why this config cannot be planned here, or None."""
    if "[docking]" in text or "[structure]" in text:
        missing = [m for m in ("vina", "meeko") if importlib.util.find_spec(m) is None]
        if missing:
            return f"needs the docking extra ({', '.join(missing)} missing)"
    if "[structure]" in text or ('file = "' in text and ".pdb" in text):
        found = _pdb2pqr_version()
        if found is None:
            return "needs pdb2pqr30"
        if found != PDB2PQR_AT_7389ABA:
            return f"digests were recorded with {PDB2PQR_AT_7389ABA}; the pdb2pqr30 found here is {found}"
    if "posebusters = true" in text and importlib.util.find_spec("posebusters") is None:
        return "needs the posebusters extra"
    return None


@pytest.mark.parametrize("config", sorted(DIGESTS_AT_7389ABA))
def test_existing_configs_keep_their_digests(config, tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(VENV_BIN) + os.pathsep + os.environ.get("PATH", ""))  # as build_prebuilt runs
    path = ROOT / config
    why = _needs(path.read_text())
    if why:
        pytest.skip(why)
    cfg = load_config(path)
    only = ONLY_CANDIDATE.get(config)
    if only is not None:  # what `--only-candidate` plans: the parent and that one analogue
        cfg = {**cfg, "candidates": {k: v for k, v in cfg["candidates"].items() if k in (cfg["parent"], only)}}
    plan = build_plan(cfg, out_dir=tmp_path)
    digest = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest
    assert digest == DIGESTS_AT_7389ABA[config]


def test_a_module_in_the_methods_package_registers_itself(tmp_path, monkeypatch):
    """Adding a method is adding a module: `register_methods()` in any module of `smeltery.methods` is called."""
    import sys

    import smeltery.methods as methods

    (tmp_path / "zz_probe_ext.py").write_text("CALLED = []\n\ndef register_methods():\n    CALLED.append(True)\n")
    monkeypatch.setattr(methods, "__path__", [*methods.__path__, str(tmp_path)])
    try:
        methods._register_extensions()
        assert sys.modules["smeltery.methods.zz_probe_ext"].CALLED == [True]
    finally:
        sys.modules.pop("smeltery.methods.zz_probe_ext", None)
