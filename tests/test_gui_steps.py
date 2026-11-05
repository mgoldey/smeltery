"""The six steps a pipeline is drawn as: what each holds, and whether it is ready, off, changed or stale."""

import copy

from smeltery.gui.catalog import catalog
from smeltery.gui.pipeline import Pipeline
from smeltery.gui.steps import STEPS, steps


def _pipeline() -> Pipeline:
    p = Pipeline.minimal("c", "parent", {"parent": "CC", "a": "CCC", "b": "CCCC"})
    p.set("pocket", file="pocket.pdb")
    p.set("cut", keep=1, floor=0.5)
    return p


def _by_key(views):
    return {v.key: v for v in views}


def test_the_six_steps_cover_every_wired_method_once():
    assert [k for k, _, _ in STEPS] == ["active_site", "ligands", "poses", "checks", "measure", "cut"]
    placed = [m for _, _, ms in STEPS for m in ms]
    assert len(placed) == len(set(placed))
    wired = {k for k, m in catalog().items() if m.wired and not m.implicit}
    assert set(placed) == wired
    views = steps(_pipeline())
    assert [v.key for v in views] == [k for k, _, _ in STEPS]
    assert all(v.label for v in views)


def test_editing_poses_marks_poses_changed_and_measure_and_cut_stale():
    base = _pipeline()
    p = copy.deepcopy(base)
    p.set("paired_poses", n_poses=12)
    v = _by_key(steps(p, base))
    assert v["active_site"].state == "ready"
    assert v["ligands"].state == "ready"
    assert v["poses"].state == "changed"
    assert v["checks"].state == "off"
    assert v["measure"].state == "stale"
    assert v["cut"].state == "stale"
    assert all(x.state != "changed" and x.state != "stale" for x in steps(base, base))


def test_a_missing_measurement_is_needs_input_on_measure():
    p = _pipeline()
    p.remove("field_interaction")
    v = _by_key(steps(p))
    assert v["measure"].state == "needs_input"
    assert v["measure"].chosen is None
    assert any(i.level == "error" for i in v["measure"].issues)
    assert v["poses"].state == "ready"


def test_each_param_reports_its_previous_value_only_when_changed():
    base = _pipeline()
    p = copy.deepcopy(base)
    p.set("paired_poses", n_poses=12)
    params = {x["name"]: x for x in _by_key(steps(p, base, {"paired_poses": {"seed": 7}}))["poses"].params}
    assert params["n_poses"]["value"] == 12
    assert params["n_poses"]["changed_from"] == 6
    assert params["n_poses"]["default"] == 6
    assert params["seed"]["changed_from"] is None
    assert params["seed"]["workspace_default"] == 7
    assert params["n_poses"]["workspace_default"] is None
    # no baseline: nothing is "changed from"
    assert all(x["changed_from"] is None for x in _by_key(steps(p))["poses"].params)
    # setting a knob to its own default is not a change
    q = copy.deepcopy(base)
    q.set("paired_poses", n_poses=6)
    assert _by_key(steps(q, base))["poses"].state == "ready"


def test_the_fixture_pipeline_checks_clean():
    assert Pipeline.ok.fget(_pipeline())
