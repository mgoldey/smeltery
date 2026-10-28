"""The GUI helpers: the catalog cannot drift from the CLI or the tier registry, and a pipeline checks its dataflow,
compiles to a config the CLI accepts, and reads every example config back unchanged."""

import json
import re
import tomllib
from pathlib import Path

import pytest

from smeltery import cli
from smeltery.gui import EXECUTION_ORDER, Pipeline, PipelineError, catalog, catalog_json, floor_status, hierarchy
from smeltery.gui._toml import dumps
from smeltery.gui.catalog import RUNGS, params_from_dataclass
from smeltery.tier_registry import registered_tiers
from smeltery.tiers import FieldInteraction, PairedPoses

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = sorted((ROOT / "examples").glob("*.toml"))
CANDS = {"benzoic": "OC(=O)c1ccccc1", "4-F": "OC(=O)c1ccc(F)cc1", "4-Cl": "OC(=O)c1ccc(Cl)cc1"}
SITE = {"q": 1.0, "atom": 0, "from_atom": 1, "distance": 3.0}


def errors(p: Pipeline) -> list[tuple[str | None, str]]:
    return [(i.stage, i.message) for i in p.check() if i.level == "error"]


def runnable() -> Pipeline:
    p = Pipeline.minimal("t", "benzoic", CANDS)
    p.set("pocket", site=[SITE])
    p.set("cut", keep=1, floor=0.0)
    return p


# ---------------------------------------------------------------- catalog


def test_every_registered_tier_is_in_the_hierarchy():
    assert set(registered_tiers()) <= {m.tier for m in catalog().values()}


def test_every_wired_placeable_method_has_an_execution_slot_and_unwired_ones_do_not():
    for k, m in catalog().items():
        assert (k in EXECUTION_ORDER) == m.wired, k


def test_section_knobs_are_the_cli_keys():
    by_key = catalog()
    assert {p.name for p in by_key["structure"].params} == cli.STRUCTURE_KEYS
    assert {p.name for p in by_key["docking"].params} == cli.DOCKING_KEYS
    assert {p.name for p in by_key["cut"].params} == cli.CUT_KEYS
    assert {p.name for p in by_key["pocket"].params} == cli.POCKET_KEYS
    assert {m.gate_key for m in by_key.values() if m.gate_key} == cli.GATE_KEYS
    assert {p.name for m in by_key.values() if m.gate_key for p in m.params} == cli.GATE_OPTION_KEYS
    # tier knobs: a subset of what the CLI lets through, at the tier's own defaults
    for key, cls in (("paired_poses", PairedPoses), ("field_interaction", FieldInteraction)):
        m = by_key[key]
        knobs = {p.name: p.default for p in m.params}
        cli._knobs({m.section: dict.fromkeys(knobs, 0)}, m.section, cls)  # raises on a knob the CLI refuses
        inst = cls()
        assert all(getattr(inst, k) == v for k, v in knobs.items())


def test_a_tier_knob_without_a_widget_is_refused_not_dropped():
    from dataclasses import dataclass, field

    @dataclass
    class T:
        weights: dict = field(default_factory=dict)

    with pytest.raises(TypeError, match="weights"):
        params_from_dataclass(T)


def test_hierarchy_is_ordered_by_rung_and_covers_every_method():
    rungs = hierarchy()
    assert [r.name for r in rungs] == [name for name, _ in RUNGS]
    assert sorted(m.key for r in rungs for m in r.methods) == sorted(catalog())
    assert all(m.wired for r in hierarchy(include_unwired=False) for m in r.methods)


def test_catalog_json_is_plain_json_and_states_what_is_unwired():
    j = json.loads(json.dumps(catalog_json()))
    assert j["methods"]["qmmm"]["wired"] is False and j["methods"]["qmmm"]["why_unwired"]
    assert j["methods"]["field_interaction"]["produces"] == {"dE_int": "kcal/mol"}
    assert "qmmm" not in catalog_json(include_unwired=False)["methods"]


def test_floor_status_agrees_with_the_tier_index_where_it_can_be_derived():
    table = (ROOT / "docs" / "tiers" / "index.md").read_text()
    docs = {m[0]: m[1] for m in re.findall(r"^\| \[(\w+)\]\(\w+\.md\) \| \w+ \| \w+ \| (\S+) \|$", table, re.M)}
    assert docs, "the tier index table did not parse"
    for m in catalog().values():
        status = floor_status(m)
        if m.tier and status != "unknown":
            assert status == docs[m.tier], m.key


# ---------------------------------------------------------------- pipeline


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_every_example_round_trips_and_checks_clean(path):
    cfg = tomllib.loads(path.read_text())
    p = Pipeline.from_config(cfg)
    assert errors(p) == []
    assert p.to_config() == cfg
    assert tomllib.loads(p.to_toml()) == cfg


def test_minimal_pipeline_reports_every_missing_knob_at_once():
    p = Pipeline.minimal("t", "benzoic", CANDS)
    got = errors(p)
    assert ("cut", "keep is required") in got
    assert any(s == "cut" and "floor" in m for s, m in got)  # FieldInteraction's floor is unmeasured
    p.set("cut", keep=1, floor=0.0)
    assert [s for s, _ in errors(p)] == ["pocket"]  # now the config validator: the pocket has no charges
    p.set("pocket", site=[SITE])
    assert errors(p) == []
    assert p.compile() == {
        "campaign": "t",
        "parent": "benzoic",
        "candidates": CANDS,
        "pocket": {"site": [SITE]},
        "cut": {"keep": 1, "floor": 0.0},
    }


def test_compile_refuses_with_every_error():
    p = Pipeline.minimal("t", "benzoic", CANDS)
    with pytest.raises(PipelineError) as e:
        p.compile()
    assert len(e.value.issues) >= 2


def test_dataflow_refuses_a_stage_before_what_it_needs():
    p = runnable()
    p.move("cut", 0)
    assert any(s == "cut" and "dE_int" in m for s, m in errors(p))
    p = runnable()
    p.move("field_interaction", 0)
    msgs = [m for s, m in errors(p) if s == "field_interaction"]
    assert any("poses" in m for m in msgs) and any("field" in m for m in msgs)


def test_order_that_moves_no_data_is_a_note_with_the_real_order():
    p = runnable()
    p.add("forcefield")
    p.add("posebusters", at=p.keys().index("forcefield") + 1)
    assert errors(p) == []
    notes = [i.message for i in p.check() if i.level == "note" and i.stage is None]
    assert notes and "posebusters -> forcefield" in notes[0]


def test_unwired_tiers_are_shown_but_not_runnable():
    p = runnable()
    p.add("qmmm")
    assert any(s == "qmmm" and "cannot run" in m for s, m in errors(p))
    assert "qmmm" not in runnable().addable()


def test_structure_excludes_pocket_and_needs_docking():
    p = runnable()
    p.add("structure", {"spec": "x.pdb", "center": [0.0, 0.0, 0.0], "pocket_cutoff": 12.0})
    msgs = errors(p)
    assert any("cannot be combined" in m for _, m in msgs)
    assert any(s == "structure" and "docking" in m for s, m in msgs)
    p.remove("pocket")
    p.add("docking")
    assert errors(p) == []
    assert "pocket" not in p.addable()


def test_docking_without_structure_needs_its_own_receptor():
    p = runnable()
    p.add("docking")
    assert any(s == "docking" and "receptor" in m for s, m in errors(p))
    p.set("docking", receptor="r.pdbqt", box_center=[0.0, 0.0, 0.0])
    assert errors(p) == []
    p.set("paired_poses", n_poses=4)  # the CLI refuses n_poses once the parent is docked; the validator says so
    assert [s for s, _ in errors(p)] == ["paired_poses"]


def test_validator_errors_are_attributed_to_their_stage():
    p = runnable()
    p.set("cut", keep=5)
    assert [s for s, _ in errors(p)] == ["cut"]


def test_knob_types_are_checked_before_the_validator():
    p = runnable()
    p.set("field_interaction", max_iter="lots")
    assert errors(p) == [("field_interaction", "max_iter must be an int, got 'lots'")]
    with pytest.raises(KeyError, match="no param"):
        p.set("cut", kept=1)


def test_posebusters_without_relaxed_substituents_is_a_warning():
    p = runnable()
    p.add("posebusters")
    assert any(i.level == "warning" and i.stage == "posebusters" for i in p.check())
    p.set("paired_poses", relax_unmapped=True)
    assert not any(i.level == "warning" for i in p.check())


def test_view_counts_only_the_cut_removing_analogues():
    p = runnable()
    p.add("posebusters")
    rows = {r["stage"]: r for r in p.view()}
    assert list(rows) == ["paired_poses", "pocket", "parity", "posebusters", "field_interaction", "cut"]
    assert rows["parity"]["implicit"] and rows["posebusters"]["candidates_out"] == 3
    assert (rows["cut"]["candidates_in"], rows["cut"]["candidates_out"]) == (2, 1)


def test_add_refuses_implicit_and_duplicate_stages():
    p = runnable()
    with pytest.raises(ValueError, match="always runs"):
        p.add("parity")
    with pytest.raises(ValueError, match="already"):
        p.add("cut")


# ---------------------------------------------------------------- toml


def test_toml_writer_round_trips_config_shapes():
    d = {
        "campaign": 'a "quoted" name',
        "flag": False,
        "n": 3,
        "x": 1e-10,
        "candidates": {"4-F": "F", "with space": "C"},
        "pocket": {"site": [SITE, {**SITE, "q": -1.0}]},
        "box": {"size": [22.0, 16.0, 16.0], "seeds": [7]},
    }
    assert tomllib.loads(dumps(d)) == d
