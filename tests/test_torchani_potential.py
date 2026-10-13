"""The float64 torchani ANI-2x PotentialProvider (issue #22): the finite-difference bar the OpenMM-ML path cannot meet.

Tests needing the `ml-potential` extra skip with a reason when it is not installed, as in CI. See docs/potentials.md.
"""

import sys
from importlib import util

import numpy as np
import pytest

from smeltery import PointCharge
from smeltery.model import Pose
from smeltery.providers import (
    FORCE_FD_RELATIVE_BAR,
    PocketContextError,
    PotentialProvider,
    UnsupportedChargeStateError,
    check_forces,
    evaluate,
    relax,
)
from smeltery.providers.torchani_ani2x import TorchaniAni2x
from smeltery.structure import MissingBackend

# Acetic acid, a fixed non-equilibrium geometry (A), the same as tests/test_ml_potential.py.
SYMBOLS = ("C", "C", "O", "O", "H", "H", "H", "H")
XYZ = np.array(
    [
        [0.0, 0.0, 0.0],
        [1.5, 0.0, 0.0],
        [2.1, 1.05, 0.0],
        [2.1, -1.15, 0.2],
        [-0.4, 1.0, 0.0],
        [-0.4, -0.5, 0.9],
        [-0.4, -0.5, -0.9],
        [3.0, -1.0, 0.1],
    ]
)
POSE = Pose(SYMBOLS, XYZ)
CHARGES = [PointCharge(-0.5, (4.0, 0.0, 0.0))]

HAVE_TORCHANI = all(util.find_spec(m) is not None for m in ("torch", "torchani"))
needs_torchani = pytest.mark.skipif(
    not HAVE_TORCHANI, reason="ml-potential extra not installed: pip install 'smeltery[ml-potential]' (pulls torch)"
)


def test_a_missing_backend_names_the_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "torchani", None)
    with pytest.raises(MissingBackend, match="ml-potential"):
        TorchaniAni2x()


@needs_torchani
def test_flags_and_protocol_and_recorded_precision():
    p = TorchaniAni2x()
    assert isinstance(p, PotentialProvider)
    assert p.supports_external_charges is False
    assert p.supported_charge_states == frozenset({0})
    assert p.settings()["precision"] == "float64"


@needs_torchani
def test_in_pocket_and_charged_requests_are_refused_not_run_in_vacuum():
    p = TorchaniAni2x()
    with pytest.raises(PocketContextError):
        evaluate(p, POSE, CHARGES)
    with pytest.raises(PocketContextError):
        p.energy_and_forces(POSE, CHARGES)
    with pytest.raises(UnsupportedChargeStateError):
        evaluate(p, POSE, None, charge=-1)
    with pytest.raises(UnsupportedChargeStateError):
        p.energy_and_forces(POSE, None, charge=-1)


@needs_torchani
@pytest.mark.parametrize("step_ang", [1e-4, 1e-3])
def test_forces_meet_the_finite_difference_bar_against_the_providers_own_energy(step_ang):
    # 1e-2 is not asserted: central-difference truncation error (3e-3 measured) exceeds the bar there.
    assert check_forces(TorchaniAni2x(), POSE, step_ang=step_ang) < FORCE_FD_RELATIVE_BAR


@needs_torchani
def test_the_finite_difference_check_can_fail_a_wrong_force():
    class Flipped(TorchaniAni2x):
        def energy_and_forces(self, *a, **k):
            r = super().energy_and_forces(*a, **k)
            return type(r)(r.energy, -r.forces)

    assert check_forces(Flipped(), POSE, step_ang=1e-4) > 1.0


@needs_torchani
@pytest.mark.skipif(util.find_spec("openmmml") is None, reason="openmmml not installed")
def test_agrees_with_the_openmmml_provider_to_float32_precision():
    from smeltery.providers.openmm_ml import OpenMMMLAni2x

    a, b = evaluate(TorchaniAni2x(), POSE), evaluate(OpenMMMLAni2x(), POSE)
    assert abs(a.energy - b.energy) < 0.05  # the OpenMM-ML provider's declared energy_noise
    assert np.linalg.norm(a.forces - b.forces) / np.linalg.norm(a.forces) < 1e-4


@needs_torchani
def test_relax_runs_on_a_copy_and_lowers_the_energy():
    p = TorchaniAni2x()
    before = np.array(POSE.coords_ang, dtype=float)
    res = relax(p, POSE, max_steps=60)
    assert np.array_equal(POSE.coords_ang, before)
    assert res.energy < evaluate(p, POSE).energy
