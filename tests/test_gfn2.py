"""GFN2 tier: Bohr point charges, empty-field anchor, floor, and an actionable skip without xtb."""

import shutil

import numpy as np
import pytest

from smeltery import ANGSTROM_TO_BOHR, Candidate, PointCharge, Pose
from smeltery import tiers
from smeltery.tiers import XTB_VS_DFT_MAE_KCAL, Gfn2, XtbUnavailableError, run_checked, xtb_singlepoint

needs_xtb = pytest.mark.skipif(
    shutil.which("xtb") is None,
    reason="xtb binary not on PATH: install xtb 6.7.x (pinned external binary, not a pip dependency)",
)

_H = 0.5951
NH4 = (["N", "H", "H", "H", "H"],
       np.array([[0, 0, 0], [_H, _H, _H], [-_H, -_H, _H], [-_H, _H, -_H], [_H, -_H, -_H]], dtype=float))


def _nh4_candidate() -> Candidate:
    c = Candidate("nh4", "[NH4+]")
    c.poses = [Pose(tuple(NH4[0]), NH4[1])]
    return c


@needs_xtb
def test_point_charge_is_read_in_bohr_coulomb_law():
    r_bohr = 20.0
    vac = xtb_singlepoint(*NH4, charge=1)
    shift = xtb_singlepoint(*NH4, charge=1, point_charges_bohr=[(1.0, r_bohr, 0.0, 0.0)]) - vac
    assert shift == pytest.approx(0.04957, abs=2e-4)  # 0.05000 for 1/R in Bohr
    # the same charge with its position left in Angstrom (20 Bohr = 10.58 A, read as Bohr) is
    # a different physical field, ~0.0918 Ha: the Bohr assertion above would fail on it
    wrong = xtb_singlepoint(*NH4, charge=1, point_charges_bohr=[(1.0, r_bohr / ANGSTROM_TO_BOHR, 0.0, 0.0)]) - vac
    assert wrong != pytest.approx(0.04957, abs=2e-3)
    # and a position converted Bohr->Angstrom twice over (20 A as Bohr is 37.8 Bohr) gives 0.02646
    other = xtb_singlepoint(*NH4, charge=1, point_charges_bohr=[(1.0, r_bohr * ANGSTROM_TO_BOHR, 0.0, 0.0)]) - vac
    assert other == pytest.approx(0.02646, abs=2e-3)


@needs_xtb
def test_tier_converts_angstrom_field_to_bohr():
    c = _nh4_candidate()
    Gfn2().run([c], {"field": []})
    vac = c.per_pose["E_gfn2"][0]
    Gfn2().run([c], {"field": [PointCharge(1.0, (20.0 / ANGSTROM_TO_BOHR, 0.0, 0.0))]})
    shift_ha = (c.per_pose["E_gfn2"][0] - vac) / tiers.HARTREE_TO_KCAL
    assert shift_ha == pytest.approx(0.04957, abs=2e-4)


@needs_xtb
def test_tier_fails_coulomb_check_if_charges_stay_in_angstrom(monkeypatch):
    """Mutation check: a tier that skipped the Bohr conversion must miss 0.04957."""
    monkeypatch.setattr(PointCharge, "as_ferric_bohr", lambda self: (self.q, *self.xyz_ang))
    c = _nh4_candidate()
    Gfn2().run([c], {"field": []})
    vac = c.per_pose["E_gfn2"][0]
    Gfn2().run([c], {"field": [PointCharge(1.0, (20.0 / ANGSTROM_TO_BOHR, 0.0, 0.0))]})
    shift_ha = (c.per_pose["E_gfn2"][0] - vac) / tiers.HARTREE_TO_KCAL
    assert shift_ha != pytest.approx(0.04957, abs=2e-3)


@needs_xtb
def test_empty_field_reproduces_vacuum_bit_identically():
    vac = xtb_singlepoint(*NH4, charge=1)
    assert xtb_singlepoint(*NH4, charge=1, point_charges_bohr=[]) == vac
    zero = [(0.0, 5.0, 0.0, 0.0), (0.0, 0.0, 6.0, 0.0)]
    assert xtb_singlepoint(*NH4, charge=1, point_charges_bohr=zero) == vac


@needs_xtb
def test_tier_runs_checked_and_records_binary():
    c = _nh4_candidate()
    g = Gfn2()
    run_checked(g, [c], {"field": []})
    assert len(c.per_pose["E_gfn2"]) == 1
    s = g.settings()
    assert s["xtb_path"] == shutil.which("xtb") and s["xtb_version"]


def test_systematic_floor_is_measured_and_not_zero():
    floor = Gfn2().systematic_floor("E_gfn2")
    assert floor != 0.0
    assert floor >= XTB_VS_DFT_MAE_KCAL == 0.825
    with pytest.raises(KeyError):
        Gfn2().systematic_floor("dE_int")


def test_produces_states_gate_not_ranker():
    assert Gfn2().produces() == {"E_gfn2": "kcal/mol"}
    assert "GATE" in Gfn2.__doc__ and "RANKER" in Gfn2.__doc__
    assert Gfn2().settings()["role"] == "gate, not ranker"


def test_absent_binary_gives_actionable_error(monkeypatch):
    monkeypatch.setattr(tiers.shutil, "which", lambda name: None)
    with pytest.raises(XtbUnavailableError, match="install xtb"):
        Gfn2().run([_nh4_candidate()], {"field": []})
    assert Gfn2().settings()["xtb_path"] is None


def test_xtb_absent_is_recorded_as_a_skip_in_ci():
    if shutil.which("xtb") is None:
        pytest.skip("xtb binary not on PATH: install xtb 6.7.x and put it on PATH; xtb tests did not run")
