"""The OpenMM-ML / ANI-2x PotentialProvider (issue #22).

Tests that need the `ml-potential` extra (openmm, openmmml, torchani) skip with a reason when it is not
installed, as in CI. The flag and missing-backend tests run everywhere. Measured results: docs/potentials.md.
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
from smeltery.providers.openmm_ml import FORCE_SCALE_BY_OPENMMML_VERSION, OpenMMMLAni2x
from smeltery.structure import MissingBackend

HAVE_ML = all(util.find_spec(m) is not None for m in ("openmm", "openmmml", "torchani"))
needs_ml = pytest.mark.skipif(
    not HAVE_ML, reason="ml-potential extra not installed: pip install 'smeltery[ml-potential]' (pulls torch)"
)

# Acetic acid, a fixed non-equilibrium geometry (Å): nonzero forces, no RDKit needed.
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


def test_the_flags_say_what_ani2x_cannot_do_without_importing_the_backend():
    # Class attributes: readable with no torch installed. ANI-2x has no charge input and no embedding.
    assert OpenMMMLAni2x.supports_external_charges is False
    assert OpenMMMLAni2x.supported_charge_states == frozenset({0})
    assert OpenMMMLAni2x.license_id == "MIT"
    assert FORCE_SCALE_BY_OPENMMML_VERSION == {"1.8": 10.0}


def test_a_missing_backend_names_the_extra(monkeypatch):
    for mod in ("openmm", "openmmml", "torchani"):
        monkeypatch.setitem(sys.modules, mod, None)  # makes `import mod` raise ImportError
    with pytest.raises(MissingBackend, match=r"smeltery\[ml-potential\]"):
        OpenMMMLAni2x()


@needs_ml
def test_it_is_a_potential_provider_and_records_versions():
    p = OpenMMMLAni2x()
    assert isinstance(p, PotentialProvider)
    s = p.settings()
    assert s["openmm"] and s["torchani"] and s["license_id"] == "MIT" and s["force_scale"] == 10.0


@needs_ml
def test_in_pocket_use_raises_end_to_end_with_the_real_backend():
    p = OpenMMMLAni2x()
    with pytest.raises(PocketContextError, match="vacuum result"):
        evaluate(p, POSE, CHARGES)
    with pytest.raises(PocketContextError):
        evaluate(p, POSE, [])
    with pytest.raises(PocketContextError):  # calling the method directly does not bypass the refusal
        p.energy_and_forces(POSE, CHARGES)
    assert p._contexts == {}  # refused before any model was built
    assert evaluate(p, POSE, None).energy < 0  # explicit vacuum is fine


@needs_ml
def test_charged_states_are_refused_not_run_as_neutral():
    p = OpenMMMLAni2x()
    for q in (-1, 1):
        with pytest.raises(UnsupportedChargeStateError):
            evaluate(p, POSE, None, q)
        with pytest.raises(UnsupportedChargeStateError):
            p.energy_and_forces(POSE, None, q)


@needs_ml
def test_openmmml_itself_ignores_the_charge_argument_which_is_why_the_provider_must_refuse():
    # The measurement behind supported_charge_states == {0} (docs/potentials.md): the unguarded stack accepts
    # charge=-1 and returns the identical neutral-model energy. If this ever fails, upstream changed: re-measure.
    import openmm
    import openmmml
    from openmm import app

    top = app.Topology()
    res = top.addResidue("M", top.addChain())
    for i, s in enumerate(SYMBOLS):
        top.addAtom(f"{s}{i}", app.Element.getBySymbol(s), res)

    def energy(**kw):
        system = openmmml.MLPotential("ani2x").createSystem(top, device="cpu", **kw)
        ctx = openmm.Context(system, openmm.VerletIntegrator(1e-3), openmm.Platform.getPlatformByName("Reference"))
        ctx.setPositions(XYZ / 10.0)
        return ctx.getState(getEnergy=True).getPotentialEnergy()._value

    assert energy(charge=-1) == energy()


@needs_ml
def test_forces_match_torchanis_own_float64_finite_difference():
    # Independent of the provider and of openmmml: float64 torchani FD of torchani's energy. This is what
    # catches openmmml 1.8's 10x force unit slip (the provider's force_scale undoes it; force_scale=1.0 fails).
    import torch
    import torchani

    model = torchani.models.ANI2x(periodic_table_index=True, dtype=torch.float64)
    z = torch.tensor([[{"H": 1, "C": 6, "O": 8}[s] for s in SYMBOLS]])
    h2kcal = torchani.units.hartree2kcalmol(1)

    def e(c):
        return float(model((z, torch.tensor(c, dtype=torch.float64).unsqueeze(0)))[1]) * h2kcal

    fd = np.zeros_like(XYZ)
    for i in range(XYZ.shape[0]):
        for k in range(3):
            up, dn = XYZ.copy(), XYZ.copy()
            up[i, k] += 1e-3
            dn[i, k] -= 1e-3
            fd[i, k] = -(e(up) - e(dn)) / 2e-3
    f = evaluate(OpenMMMLAni2x(), POSE).forces
    rel = np.linalg.norm(f - fd) / np.linalg.norm(fd)
    assert rel < 1e-3, rel  # measured 1.0e-4: float32 autograd precision, not the FD bar
    wrong = evaluate(OpenMMMLAni2x(force_scale=1.0), POSE).forces
    assert np.linalg.norm(wrong - fd) / np.linalg.norm(fd) > 0.5  # the unit slip is large and detected


@needs_ml
def test_an_unmeasured_openmmml_version_is_refused(monkeypatch):
    import importlib.metadata as md

    real = md.version
    monkeypatch.setattr(md, "version", lambda name: "99.0" if name == "openmmml" else real(name))
    with pytest.raises(RuntimeError, match="has not been measured"):
        OpenMMMLAni2x()
    assert OpenMMMLAni2x(force_scale=10.0).force_scale == 10.0  # an explicit, measured scale is accepted


@needs_ml
def test_unsupported_elements_raise():
    with pytest.raises(ValueError, match="Br"):
        OpenMMMLAni2x().energy_and_forces(Pose(("C", "Br"), np.array([[0.0, 0, 0], [1.9, 0, 0]])))


@needs_ml
def test_relax_runs_on_a_copy_and_lowers_the_energy():
    p = OpenMMMLAni2x()
    before = XYZ.copy()
    r = relax(p, POSE, fmax=0.05, max_steps=300)
    assert np.array_equal(POSE.coords_ang, before)
    assert r.converged and r.energy < evaluate(p, POSE).energy


@needs_ml
@pytest.mark.xfail(
    strict=True,
    reason="MEASURED NEGATIVE: openmmml's ANI path is float32 (energies ~1.4e5 kcal/mol, resolution ~0.016), so a "
    "finite difference of the provider's own energy cannot reach FORCE_FD_RELATIVE_BAR (docs/potentials.md). "
    "Strict: if this starts passing, upstream went float64; update the docs and remove the xfail.",
)
def test_finite_difference_bar_is_not_met_by_the_float32_backend():
    best = min(check_forces(OpenMMMLAni2x(), POSE, step_ang=h) for h in (1e-3, 1e-2, 3e-2))
    assert best < FORCE_FD_RELATIVE_BAR
