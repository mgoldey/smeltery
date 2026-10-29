"""`Condition`, `Cache`, `Step`, `Study`: the study layer, anchored on the mistakes it exists to stop.

Three anchors, each a real event in this project:

* `test_pooling_has_to_be_asked_for` -- pooling two arms' molecules moved a measured difference from
  -1.21 to -3.24 kcal/mol. With conditions as values, pooling is `project(())`, an operation written
  on purpose rather than one that happens when someone calls median on everything.
* `test_a_step_that_is_not_a_function_of_its_inputs_is_refused` -- a cache that silently served one
  of two differing results would put every downstream conclusion at the mercy of which run landed
  first.
* `test_changing_one_param_reruns_only_what_depends_on_it` -- the 180-run sweep and the 5.6 h ddE run
  each resumed only because of hand-written bookkeeping, one of which was wrong on the first try.
"""

from __future__ import annotations

import pytest

from smeltery.study import (
    Cache,
    CacheMismatchError,
    Condition,
    FunctionStep,
    Study,
    StudyError,
    common_fields,
    grid,
    step_key,
)

# ---------------------------------------------------------------- Condition


def test_a_condition_is_a_value_hashable_and_immutable():
    c = Condition(scaffold="s17", mode="k4")
    assert c["scaffold"] == "s17" and len(c) == 2
    assert c == Condition(mode="k4", scaffold="s17"), "field order must not matter"
    assert len({c, Condition(scaffold="s17", mode="k4")}) == 1, "usable as a dict key"
    with pytest.raises(AttributeError):
        c.scaffold = "s19"


def test_pooling_has_to_be_asked_for():
    """The anchor. `s17_k4` as a string cannot be grouped by scaffold; as a Condition it can, and
    discarding every factor -- which is what pooling IS -- is a call you have to write."""
    cells = [Condition(scaffold=s, mode=m) for s in ("s17", "s19") for m in ("k", "k4")]
    by_scaffold = {c.project(("scaffold",)) for c in cells}
    assert by_scaffold == {Condition(scaffold="s17"), Condition(scaffold="s19")}
    pooled = {c.project(()) for c in cells}
    assert pooled == {Condition()}, "projecting onto no factors collapses everything: that is pooling"
    assert "pooled" in repr(Condition())


def test_projection_refuses_a_factor_that_is_not_there():
    with pytest.raises(KeyError, match="target"):
        Condition(scaffold="s17").project(("target",))


def test_common_fields_is_what_a_comparison_may_group_by():
    a = [Condition(scaffold="s17", mode="k"), Condition(scaffold="s19", mode="k4")]
    b = [*a, Condition(scaffold="s17", mode="k", seed="100")]
    assert common_fields(a) == ("mode", "scaffold")
    assert common_fields(b) == ("mode", "scaffold"), "a factor only some conditions fix is not common"


def test_grid_is_the_design_as_a_value():
    g = grid(scaffold=["s17", "s19"], std=[0, 1, 2])
    assert len(g) == 6
    assert Condition(scaffold="s17", std="1") in g
    assert all(c.fields == ("scaffold", "std") for c in g)
    with pytest.raises(ValueError, match="no levels"):
        grid(scaffold=[])


def test_levels_are_labels_not_measurements():
    assert Condition(std=2)["std"] == "2"
    with pytest.raises(ValueError, match="no level"):
        Condition(std=None)


# ---------------------------------------------------------------- Cache


def test_a_step_that_is_not_a_function_of_its_inputs_is_refused(tmp_path):
    """The anchor. Serving one of two differing results would silently decide every downstream
    number by which run happened to land in the cache first."""
    c = Cache(tmp_path)
    k = step_key("score", "1", {"seed": 1}, {})
    c.put(k, step="score", version="1", params={"seed": 1}, inputs={}, value=[1.0, 2.0])
    c.put(k, step="score", version="1", params={"seed": 1}, inputs={}, value=[1.0, 2.0])  # idempotent
    with pytest.raises(CacheMismatchError, match="not a function of its declared inputs"):
        c.put(k, step="score", version="1", params={"seed": 1}, inputs={}, value=[1.0, 9.9])


def test_the_key_changes_with_anything_that_can_change_the_output():
    base = step_key("gen", "1", {"std": 2}, {"scaffold": "abc"})
    assert step_key("gen", "1", {"std": 3}, {"scaffold": "abc"}) != base, "a param"
    assert step_key("gen", "2", {"std": 2}, {"scaffold": "abc"}) != base, "the step's version"
    assert step_key("gen", "1", {"std": 2}, {"scaffold": "xyz"}) != base, "an upstream input"
    assert step_key("gen", "1", {"std": 2}, {"scaffold": "abc"}) == base, "and is stable otherwise"


def test_a_missing_entry_is_a_miss_not_an_error(tmp_path):
    assert Cache(tmp_path).get("nope") is None
    assert not Cache(tmp_path).has("nope")


# ---------------------------------------------------------------- Study


def _steps(log: list[str]) -> list[FunctionStep]:
    def gen(inputs, cond, ctx):
        log.append(f"gen:{cond.label()}")
        return [float(cond["std"]) * 10 + i for i in range(3)]

    def accept(inputs, cond, ctx):
        log.append(f"accept:{cond.label()}")
        return [v for v in inputs["samples"] if v >= 0]

    def score(inputs, cond, ctx):
        log.append(f"score:{cond.label()}")
        return sum(inputs["accepted"])

    return [
        FunctionStep("generate", "1", (), "samples", gen),
        FunctionStep("accept", "1", ("samples",), "accepted", accept),
        FunctionStep("score", "1", ("accepted",), "total", score),
    ]


def test_a_study_runs_its_dag_in_order_and_caches(tmp_path):
    log: list[str] = []
    s = Study("demo", _steps(log), grid(std=[0, 1]))
    assert s.check() == []
    r1 = s.run(Cache(tmp_path), verbose=False)
    assert len(r1) == 6 and not any(x.cached for x in r1)
    assert log.index("gen:std=0") < log.index("accept:std=0") < log.index("score:std=0")

    log.clear()
    r2 = s.run(Cache(tmp_path), verbose=False)
    assert all(x.cached for x in r2), "a second run re-executes nothing"
    assert log == [], "and does no work"


def test_changing_one_param_reruns_only_what_depends_on_it(tmp_path):
    """The anchor. A new setting must invalidate its own path and nothing else."""
    cache = Cache(tmp_path)
    log: list[str] = []
    Study("demo", _steps(log), grid(std=[0, 1])).run(cache, verbose=False)

    log.clear()
    # A third condition: only IT runs; std=0 and std=1 stay cached.
    Study("demo", _steps(log), grid(std=[0, 1, 2])).run(cache, verbose=False)
    assert sorted(log) == ["accept:std=2", "gen:std=2", "score:std=2"]


def test_bumping_a_step_version_invalidates_it_and_everything_downstream(tmp_path):
    cache = Cache(tmp_path)
    log: list[str] = []
    Study("demo", _steps(log), grid(std=[0])).run(cache, verbose=False)

    log.clear()
    steps = _steps(log)
    steps[1].version = "2"  # `accept` changed meaning
    Study("demo", steps, grid(std=[0])).run(cache, verbose=False)
    assert "gen:std=0" not in log, "upstream of the change is untouched"
    assert sorted(log) == ["accept:std=0", "score:std=0"], "the step and its downstream re-run"


def test_plan_says_what_would_run_without_running_it(tmp_path):
    cache = Cache(tmp_path)
    s = Study("demo", _steps([]), grid(std=[0, 1]))
    assert s.plan(cache) == {"study": "demo", "conditions": 2, "steps": 3, "cached": 0, "to_run": 6}
    s.run(cache, verbose=False)
    assert s.plan(cache)["to_run"] == 0 and s.plan(cache)["cached"] == 6


def test_a_missing_input_is_refused_before_anything_runs():
    s = Study("broken", [FunctionStep("score", "1", ("nothing_makes_this",), "total", lambda *_: 0)], grid(x=[1]))
    assert any("nothing provides" in p for p in s.check())
    with pytest.raises(StudyError, match="nothing provides"):
        s.run(Cache("/tmp/unused"), verbose=False)


def test_a_cycle_is_refused_and_named():
    s = Study(
        "cyclic",
        [
            FunctionStep("a", "1", ("b_out",), "a_out", lambda *_: 0),
            FunctionStep("b", "1", ("a_out",), "b_out", lambda *_: 0),
        ],
        grid(x=[1]),
    )
    assert any("cycle" in p for p in s.check())


def test_two_steps_cannot_provide_the_same_output():
    s = Study(
        "ambiguous",
        [
            FunctionStep("a", "1", (), "poses", lambda *_: 0),
            FunctionStep("b", "1", (), "poses", lambda *_: 0),
        ],
        grid(x=[1]),
    )
    assert any("provide" in p for p in s.check())


def test_no_conditions_is_refused_rather_than_silently_doing_nothing():
    assert any("no conditions" in p for p in Study("empty", _steps([]), []).check())


def test_the_dag_is_plain_data_for_a_viewer():
    rows = Study("demo", _steps([]), grid(std=[0])).dag()
    assert [r["step"] for r in rows] == ["generate", "accept", "score"]
    assert rows[1]["upstream"] == ["generate"] and rows[2]["upstream"] == ["accept"]
    assert rows[0]["needs"] == [] and rows[0]["provides"] == "samples"
