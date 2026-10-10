"""SurfaceEsp: ESP on a Lebedev vdW surface (issue #20).

The Rust reference numbers below are the output of ferric's own
`cargo test -p ferric-scf --test esp_surface -- --nocapture`
(`surface_counts_buried_points_for_polyatomic`: water, cc-pVDZ, RHF,
vdw_scale=1.4, 110 Lebedev points), as recorded in ferric's
`crates/ferric-python/tests/test_esp_on_surface.py` (mgoldey/ferric#359).
"""

import numpy as np
import pytest

from smeltery import ANGSTROM_TO_BOHR, Candidate, PoseGateError
from smeltery.model import Pose
from smeltery.tiers import (
    SurfaceEsp,
    SurfaceEspUnavailableError,
    UndeclaredQuantityError,
    run_checked,
    surface_esp,
)

WATER = Pose(("O", "H", "H"), np.array([[0.0, 0.0, 0.117], [0.0, 0.757, -0.469], [0.0, -0.757, -0.469]]))
NEON = Pose(("Ne",), np.array([[0.0, 0.0, 0.0]]))

RUST_N_POINTS = 154
RUST_N_BURIED = 176
RUST_SUM = 9.085327916437809e-1
RUST_SUM_ABS = 3.987656600183048e0
RUST_FIRST = -2.250396583511804e-2
RUST_LAST = 3.530571792071813e-2


@pytest.fixture
def binding():
    """Skip where the installed ferric predates the binding (the PyPI wheel CI installs, until a release has it)."""
    import ferric

    if not hasattr(ferric, "esp_on_surface"):
        pytest.skip("this ferric has no esp_on_surface (needs mgoldey/ferric#359, commit b22183b)")


def _scf(pose, basis):
    import ferric

    mol = ferric.Molecule.from_xyz_string(pose.to_xyz("t"))
    bs = ferric.BasisSet.bundled(basis)
    return ferric.run_rhf(mol, bs), bs


@pytest.mark.needs_ferric
@pytest.mark.usefixtures("binding")
def test_surface_esp_reproduces_the_rust_reference_to_1e_10():
    rhf, bs = _scf(WATER, "cc-pvdz")
    s = surface_esp(WATER, rhf, bs, vdw_scale=1.4, n_angular=110)
    assert s.points_ang.shape == (RUST_N_POINTS, 3)
    assert s.esp.shape == (RUST_N_POINTS,)
    assert s.n_buried == RUST_N_BURIED
    assert s.esp.sum() == pytest.approx(RUST_SUM, abs=1e-10)
    assert np.abs(s.esp).sum() == pytest.approx(RUST_SUM_ABS, abs=1e-10)
    assert s.esp[0] == pytest.approx(RUST_FIRST, abs=1e-10)
    assert s.esp[-1] == pytest.approx(RUST_LAST, abs=1e-10)


@pytest.mark.needs_ferric
@pytest.mark.usefixtures("binding")
def test_buried_points_are_dropped_for_a_polyatomic_and_none_for_an_isolated_atom():
    rhf, bs = _scf(WATER, "sto-3g")
    s = surface_esp(WATER, rhf, bs)
    assert s.n_buried > 0
    assert len(s.esp) + s.n_buried == 3 * 110  # every point is either kept or counted as buried

    rhf, bs = _scf(NEON, "sto-3g")
    s = surface_esp(NEON, rhf, bs)
    assert s.n_buried == 0
    assert s.points_ang.shape == (110, 3)


@pytest.mark.needs_ferric
@pytest.mark.usefixtures("binding")
def test_surface_points_are_returned_in_angstrom():
    import ferric

    rhf, bs = _scf(WATER, "sto-3g")
    mol = ferric.Molecule.from_xyz_string(WATER.to_xyz("t"))
    raw_points_bohr, raw_esp, _ = ferric.esp_on_surface(mol, bs, rhf)
    s = surface_esp(WATER, rhf, bs)
    assert np.allclose(s.points_ang * ANGSTROM_TO_BOHR, raw_points_bohr, atol=1e-12)
    assert np.array_equal(s.esp, np.asarray(raw_esp))


@pytest.mark.needs_ferric
@pytest.mark.usefixtures("binding")
def test_tier_writes_only_declared_scalars_and_keeps_the_arrays():
    tier = SurfaceEsp(basis="sto-3g")
    cand = Candidate("water", "O", [WATER, WATER])
    run_checked(tier, [cand], {})
    assert set(cand.per_pose) == set(tier.produces())
    assert all(len(v) == 2 for v in cand.per_pose.values())
    assert cand.per_pose["n_buried"][0] > 0
    s = tier.surfaces[("water", 0)]
    assert cand.per_pose["esp_surface_min"][0] == pytest.approx(float(s.esp.min()))
    assert cand.per_pose["esp_surface_max"][0] == pytest.approx(float(s.esp.max()))
    assert cand.per_pose["n_buried"][0] == s.n_buried


def test_tier_skips_with_an_actionable_message_on_a_ferric_without_the_binding(monkeypatch):
    import ferric

    monkeypatch.delattr(ferric, "esp_on_surface", raising=False)
    with pytest.raises(SurfaceEspUnavailableError, match="ferric#359"):
        surface_esp(WATER, object(), object())


def test_a_candidate_without_a_pose_report_is_refused_when_the_ctx_demands_one():
    cand = Candidate("nr", "O", [WATER])
    with pytest.raises(PoseGateError, match="no pose report"):
        SurfaceEsp().run([cand], {"require_pose_report": True})


def test_declares_units_and_has_no_measured_floor():
    tier = SurfaceEsp()
    assert tier.produces() == {"esp_surface_min": "Eh/e", "esp_surface_max": "Eh/e", "n_buried": "count"}
    assert all(tier.systematic_floor(q) is None for q in tier.produces())
    with pytest.raises(KeyError, match="does not produce"):
        tier.systematic_floor("dE_int")
    est = tier.estimate_cost([Candidate("w", "O", [WATER])])
    assert est["predicted"] is None and est["basis"].startswith("unmeasured")


def test_undeclared_keys_are_still_caught_by_run_checked():
    class Sloppy(SurfaceEsp):
        def run(self, candidates, ctx):
            candidates[0].per_pose["oops"] = [0.0]

    with pytest.raises(UndeclaredQuantityError):
        run_checked(Sloppy(), [Candidate("w", "O", [WATER])], {})
