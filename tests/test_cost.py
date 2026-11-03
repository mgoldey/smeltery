"""Cost model: schema for every tier, honest None, DFT port, and prediction vs measurement."""

import re
from pathlib import Path

import numpy as np
import pytest

from smeltery import Candidate, Pose, cost
from smeltery.tiers import FieldDftInteraction, FieldInteraction, PairedPoses

KEYS = {"quantity", "unit", "predicted", "basis"}
ACID = "OC(=O)c1ccccc1"


def _posed(smiles=ACID, n=2):
    c = Candidate("c", smiles)
    PairedPoses(n_poses=n).run([c], {"parent": c})
    return c


def _tiers():
    return [PairedPoses(), FieldInteraction(), FieldDftInteraction()]


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


# ---------------------------------------------------------------- PBE+D3 tier


def test_pbe_cost_reproduces_its_own_reference():
    """The reference system must predict its own measured time, or the anchor is wrong."""
    secs = cost.pbe_seconds_per_pose(cost.PBE_REFERENCE)
    assert secs == pytest.approx(cost.PBE_REFERENCE_VACUUM_SECONDS + cost.PBE_REFERENCE_FIELD_SECONDS)


def test_pbe_cost_grows_with_both_atoms_and_basis():
    """xc_fock_work is nbf^2 x npts, so BOTH axes must move the prediction."""
    ref = cost.PBE_REFERENCE
    more_atoms = cost.DftSize(n_atoms=ref.n_atoms * 2, n_basis_functions=ref.n_basis_functions)
    more_basis = cost.DftSize(n_atoms=ref.n_atoms, n_basis_functions=ref.n_basis_functions * 2)
    base = cost.pbe_seconds_per_pose(ref)
    assert cost.pbe_seconds_per_pose(more_atoms) == pytest.approx(2 * base)  # npts is linear in atoms
    assert cost.pbe_seconds_per_pose(more_basis) == pytest.approx(4 * base)  # nbf enters squared


def test_ri_tensor_is_packed_and_matches_ferrics_own_number():
    """ferric stores the symmetric `mn` triangle; the dense form overestimates by exactly 2x.

    Anchored on ferric's own spill warning for a 74-atom ligand at def2-SVP
    (nbf=757, naux=3805 from def2-universal-jkfit): it reported 8.73 GB.
    """
    packed = cost.ri_tensor_gb(757, 3805)
    dense = cost.ri_tensor_gb(757, 3805, packed=False)
    assert packed == pytest.approx(8.73, abs=0.01)
    # nbf^2 / (nbf(nbf+1)/2) = 2nbf/(nbf+1): 2x only in the limit, 1.9987 at nbf=757.
    assert dense / packed == pytest.approx(2 * 757 / 758, rel=1e-6)


def test_dft_memory_reports_both_terms_and_their_sum():
    mem = cost.dft_memory_gb(cost.PBE_REFERENCE, 3805)
    assert mem["total_gb"] == pytest.approx(mem["ao_cache_gb"] + mem["ri_tensor_gb"])
    assert mem["ao_cache_gb"] > 0 and mem["ri_tensor_gb"] > 0


def test_pbe_tier_reports_dispersion_separately_from_the_interaction_energy():
    """D3 is geometry-only, so it must be its own quantity and never folded into dE_int."""
    tier = FieldDftInteraction()
    assert set(tier.produces()) == {"dE_int", "E_d3"}
    assert "cancels in dE_int" in tier.settings()["dispersion"]


def test_pbe_tier_floor_is_none_not_an_invented_number():
    """STO-3G error cancelling in a paired ddE is a design expectation, not a measurement."""
    assert FieldDftInteraction().systematic_floor("dE_int") is None
    with pytest.raises(KeyError):
        FieldDftInteraction().systematic_floor("not_a_quantity")


def test_pbe_estimate_cost_declines_outside_what_was_measured():
    """A different functional or basis gets None and a reason -- never a silent extrapolation."""
    posed = _posed()
    for tier, why in (
        (FieldDftInteraction(functional="B3LYP"), "PBE"),
        (FieldDftInteraction(basis="def2-svp"), "sto-3g"),
    ):
        est = tier.estimate_cost([posed])
        assert est["predicted"] is None
        assert est["basis"].startswith("unmeasured") and why in est["basis"]


def test_pbe_estimate_memory_says_cannot_say_rather_than_raising():
    """`run`'s pre-check calls this; an exception there would block a legitimate def2-SVP run."""
    mem = FieldDftInteraction(basis="def2-svp").estimate_memory([_posed()])
    assert mem["predicted"] is None
    assert "fits_budget" not in mem  # no verdict, so `run` must not refuse
    assert mem["basis"].startswith("unmeasured")


def test_pbe_tier_refuses_a_run_that_would_spill_to_disk():
    """Over budget is a cliff (the RI tensor is re-read every iteration), so refuse before the SCF."""
    from smeltery.tiers import DftMemoryError

    tier = FieldDftInteraction(memory_budget_gb=0.001)
    with pytest.raises(DftMemoryError, match="spill"):
        tier.run([_posed(n=1)], {"field": []})


def test_pbe_tier_can_be_told_to_run_anyway():
    """check_memory=False must skip the gate, not merely lower it."""
    tier = FieldDftInteraction(memory_budget_gb=0.001, check_memory=False)
    assert tier.estimate_memory([_posed(n=1)])["fits_budget"] is False
    assert tier.settings()["memory_budget_gb"] is None


def test_dft_energy_reads_total_energy_not_the_rhf_attribute():
    """`DftResult` has `total_energy`; `RhfResult` has `energy`. Using the wrong one is an AttributeError."""
    from smeltery.tiers import _dft_energy

    class Result:
        total_energy = -123.5
        e_dispersion = None

    assert _dft_energy(Result()) == -123.5


def test_dft_energy_refuses_to_double_count_dispersion():
    """If a future ferric applies D3 inside the SCF, the tier's separate term would double-count."""
    from smeltery.tiers import _dft_energy

    class Result:
        total_energy = -123.5
        e_dispersion = -0.01
        dispersion_model = "d3bj"

    with pytest.raises(RuntimeError, match="double-count"):
        _dft_energy(Result())


@pytest.mark.needs_ferric
def test_pbe_tier_runs_both_legs_and_reports_dispersion_separately():
    """Full path on a real (tiny) system: two SCFs, the D3 call, and `_dft_energy`.

    Water in a symmetric two-charge field, 0.34 s. The 74-atom ligand from the
    gen-compare set gives dE_int -19.436 / E_d3 -68.715 kcal/mol, reproducing a
    standalone measurement of -19.44 / -68.71 -- but it needs ~190 s of SCF, so
    the regression test uses a system small enough to run every time.
    """
    from smeltery import PointCharge
    from smeltery.tiers import FieldDftInteraction

    pose = Pose(
        symbols=("O", "H", "H"),
        coords_ang=np.array([[0.0, 0.0, 0.0], [0.9572, 0.0, 0.0], [-0.2400, 0.9266, 0.0]]),
    )
    cand = Candidate("water", "O", poses=[pose])
    field = [PointCharge(-0.5, (4.0, 0.0, 0.0)), PointCharge(0.5, (-4.0, 0.0, 0.0))]
    FieldDftInteraction().run([cand], {"field": field})

    assert cand.per_pose["dE_int"][0] == pytest.approx(-5.1517, abs=1e-3)
    assert cand.per_pose["E_d3"][0] == pytest.approx(-0.2256, abs=1e-3)
    # D3 is geometry-only: it must NOT have been folded into the interaction energy.
    assert cand.per_pose["dE_int"][0] != pytest.approx(cand.per_pose["E_d3"][0])


@pytest.mark.needs_ferric
def test_pbe_tier_dispersion_is_identical_in_both_legs_so_it_cancels():
    """The claim that D3 cancels in dE_int, checked against ferric rather than asserted.

    `d3bj_energy` takes only (mol, functional) -- no charges, no density -- so
    the same geometry must give the same correction whether or not a field is present.
    """
    import ferric

    mol = ferric.Molecule.from_xyz_string("3\nw\nO 0.0 0.0 0.0\nH 0.9572 0.0 0.0\nH -0.24 0.9266 0.0\n", 0, 1)
    assert ferric.d3bj_energy(mol, functional="PBE") == ferric.d3bj_energy(mol, functional="PBE")
    # And a plain PBE run carries no dispersion of its own, so adding D3 cannot double-count.
    result = ferric.run_dft(mol, ferric.BasisSet.bundled("sto-3g"), functional="PBE")
    assert result.e_dispersion is None
    assert result.total_energy == result.e_scf


def test_available_memory_is_read_from_meminfo_and_is_plausible():
    """MemAvailable, not MemFree: the kernel's estimate of what a new allocation gets without swapping."""
    from smeltery.cost import available_memory_gb

    got = available_memory_gb()
    if got is None:  # a platform without /proc: "cannot tell" is a legal answer, never an exception
        return
    assert 0.0 < got < 1024.0


def test_available_memory_returns_none_rather_than_raising_without_proc(monkeypatch):
    """A caller must be able to tell 'no headroom' from 'cannot tell', as estimate_memory already does."""
    import builtins

    from smeltery.cost import available_memory_gb

    real_open = builtins.open

    def no_proc(path, *a, **k):
        if str(path) == "/proc/meminfo":
            raise OSError("no /proc here")
        return real_open(path, *a, **k)

    monkeypatch.setattr(builtins, "open", no_proc)
    assert available_memory_gb() is None


def test_pbe_tier_refuses_to_start_when_the_machine_has_no_headroom():
    """The fixed memory_budget_gb check cannot see a loaded box; require_free_gb is what catches it.

    Regression: a run whose per-calculation prediction was ~2 GB held ~11 GB resident and thrashed on a
    machine with 4 GB available, taking 67 min on a molecule whose peers took 140 s.
    """
    import numpy as np

    from smeltery.model import Candidate, PointCharge, Pose
    from smeltery.tiers import DftMemoryError, FieldDftInteraction

    pose = Pose(("O", "H", "H"), np.array([[0.0, 0.0, 0.0], [0.9572, 0.0, 0.0], [-0.24, 0.9266, 0.0]]))
    cand = Candidate("water", "O", [pose])
    tier = FieldDftInteraction(require_free_gb=1e6)  # no machine has a petabyte free
    with pytest.raises(DftMemoryError, match="below the"):
        tier.run([cand], {"field": [PointCharge(-0.5, (4.0, 0.0, 0.0))]})


def test_pbe_tier_headroom_check_is_off_by_default():
    """Opt-in: existing callers must not start failing on a busy machine."""
    from smeltery.tiers import FieldDftInteraction

    assert FieldDftInteraction().require_free_gb is None
    assert FieldDftInteraction().settings()["require_free_gb"] is None
