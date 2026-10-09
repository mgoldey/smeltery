"""Cost model: schema for every tier, honest None, DFT port, and prediction vs measurement."""

import re
from pathlib import Path

import numpy as np
import pytest

from smeltery import Candidate, Pose, cost
from smeltery.tiers import FieldInteraction, PairedPoses

KEYS = {"quantity", "unit", "predicted", "basis"}
ACID = "OC(=O)c1ccccc1"


def _posed(smiles=ACID, n=2):
    c = Candidate("c", smiles)
    PairedPoses(n_poses=n).run([c], {"parent": c})
    return c


def _tiers():
    return [PairedPoses(), FieldInteraction()]


@pytest.mark.parametrize("tier", _tiers(), ids=lambda t: t.name)
def test_every_tier_estimate_cost_has_the_four_keys(tier):
    for cands in ([], [Candidate("c", ACID)], [_posed()]):
        est = tier.estimate_cost(cands)
        assert set(est) == KEYS, est
        assert isinstance(est["quantity"], str) and isinstance(est["unit"], str)
        assert est["predicted"] is None or est["predicted"] > 0
        assert isinstance(est["basis"], str) and est["basis"]


def test_tier_with_no_measurement_returns_none_not_a_guess():
    est = PairedPoses().estimate_cost([Candidate("a", ACID), Candidate("b", ACID)])
    assert est["predicted"] is None
    assert est["basis"].startswith("unmeasured")


def test_field_interaction_is_none_outside_what_was_measured():
    posed = _posed()
    assert FieldInteraction(basis="def2-svp").estimate_cost([posed])["predicted"] is None  # other basis
    assert FieldInteraction().estimate_cost([Candidate("c", ACID)])["predicted"] is None  # no poses yet
    chloro = Candidate("cl", "CCl")
    chloro.poses = [Pose(("C", "Cl", "H"), np.zeros((3, 3)))]  # Cl is not in the STO-3G table
    assert FieldInteraction().estimate_cost([chloro])["predicted"] is None
    assert FieldInteraction().estimate_cost([posed])["predicted"] > 0


def test_prediction_scales_with_pose_count():
    one = FieldInteraction().estimate_cost([_posed(n=1)])["predicted"]
    three = FieldInteraction().estimate_cost([_posed(n=3)])["predicted"]
    assert three == pytest.approx(3 * one)


def test_rhf_prediction_within_25_percent_of_measured_validation_runs():
    """Validation molecules were never used in the exponent fit (see cost.py, scripts/measure_cost.py).

    Predicted and measured numbers are in the assertion message and the committed
    fixture; measured values are single-threaded wall seconds on the recorded machine.
    """
    record = cost.load_measurements()
    cal = cost.fit_rhf_calibration(record)
    validation = [s for s in record["samples"] if s["role"] == "validation"]
    assert validation
    for s in validation:
        predicted = cal.predicted_seconds_per_pose(s["n_basis_functions"])
        measured = s["seconds_per_pose"]
        assert abs(predicted - measured) / measured <= 0.25, (
            f"{s['name']}: predicted {predicted:.2f} s vs measured {measured:.2f} s"
        )


def test_tier_estimate_uses_the_fixture_model():
    record = cost.load_measurements()
    s = next(x for x in record["samples"] if x["role"] == "validation")
    c = _posed(smiles=s["smiles"], n=1)
    assert cost.sto3g_basis_functions(list(c.poses[0].symbols)) == s["n_basis_functions"]
    got = FieldInteraction().estimate_cost([c])["predicted"]
    assert got == pytest.approx(cost.rhf_calibration().predicted_seconds_per_pose(s["n_basis_functions"]))


def test_dft_port_matches_ferric_structure():
    assert cost.GRID_POINTS_PER_ATOM == 8250
    size = cost.DftSize(n_atoms=10, n_basis_functions=20)
    assert size.grid_points == 82500
    assert size.xc_fock_work == 20**2 * 82500
    ref = cost.DftSize(5, 10)
    assert size.predicted_seconds(ref, 2.0) == pytest.approx(2.0 * size.xc_fock_work / ref.xc_fock_work)
    # alkanes C10H22 (32 atoms) and C20H42 (62 atoms): nbf 82 and 162, so work ratio = (162/82)^2 * 62/32
    a32 = cost.DftSize(32, cost.sto3g_basis_functions(["C"] * 10 + ["H"] * 22))
    a62 = cost.DftSize(62, cost.sto3g_basis_functions(["C"] * 20 + ["H"] * 42))
    assert (a32.n_basis_functions, a62.n_basis_functions) == (72, 142)
    assert a62.xc_fock_work / a32.xc_fock_work == pytest.approx((142 / 72) ** 2 * 62 / 32)


def test_dft_constants_name_their_ferric_source_file():
    src = Path(cost.__file__).read_text().splitlines()
    for const, fname in (("N_RADIAL", "grid.rs"), ("AO_CACHE_PLANES", "ks.rs")):
        i = next(k for k, line in enumerate(src) if line.startswith(const))
        window = "\n".join(src[max(0, i - 6) : i])
        assert re.search(rf"# Source: ferric .*{re.escape(fname)}", window), const
