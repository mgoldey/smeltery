"""PotentialProvider: the pocket guard, charge states, the exactness anchor and finite-difference forces (issue #22).

The test potentials here are analytic stand-ins with a known minimum. They test the
interface and the relaxer, NOT any ML potential: no ML backend is exercised in this file.
"""

import numpy as np
import pytest

from smeltery import PointCharge
from smeltery.model import Pose
from smeltery.providers import (
    FORCE_FD_RELATIVE_BAR,
    PocketContextError,
    PotentialProvider,
    PotentialResult,
    UnsupportedChargeStateError,
    check_forces,
    evaluate,
    relax,
)

SYMBOLS = ("O", "H", "H")
MINIMUM = np.array([[0.0, 0.0, 0.117], [0.0, 0.757, -0.469], [0.0, -0.757, -0.469]])
START = MINIMUM + np.array([[0.30, -0.20, 0.10], [-0.25, 0.15, 0.20], [0.10, 0.30, -0.15]])
CHARGES = [PointCharge(0.5, (3.0, 0.0, 0.0))]


class Harmonic:
    """E = 1/2 k |x - x0|^2 per coordinate, known minimum x0. Records the charges it was given."""

    def __init__(self, k=40.0, sees_charges=True, states=frozenset({0})):
        self.name = "harmonic-test"
        self.k = k
        self.supports_external_charges = sees_charges
        self.supported_charge_states = states
        self.received = "never called"

    def energy_and_forces(self, pose, point_charges=None, charge=0):
        self.received = point_charges
        d = np.asarray(pose.coords_ang) - MINIMUM
        return PotentialResult(0.5 * self.k * float((d * d).sum()), -self.k * d)

    def settings(self):
        return {"k": self.k}


class Anharmonic(Harmonic):
    """Cubic + quartic terms, so the finite-difference check is not trivially exact."""

    def energy_and_forces(self, pose, point_charges=None, charge=0):
        d = np.asarray(pose.coords_ang) - MINIMUM
        e = 0.5 * self.k * (d * d).sum() + 3.0 * (d**3).sum() + 5.0 * (d**4).sum()
        return PotentialResult(float(e), -(self.k * d + 9.0 * d**2 + 20.0 * d**3))


class WrongForces(Anharmonic):
    """Forces off by a few percent: the finite-difference check must reject this."""

    def energy_and_forces(self, pose, point_charges=None, charge=0):
        r = super().energy_and_forces(pose, point_charges, charge)
        return PotentialResult(r.energy, r.forces * 1.03)


def pose(xyz=START):
    return Pose(SYMBOLS, np.array(xyz, dtype=float))


def test_a_potential_that_cannot_see_the_pocket_raises_instead_of_running_in_vacuum():
    blind = Harmonic(sees_charges=False)
    with pytest.raises(PocketContextError, match="vacuum result"):
        evaluate(blind, pose(), CHARGES)
    with pytest.raises(PocketContextError):
        evaluate(blind, pose(), [])  # an empty pocket is still an in-pocket request
    assert blind.received == "never called"  # it was refused before the potential ran
    assert evaluate(blind, pose(), None).energy > 0  # asking for vacuum explicitly is fine


def test_a_potential_that_sees_charges_is_handed_them():
    sighted = Harmonic(sees_charges=True)
    evaluate(sighted, pose(), CHARGES)
    assert sighted.received == CHARGES


def test_an_unsupported_charge_state_raises():
    neutral_only = Harmonic(states=frozenset({0}))
    with pytest.raises(UnsupportedChargeStateError, match=r"\[0\], not -1"):
        evaluate(neutral_only, pose(), charge=-1)
    assert evaluate(Harmonic(states=frozenset({-1, 0})), pose(), charge=-1).energy > 0


def test_forces_of_the_wrong_shape_are_rejected():
    class Bad(Harmonic):
        def energy_and_forces(self, pose, point_charges=None, charge=0):
            return PotentialResult(0.0, np.zeros((2, 3)))

    with pytest.raises(ValueError, match="shape"):
        evaluate(Bad(), pose())


def test_relaxation_reaches_the_known_minimum_to_1e_6():
    provider = Harmonic()
    r = relax(provider, pose())
    assert r.converged
    assert np.abs(r.coords_ang - MINIMUM).max() < 1e-6
    assert r.energy < 1e-9


def test_relaxation_also_reaches_the_minimum_of_an_anharmonic_potential():
    r = relax(Anharmonic(), pose())
    assert r.converged
    assert np.abs(r.coords_ang - MINIMUM).max() < 1e-6


def test_relaxation_never_writes_into_the_input_pose():
    start = pose()
    before = start.coords_ang.copy()
    relax(Harmonic(), start)
    assert np.array_equal(start.coords_ang, before)


def test_relaxation_reports_non_convergence_instead_of_pretending():
    r = relax(Harmonic(), pose(), max_steps=1)
    assert not r.converged and r.max_force > 1e-8


def test_relaxation_in_a_pocket_goes_through_the_guard():
    with pytest.raises(PocketContextError):
        relax(Harmonic(sees_charges=False), pose(), CHARGES)


def test_forces_agree_with_a_finite_difference_of_the_energy_within_the_recorded_bar():
    assert FORCE_FD_RELATIVE_BAR == 1e-4
    for provider in (Harmonic(), Anharmonic()):
        assert check_forces(provider, pose()) < FORCE_FD_RELATIVE_BAR


def test_the_finite_difference_check_can_fail():
    """A check nobody has seen fail is an assumption: forces off by 3% are far outside the bar."""
    assert check_forces(WrongForces(), pose()) > 100 * FORCE_FD_RELATIVE_BAR


def test_the_check_refuses_to_judge_a_stationary_point():
    with pytest.raises(ValueError, match="displaced geometry"):
        check_forces(Harmonic(), pose(MINIMUM))


def test_the_test_potentials_satisfy_the_protocol():
    assert isinstance(Harmonic(), PotentialProvider)
