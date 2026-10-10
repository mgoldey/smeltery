"""The golden path (issue #33): the documented path actually runs, every registered tier is accounted for, and
every gate is proven reached by switching it off and watching the smoke test fail.

Scenario (tests/golden_path.py): ethanol, propanol, butanol (9, 12, 15 atoms), 2 poses each, ONE point charge,
STO-3G. The CLI (`smeltery run`, a subprocess, because PoseBusters cannot run under pytest's stderr capture) runs
PoseBusters + MMFF94 (+ GFN2 when an `xtb` binary exists) and the field-interaction SCFs, and the test asserts the
HONEST outcome, never a ranking: exit 0, UNRANKED, one tie group holding both analogues, per-stage in/out
counts, the record's tiers and floor source, and an `input_digest` equal across two identical runs.

Every gate G1..G9 of docs/tiers/index.md has a check that feeds it an input it refuses. The mutation sweep
(`test_disabling_one_gate_fails_the_smoke_test`) switches ONLY that gate off, in the library and in the CLI
subprocess, reruns the whole smoke test, and requires exactly the gate's own check (plus the reviewed
COLLATERAL) to fail. What the CLI itself applies is measured, not assumed: the golden CLI run is traced and
`test_the_cli_applies_exactly_g1_to_g4` fails if that set changes. G5..G9 are library-only today; they are
tested through the library call the docs name, and that is stated here, not hidden.

Tier accounting: a tier counts as reached only if its `run` RETURNED during the instrumented scenarios. Tiers
the environment cannot reach are skipped with a stated reason (see `golden_path.TIER_PLAN`); a registered tier
missing from that reviewed table fails `test_every_registered_tier_is_in_the_reviewed_plan`.

MEASURED WALL TIME (2026-10-10, 12-core dev box at load 23-34 from other jobs, nice 10, OPENBLAS_NUM_THREADS=1,
Python 3.11.14, ferric 0.1.0rc7 from PyPI, no xtb on PATH, as the CI `test` job): this module 140 s end to end
(23 passed, 7 skipped, the skips being declared gaps); with an xtb binary present 171 s. Of that, the unmutated
golden path (two full CLI runs + checks + library tiers) took 17-37 s and each of the 10 gate-mutation reruns
9-31 s. tests/test_docking_golden_path.py, run alone with Vina and pdb2pqr30: 15 s. NOT measured on a GitHub
runner (UNVERIFIED there); the asserted bounds (SMOKE_BOUND_S per run) are deliberately generous, ~10x.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from golden_path import (
    CHECKS,
    COLLATERAL,
    MUTATIONS,
    OPENMM_REASON,
    TIER_PLAN,
    Reach,
    install_tier_trace,
    replace_function,
    run_library_tiers,
    run_smoke,
    tier_accounting,
)

from smeltery.tier_registry import registered_tiers

ROOT = Path(__file__).resolve().parents[1]

#: The issue's budget is 10 minutes on a GitHub runner; each bound below is a single smoke run, far inside it.
SMOKE_BOUND_S = 300.0

needs_pb = pytest.mark.skipif(
    importlib.util.find_spec("posebusters") is None, reason="needs the posebusters extra (the CI test job has it)"
)

# ------------------------------------------------------------------------------ the catalogue is complete


def catalogue_gates() -> list[str]:
    text = (ROOT / "docs" / "tiers" / "index.md").read_text()
    return re.findall(r"^\| (G\d+) \|", text, flags=re.M)


def test_every_gate_in_the_docs_catalogue_has_a_check_and_a_mutation():
    gates = catalogue_gates()
    assert gates == [f"G{i}" for i in range(1, len(gates) + 1)] and len(gates) >= 9
    assert set(CHECKS) == set(gates), "docs/tiers/index.md and the smoke checks disagree: add the check + mutation"
    assert set(MUTATIONS) == set(gates)
    assert all(MUTATIONS[g] for g in gates)


# ------------------------------------------------------------------------------ the golden path, unmutated


@pytest.fixture(scope="module")
def golden():
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    smoke = run_smoke(None, twice=True, trace=True)
    completed: list[str] = []
    failed: list[str] = []
    undo = install_tier_trace(completed, failed)
    try:
        run_library_tiers()
    finally:
        undo()
    smoke.library_trace = {"completed": completed, "failed": failed}
    smoke.reached = set(smoke.cli_trace["completed"]) | set(completed)
    return smoke


@pytest.mark.needs_ferric
def test_the_golden_path_passes_every_check_and_is_fast(golden):
    assert golden.failing == set(), json.dumps({k: v for k, v in golden.results.items() if v}, indent=1)
    assert golden.seconds < SMOKE_BOUND_S, f"golden path took {golden.seconds:.0f}s"


@pytest.mark.needs_ferric
def test_input_digest_is_stable_across_two_runs_of_identical_inputs(golden):
    a, b = golden.records
    assert re.fullmatch(r"[0-9a-f]{64}", a["input_digest"])
    assert a["input_digest"] == b["input_digest"]
    assert a["results"] == b["results"]  # same inputs, same numbers and the same (non-)ranking
    assert a["results"]["cut"]["unranked"] is True


@pytest.mark.needs_ferric
def test_the_cli_applies_exactly_g1_to_g4(golden):
    """Measured by tracing the real CLI run. G5..G9 are NOT applied by `smeltery run`: paired_delta is called
    without tier=, cut without tier= or sensitivity=, and neither require_same_formula nor run_checked is called.
    That is what docs/tiers/index.md says; if the CLI starts applying one, this fails and the check for that
    gate must be extended to drive it through the CLI."""
    applied = set(golden.cli_trace["gates_applied"])
    assert applied == {"G1", "G2", "G3", "G4"}, golden.cli_trace["gates_applied"]


# ------------------------------------------------------------------------------ every tier is accounted for


def test_every_registered_tier_is_in_the_reviewed_plan():
    assert set(registered_tiers()) == set(TIER_PLAN), (
        "a tier was added (or removed) without updating golden_path.TIER_PLAN: give it a reach scenario "
        "or declare it unreachable with a reason"
    )


def test_the_accounting_fails_for_a_new_tier_and_for_a_tier_nothing_reached():
    names = set(TIER_PLAN)
    assert tier_accounting(names, TIER_PLAN, reached=names) == []
    new = tier_accounting(names | {"brand_new_tier"}, TIER_PLAN, reached=names)
    assert any("brand_new_tier" in p for p in new)
    gone = tier_accounting(names, {**TIER_PLAN, "ghost": Reach("cli")}, reached=names)
    assert any("ghost" in p for p in gone)
    unreached = tier_accounting(names, TIER_PLAN, reached=names - {"paired_poses"})
    assert any("'paired_poses'" in p and "no scenario reached" in p for p in unreached)


@pytest.mark.needs_ferric
@pytest.mark.parametrize("tier", sorted(set(registered_tiers()) | set(TIER_PLAN)))
def test_tier_is_reached(tier, golden):
    """Reach is measured: `run` of the tier returned during the CLI trace or the library scenarios."""
    assert tier in TIER_PLAN, f"{tier!r} is registered but not in golden_path.TIER_PLAN"
    reach = TIER_PLAN[tier]
    reason = reach.needs()
    if reach.via == "delegated":
        owner = ROOT / reach.note
        assert owner.is_file() and "def test_docking_tier_is_reached" in owner.read_text(), (
            f"{reach.note} must exist and assert that the docking tier is reached"
        )
        pytest.skip(reason)
    if reason is not None:
        pytest.skip(f"{tier}: {reason}")
    assert tier in golden.reached, f"{tier} was never reached (completed: {sorted(golden.reached)})"
    assert tier not in set(golden.cli_trace["failed"]) | set(golden.library_trace["failed"])


def test_the_forcefield_openmm_path_is_declared_not_exercised():
    pytest.skip(OPENMM_REASON)


# ------------------------------------------------------------------------------ mutation: disable one gate


def test_a_mutation_is_installed_everywhere_and_fully_undone():
    from smeltery import cli, funnel
    from smeltery import tier_floor as exported

    before = funnel.tier_floor
    assert cli.tier_floor is before and exported is before
    undo = MUTATIONS["G4"][0]()
    assert funnel.tier_floor is not before and cli.tier_floor is funnel.tier_floor
    import smeltery

    assert smeltery.tier_floor is funnel.tier_floor, "the package re-export must see the mutation too"
    undo()
    assert funnel.tier_floor is before and cli.tier_floor is before and smeltery.tier_floor is before


def test_a_mutation_that_matches_no_if_is_an_error_not_a_silent_pass():
    from golden_path import _if_false_factory

    with pytest.raises(AssertionError, match="wrong number"):
        replace_function(
            "smeltery.funnel", "tier_floor", _if_false_factory("smeltery.funnel", "tier_floor", ("no such test",))
        )


SWEEP = [(g, i) for g, variants in MUTATIONS.items() for i in range(len(variants))]


@pytest.mark.needs_ferric
@needs_pb
@pytest.mark.parametrize(("gate", "variant"), SWEEP, ids=[f"{g}-{i}" for g, i in SWEEP])
def test_disabling_one_gate_fails_the_smoke_test(gate, variant):
    smoke = run_smoke(f"{gate}:{variant}")
    want = {gate} | COLLATERAL.get(gate, set())
    detail = json.dumps({k: v for k, v in smoke.results.items() if v}, indent=1)
    assert gate in smoke.failing, f"disabling {gate} did NOT fail its check -- the gate is not reached:\n{detail}"
    assert smoke.failing == want, f"disabling {gate} failed {sorted(smoke.failing)}, expected {sorted(want)}:\n{detail}"
    assert smoke.seconds < SMOKE_BOUND_S
