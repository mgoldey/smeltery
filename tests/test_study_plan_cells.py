import pytest

from smeltery.study import Cache, FunctionStep, Study, grid


def _steps(_):
    def f(inputs, cond, ctx):
        return 1

    return [FunctionStep("s", "v1", (), "x", f)]


@pytest.fixture
def cache(tmp_path):
    return Cache(tmp_path)


@pytest.fixture
def study():
    return Study("demo", _steps([]), grid(std=[0, 1]))


def test_plan_cells_returns_one_row_per_condition_and_step(study, cache):
    rows = study.plan_cells(cache)
    assert len(rows) == len(study.conditions) * len(study.steps)


def test_plan_cells_totals_agree_with_plan(study, cache):
    rows, totals = study.plan_cells(cache), study.plan(cache)
    assert sum(r["state"] == "cached" for r in rows) == totals["cached"]
    assert sum(r["state"] == "to_run" for r in rows) == totals["to_run"]


def test_a_cached_step_is_reported_cached_for_that_condition_only(study, cache):
    rows = study.plan_cells(cache)
    assert {r["state"] for r in rows} <= {"cached", "to_run"}


def test_plan_cells_uses_the_same_keys_as_plan(study, cache):
    assert all(isinstance(r["key"], str) and r["key"] for r in study.plan_cells(cache))
