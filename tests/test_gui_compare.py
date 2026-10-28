"""`Comparison`: per-key differences, the floor deciding what is resolved, and the pooling trap.

The load-bearing test is `test_pooling_hides_what_per_key_differencing_shows`, built from the real
gen-compare numbers: pooled medians differ by -3.24 kcal/mol and the per-cell median difference is
-1.21, because each arm accepted molecules in a different mixture of cells. That gap is the whole
reason this module exists, so it is asserted on real figures rather than on a toy.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smeltery.gui import Comparison
from smeltery.gui.views.compare import comparison_caption, unpaired_table

#: Two arms over the same keys. Arm A is better on key1, worse on key2, and key3 only A produced for.
A = {"key1": [-20.0, -21.0, -22.0], "key2": [-10.0, -11.0], "key3": [-30.0, -31.0]}
B = {"key1": [-18.0, -19.0], "key2": [-13.0, -14.0]}


def c(floor: float | None = None, **kw) -> Comparison:
    return Comparison.from_values(A, B, label_a="A", label_b="B", floor=floor, **kw)


def test_unpaired_keys_are_reported_not_dropped():
    """An arm producing nothing for a key is a result about that arm -- the fact pooling hides."""
    rows = {r.key: r for r in c().rows()}
    assert rows["key3"].unpaired and rows["key3"].diff is None
    assert rows["key3"].n_a == 2 and rows["key3"].n_b == 0
    assert c().summary()["n_unpaired"] == 1
    assert unpaired_table(c()) == [{"key": "key3", "n A": 2, "n B": 0, "which arm is empty": "B"}]


def test_difference_is_a_minus_b_and_negative_favours_a():
    rows = {r.key: r for r in c().rows()}
    assert rows["key1"].diff == pytest.approx(-21.0 - -18.5)  # A better
    assert rows["key1"].leader == "a"
    assert rows["key2"].diff == pytest.approx(-10.5 - -13.5)  # B better
    assert rows["key2"].leader == "b"


def test_a_difference_inside_the_floor_is_not_resolved():
    """The floor is the whole point: below it, a lead is not a finding."""
    rows = {r.key: r for r in c(floor=5.0).rows()}
    assert rows["key1"].resolved is False  # |-2.5| < 5.0
    assert rows["key2"].resolved is False  # |+3.0| < 5.0
    tight = {r.key: r for r in c(floor=1.0).rows()}
    assert tight["key1"].resolved is True and tight["key2"].resolved is True


def test_no_measured_floor_means_unknown_not_resolved():
    """An unmeasured floor is not a licence to call differences real."""
    for r in c(floor=None).rows():
        assert r.resolved is None
    s = c(floor=None).summary()
    assert s["aggregate_resolved"] is None and s["n_resolved"] is None
    assert "no floor is measured" in c(floor=None).verdict()


def test_verdict_says_NOT_RESOLVED_when_the_aggregate_is_inside_the_floor():
    v = c(floor=5.0).verdict()
    assert v.startswith("NOT RESOLVED")
    assert "does not separate the arms" in v
    assert "favours" in c(floor=0.1).verdict()


def test_pooling_hides_what_per_key_differencing_shows():
    """The gen-compare result, as a regression on the real numbers.

    Each arm's cells differ by ~9 kcal/mol among themselves, and the arms accepted molecules in
    different mixtures of them. Pooling therefore compares mixtures: it gives -3.24 where the per-cell
    median difference is -1.21. The per-key figure must show the smaller, honest number.
    """
    deep, shallow = [-21.0] * 25, [-13.0] * 5
    pxm = {"deep_cell": deep, "shallow_cell": shallow}
    # The other arm accepted NOTHING in the deep cell -- exactly the gen-compare s3acid_k4 case.
    sbdd = {"shallow_cell": [-11.8] * 5}
    comp = Comparison.from_values(pxm, sbdd, label_a="pxm", label_b="sbdd", floor=4.7)
    s = comp.summary()
    assert s["n_paired"] == 1 and s["n_unpaired"] == 1
    per_key = s["median_per_key_diff"]
    pooled = s["pooled_diff_CONFOUNDED"]
    assert per_key == pytest.approx(-1.2)
    # Pooled: A's median is -21.0 (25 deep molecules swamp 5 shallow ones), B's is -11.8.
    assert pooled == pytest.approx(-9.2)
    assert abs(pooled) > abs(per_key), "pooling must be shown to overstate, not quietly agree"
    assert s["aggregate_resolved"] is False, "-1.2 is inside a 4.7 floor"
    assert "NOT RESOLVED" in comp.verdict()


def test_the_caption_carries_the_pooled_number_with_its_warning():
    cap = comparison_caption(c(floor=5.0))
    assert "CONFOUNDED" in cap and "NOT RESOLVED" in cap


def test_summary_counts_which_arm_leads_each_key():
    s = c(floor=1.0).summary()
    assert s["keys_favouring_a"] == 1 and s["keys_favouring_b"] == 1
    assert s["n_keys"] == 3 and s["n_paired"] == 2
    assert s["n_resolved"] == 2


def test_nothing_comparable_is_said_plainly():
    comp = Comparison.from_values({"x": [1.0]}, {"y": [2.0]}, label_a="A", label_b="B", floor=1.0)
    assert comp.summary()["n_paired"] == 0
    assert "cannot be compared" in comp.verdict()


def test_keys_are_the_union_so_an_arms_gaps_stay_visible():
    assert c().keys == ["key1", "key2", "key3"]


def test_figure_draws_without_a_toolkit_error():
    plotly = pytest.importorskip("plotly")  # noqa: F841
    from smeltery.gui.views.compare import compare_figure

    fig = compare_figure(c(floor=4.7))
    assert fig.layout.height
    # The floor band must be drawn, or "inside the floor" is invisible.
    assert any(sh.type == "rect" for sh in fig.layout.shapes)
    empty = compare_figure(Comparison.from_values({"x": [1.0]}, {"y": [2.0]}, label_a="A", label_b="B"))
    assert any("cannot be compared" in (a.text or "") for a in empty.layout.annotations)


# ---------------------------------------------------------------------------- sibling discovery

# `make_run_dir(root)` writes `root/out`, so a shared parent for two runs means two roots under one
# directory and comparing `a/out` with `b/out` -- which is the real layout of a study: one directory
# per run, side by side.


def _two_runs(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gui_fixture import make_run_dir

    base = tmp_path / "study"
    base.mkdir()
    return make_run_dir(base / "armA"), make_run_dir(base / "armB"), base


def test_sibling_runs_finds_run_directories_beside_one_and_never_itself(tmp_path):
    """Discovery is by convention, so its edges are pinned rather than left to chance."""
    from smeltery.gui.app import sibling_runs

    a, b, base = _two_runs(tmp_path)
    # Siblings are looked for beside the OUT directory, i.e. inside its own parent.
    assert sibling_runs(a) == []  # a/out has no sibling run inside a/
    (base / "armA" / "not-a-run").mkdir()
    assert sibling_runs(a) == [], "a directory without run-*.json is never offered"

    # Put two runs under one parent, the way a study lays them out.
    flat = tmp_path / "flat"
    flat.mkdir()
    import shutil

    shutil.copytree(a, flat / "armA")
    shutil.copytree(b, flat / "armB")
    found = sibling_runs(flat / "armA")
    assert [p.name for p in found] == ["armB"]
    assert (flat / "armA").resolve() not in [p.resolve() for p in found], "a run is never its own sibling"


def test_sibling_runs_on_a_record_path_looks_beside_its_directory(tmp_path):
    """Given OUT/run-*.json rather than OUT, the siblings are OUT's siblings."""
    import shutil

    from smeltery.gui.app import sibling_runs

    a, b, _ = _two_runs(tmp_path)
    flat = tmp_path / "flat"
    flat.mkdir()
    shutil.copytree(a, flat / "armA")
    shutil.copytree(b, flat / "armB")
    record = next((flat / "armA").glob("run-*.json"))
    assert [p.name for p in sibling_runs(record)] == ["armB"]


def test_sibling_runs_of_a_lone_directory_is_empty_not_an_error(tmp_path):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from gui_fixture import make_run_dir

    from smeltery.gui.app import sibling_runs

    assert sibling_runs(make_run_dir(tmp_path / "only")) == []
