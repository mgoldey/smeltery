"""Qmmm: QM/MM single points with a real covalent cut (issue #19; ferric #319 fixed by ferric PR #336).

Fixture: tests/data/gly3.pqr geometry with AMBER ff14SB parameters (tests/data/gly3_ff14sb.json,
extracted by tests/data/make_gly3_ff14sb.py with OpenMM; the numbers are OpenMM's, not typed here).
Atom order is the file's: N1 CA1 C1 O1 H1 HA21 HA31 H21 H31 N2 ...

The QM region used throughout is the N-terminal ammonium plus CH2 of residue 1 (charge +1), so the one
cut bond is the C-C bond CA1-C1: a real covalent cut through a peptide backbone.
"""

import json
import types
from pathlib import Path

import numpy as np
import pytest

from smeltery import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL, Candidate, PoseGateError
from smeltery.gates import PoseReport, PoseVerdict
from smeltery.model import Pose
from smeltery.tiers import (
    CC_LINK_SCALE,
    MIN_LINK_CHARGE_DISTANCE_ANG,
    MmParameters,
    Qmmm,
    QmmmBoundaryError,
    QmmmUnavailableError,
    UndeclaredQuantityError,
    cut_lj_exclusions_probe,
    run_checked,
)

DATA = Path(__file__).parent / "data"
PARAMS = MmParameters.from_json(DATA / "gly3_ff14sb.json")
ATOMS = json.loads((DATA / "gly3_ff14sb.json").read_text())["atoms"]
XYZ = np.array([a["xyz"] for a in ATOMS], dtype=float)
NAMES = [f"{a['name']}{a['resid']}" for a in ATOMS]


def idx(*names):
    return [NAMES.index(n) for n in names]


QM_A = tuple(idx("N1", "H1", "H21", "H31", "CA1", "HA21", "HA31"))  # cut CA1-C1 (C-C)
QM_B = QM_A + tuple(idx("C1", "O1"))  # cut C1-N2 (C-N)
POSE = Pose(PARAMS.symbols, XYZ.copy())


def cand(poses=None, name="gly3"):
    return Candidate(name, "NCC(=O)NCC(=O)NCC(=O)O", list(poses or [POSE]))


@pytest.fixture
def engine():
    """Skip, with the reason, where this ferric cannot do a covalent cut (the PyPI wheel older than rc7)."""
    import ferric

    from smeltery.tiers import _qmmm_api_missing

    missing = _qmmm_api_missing(ferric)
    if missing:
        pytest.skip(f"this ferric lacks {missing}; needs ferric >= v0.1.0rc7 (QM/MM link atoms, MmTopology)")
    ok, lj = cut_lj_exclusions_probe(ferric)
    if not ok:
        pytest.skip(
            f"this ferric has no QM-MM LJ exclusion across a bonded cut (probe {lj:.1f} kcal/mol): needs PR #336"
        )
    return ferric


# ---------------------------------------------------------------- pure Python: protocol and refusals


def test_declares_units_has_no_floor_and_unmeasured_cost():
    tier = Qmmm(PARAMS, QM_A, 1)
    assert tier.produces() == {"E_qmmm": "kcal/mol", "E_qm_embedded": "kcal/mol", "E_mm": "kcal/mol"}
    assert all(tier.systematic_floor(q) is None for q in tier.produces())
    with pytest.raises(KeyError, match="does not produce"):
        tier.systematic_floor("dE_int")
    est = tier.estimate_cost([cand()])
    assert est["predicted"] is None and "unmeasured" in est["basis"]


def test_settings_record_the_boundary_scheme_and_force_field_provenance():
    s = Qmmm(PARAMS, QM_A, 1).settings()
    assert s["boundary_scheme"] == "delete-host"  # Z1 by default (issue #19)
    assert s["cut_bonds"] == [(idx("CA1")[0], idx("C1")[0])]
    assert s["link_scale"] == pytest.approx(CC_LINK_SCALE)
    assert s["mm_force_field"]["forcefield"].startswith("amber14")
    assert len(s["mm_force_field"]["sha256"]) == 64
    assert s["min_link_charge_distance_ang"] == MIN_LINK_CHARGE_DISTANCE_ANG


def test_mm_parameters_refuse_missing_provenance_and_mismatched_lengths():
    with pytest.raises(ValueError, match="provenance"):
        MmParameters(("H",), (0.0,), (1.0,), (0.1,), ())
    with pytest.raises(ValueError, match="entries"):
        MmParameters(("H", "H"), (0.0,), (1.0, 1.0), (0.1, 0.1), (), provenance={"x": 1})


def test_bad_partitions_are_refused_at_construction():
    with pytest.raises(ValueError, match="qm_indices"):
        Qmmm(PARAMS, (), 0)
    with pytest.raises(ValueError, match="qm_indices"):
        Qmmm(PARAMS, (0, 0), 0)
    with pytest.raises(ValueError, match="qm_indices"):
        Qmmm(PARAMS, (999,), 0)
    with pytest.raises(ValueError, match="boundary_scheme"):
        Qmmm(PARAMS, QM_A, 1, boundary_scheme="z2")
    with pytest.raises(ValueError, match="link_scale"):
        Qmmm(PARAMS, QM_A, 1, link_scale=1.2)


def test_the_link_distance_threshold_is_inside_the_reported_bracket():
    # smeltery#19: 0.443 A (keep) diverged, 1.305 A (delete-host) converged. Those are the issue's numbers,
    # not reproduced here (see the note at MIN_LINK_CHARGE_DISTANCE_ANG).
    assert 0.443 < MIN_LINK_CHARGE_DISTANCE_ANG < 1.305


def test_a_non_carbon_carbon_cut_needs_an_explicit_link_scale():
    tier = Qmmm(PARAMS, QM_B, 1)  # cuts C1-N2
    with pytest.raises(QmmmBoundaryError, match="not C-C"):
        tier.resolved_link_scale()
    assert Qmmm(PARAMS, QM_B, 1, link_scale=0.8).resolved_link_scale() == 0.8


def test_an_engine_without_the_api_is_refused_with_a_message_naming_the_fix():
    tier = Qmmm(PARAMS, QM_A, 1)
    with pytest.raises(QmmmUnavailableError, match=r"run_qmmm.*v0\.1\.0rc7"):
        tier._require_engine(types.SimpleNamespace(QmmmSystem=object, MmTopology=object))


def test_an_engine_that_applies_lj_across_the_cut_is_refused_citing_319(monkeypatch):
    # Simulates a ferric before PR #336: the probe sees a thousands-of-kcal/mol LJ across a bonded pair.
    import smeltery.tiers as tiers

    ferric = types.SimpleNamespace(
        QmmmSystem=type(
            "QmmmSystem",
            (),
            {"with_link_atoms": 0, "with_boundary_charges": 0, "min_link_to_charge_distance": 0, "qm_molecule": 0},
        ),
        MmTopology=object,
        run_qmmm=object,
    )
    monkeypatch.setattr(tiers, "cut_lj_exclusions_probe", lambda f=None: (False, 6838.0))
    with pytest.raises(QmmmUnavailableError, match="ferric#319"):
        Qmmm(PARAMS, QM_A, 1)._require_engine(ferric)
    # No cut bond -> the cut-pair LJ question does not arise, so the same engine is accepted.
    Qmmm(PARAMS, tuple(range(len(PARAMS.symbols))), 0)._require_engine(ferric)


def test_a_failing_pose_report_is_refused_before_any_engine_work():
    c = cand()
    c.pose_report = PoseReport((PoseVerdict((), ("bond_lengths",)),), "mol", "0.0")
    with pytest.raises(PoseGateError, match="PoseBusters"):
        Qmmm(PARAMS, QM_A, 1).run([c], {})
    c2 = cand()
    with pytest.raises(PoseGateError, match="no pose report"):
        Qmmm(PARAMS, QM_A, 1).run([c2], {"require_pose_report": True})


# ---------------------------------------------------------------- with the engine


WATER = Pose(("O", "H", "H"), np.array([[0.0, 0.0, 0.117], [0.0, 0.757, -0.469], [0.0, -0.757, -0.469]]))
WATER_PARAMS = MmParameters(
    ("O", "H", "H"),
    (-0.8, 0.4, 0.4),
    (3.15, 1.0, 1.0),
    (0.15, 0.0, 0.0),
    ((0, 1, 500.0, 0.96), (0, 2, 500.0, 0.96)),
    provenance={"source": "test-only illustrative numbers, not a force field"},
)


@pytest.mark.needs_ferric
def test_all_qm_region_is_bit_identical_to_plain_rhf_and_has_no_mm_energy(engine):
    """Vacuum anchor (issue #19): no MM region and no cut -> exactly the gas-phase RHF energy."""
    tier = Qmmm(WATER_PARAMS, (0, 1, 2), 0)
    c = Candidate("water", "O", [WATER])
    run_checked(tier, [c], {})
    mol = engine.Molecule.from_xyz_string(WATER.to_xyz("water"))
    plain = engine.run_rhf(
        mol, engine.BasisSet.bundled("sto-3g"), energy_conv=tier.energy_conv, density_conv=tier.density_conv
    )
    assert c.per_pose["E_qm_embedded"][0] == plain.energy * HARTREE_TO_KCAL  # bit-identical, not approx
    assert c.per_pose["E_mm"][0] == 0.0
    assert c.per_pose["E_qmmm"][0] == c.per_pose["E_qm_embedded"][0]


@pytest.mark.needs_ferric
def test_the_probe_can_fail_an_engine_that_ignores_the_bond_list_is_detected(engine):
    """Sensitivity of the #319 probe: wrap ferric so MmTopology drops its bonds (so every QM-MM pair is
    'non-bonded', which is what ferric did before PR #336). The probe must then report thousands of kcal/mol."""

    class NoBonds:
        def __getattr__(self, name):
            return getattr(engine, name)

        class MmTopology:
            @staticmethod
            def from_amber_units(q, s, e, bonds, angles, torsions):
                return engine.MmTopology.from_amber_units(q, s, e, [], [], [])

    ok, lj = cut_lj_exclusions_probe(NoBonds())
    assert not ok and lj > 1000.0
    assert cut_lj_exclusions_probe(engine) == (True, 0.0)


@pytest.mark.needs_ferric
def test_cut_lj_follows_the_bond_list_which_is_what_makes_the_cut_safe(engine):
    """Measured: with the cut bond in the topology the LJ is ~5 kcal/mol; omit it and it is ~6838 (ferric #319)."""
    tier = Qmmm(PARAMS, QM_A, 1)
    c = cand()
    run_checked(tier, [c], {})
    lj_ok = tier.diagnostics[("gly3", 0)]["mm_components_kcal"]["lj"]
    assert abs(lj_ok) < 50.0

    cut = tuple(sorted(idx("CA1", "C1")))
    no_cut_bond = [b for b in PARAMS.bonds if tuple(sorted(b[:2])) != cut]
    assert len(no_cut_bond) == len(PARAMS.bonds) - 1
    sysm = engine.QmmmSystem(
        list(PARAMS.symbols), [tuple(r) for r in XYZ], list(PARAMS.charges), qm_indices=list(QM_A), charge=1
    ).with_link_atoms([(b[0], b[1]) for b in PARAMS.bonds], CC_LINK_SCALE)
    res = engine.run_qmmm(sysm, "sto-3g", mm_topology=PARAMS.topology(engine, bonds=no_cut_bond))
    lj_bad = res.mm_energy["lj"] * HARTREE_TO_KCAL
    assert lj_bad > 1000.0 > abs(lj_ok)


@pytest.mark.needs_ferric
def test_a_run_writes_only_declared_keys_and_never_touches_the_input_coordinates(engine):
    pose = Pose(PARAMS.symbols, XYZ.copy())
    before = pose.coords_ang.tobytes()
    c = cand([pose])
    tier = Qmmm(PARAMS, QM_A, 1)
    run_checked(tier, [c], {})  # raises UndeclaredQuantityError on a stray key
    assert pose.coords_ang.tobytes() == before
    assert set(c.per_pose) == set(tier.produces())
    assert c.per_pose["E_qmmm"][0] == pytest.approx(c.per_pose["E_qm_embedded"][0] + c.per_pose["E_mm"][0])
    assert np.isfinite(c.per_pose["E_qmmm"][0])
    # the force field really is applied: MM energy and its LJ part are non-zero (measured 5.38 and 5.13 kcal/mol)
    assert c.per_pose["E_mm"][0] == pytest.approx(5.38, abs=0.05)
    assert tier.diagnostics[("gly3", 0)]["mm_components_kcal"]["lj"] == pytest.approx(5.13, abs=0.05)
    assert undeclared_key_is_caught(tier)


def undeclared_key_is_caught(tier):
    class Sneaky(Qmmm):
        def run(self, candidates, ctx):
            super().run(candidates, ctx)
            candidates[0].per_pose["stray"] = [0.0]

    with pytest.raises(UndeclaredQuantityError):
        run_checked(Sneaky(PARAMS, QM_A, 1), [cand()], {})
    return True


@pytest.mark.needs_ferric
def test_keep_scheme_with_a_charge_0p43_A_from_the_link_h_is_refused_before_the_scf(engine, monkeypatch):
    calls = []
    real = engine.run_qmmm
    monkeypatch.setattr(engine, "run_qmmm", lambda *a, **k: calls.append(1) or real(*a, **k))
    with pytest.raises(QmmmBoundaryError, match=r"0\.43\d A from the nearest embedding charge"):
        Qmmm(PARAMS, QM_A, 1, boundary_scheme="keep").run([cand()], {})
    assert calls == []  # refused before any SCF
    # The same partition under Z1 passes the gate (its nearest charge is 1.47 A away) and runs.
    c = cand()
    Qmmm(PARAMS, QM_A, 1, boundary_scheme="delete-host").run([c], {})
    assert len(calls) >= 1 and np.isfinite(c.per_pose["E_qmmm"][0])


@pytest.mark.needs_ferric
def test_the_gate_threshold_is_the_knob_that_decides(engine):
    d = engine.QmmmSystem(
        list(PARAMS.symbols), [tuple(r) for r in XYZ], list(PARAMS.charges), qm_indices=list(QM_A), charge=1
    ).with_link_atoms([(b[0], b[1]) for b in PARAMS.bonds], CC_LINK_SCALE)
    d = d.with_boundary_charges([(b[0], b[1]) for b in PARAMS.bonds], "rcd").min_link_to_charge_distance()
    assert d == pytest.approx(0.902, abs=1e-3)  # measured on this fixture
    t = Qmmm(PARAMS, QM_A, 1, boundary_scheme="rcd", min_link_charge_distance_ang=d + 0.01)
    with pytest.raises(QmmmBoundaryError):
        t.run([cand()], {})
    t = Qmmm(PARAMS, QM_A, 1, boundary_scheme="rcd", min_link_charge_distance_ang=d - 0.01)
    c = cand()
    t.run([c], {})
    assert np.isfinite(c.per_pose["E_qmmm"][0])


@pytest.mark.needs_ferric
def test_odd_electron_count_is_refused_before_the_scf(engine):
    with pytest.raises(QmmmBoundaryError, match="even count"):
        Qmmm(PARAMS, QM_A, 0).run([cand()], {})  # charge 0 on the ammonium fragment -> 19 electrons


@pytest.mark.needs_ferric
def test_accessor_units_link_positions_are_angstrom_and_point_charges_are_bohr(engine):
    """ferric.pyi: link_atom_positions() is Angstrom, point_charges() is Bohr. Converting both (or neither)
    is the unit bug this guards; the tier's distance gate must use exactly one conversion."""
    bonds = [(b[0], b[1]) for b in PARAMS.bonds]
    sysm = (
        engine.QmmmSystem(
            list(PARAMS.symbols), [tuple(r) for r in XYZ], list(PARAMS.charges), qm_indices=list(QM_A), charge=1
        )
        .with_link_atoms(bonds, CC_LINK_SCALE)
        .with_boundary_charges(bonds, "delete-host")
    )
    ca, c1 = XYZ[idx("CA1")[0]], XYZ[idx("C1")[0]]
    (link,) = sysm.link_atom_positions()
    assert np.allclose(link, ca + CC_LINK_SCALE * (c1 - ca), atol=1e-9)  # Angstrom: no conversion applied

    pcs = np.array(sysm.point_charges())  # (q, x, y, z) in Bohr
    # the MM atom HA22 (index of residue 2) keeps its charge and sits where the input put it, in Bohr
    k = idx("HA22")[0]
    row = pcs[np.argmin(np.linalg.norm(pcs[:, 1:] - XYZ[k] * ANGSTROM_TO_BOHR, axis=1))]
    assert np.allclose(row[1:], XYZ[k] * ANGSTROM_TO_BOHR, atol=1e-9)
    assert row[0] == pytest.approx(PARAMS.charges[k])

    # One conversion, applied to the charges, reproduces ferric's gate distance; converting neither or both does not.
    correct = np.linalg.norm(pcs[:, 1:] / ANGSTROM_TO_BOHR - np.array(link), axis=1).min()
    assert correct == pytest.approx(sysm.min_link_to_charge_distance(), abs=1e-9)
    neither = np.linalg.norm(pcs[:, 1:] - np.array(link), axis=1).min()  # Bohr charges vs Angstrom link
    both = np.linalg.norm(pcs[:, 1:] / ANGSTROM_TO_BOHR - np.array(link) / ANGSTROM_TO_BOHR, axis=1).min()
    assert abs(neither - correct) > 0.05
    assert abs(both - correct) > 1e-2  # smaller shift (the link sits near the origin), still far above 1e-9


@pytest.mark.needs_ferric
def test_boundary_shift_is_a_measurement_not_a_claim(engine):
    """Relative energy of two conformers (rigid rotation of everything beyond CA1-C1 by 60 deg) for the cut at
    CA1-C1 vs the cut one bond further out at C1-N2 (explicit link scale 1.09/d(C1-N2)). Totals of different
    partitions are NOT comparable (QM atoms carry their electronic energy), so the comparable quantity is the
    conformer difference. Only sanity is asserted; the spread is reported in the PR as a measurement."""
    ca, c1, n2 = (XYZ[idx(n)[0]] for n in ("CA1", "C1", "N2"))
    k = (c1 - ca) / np.linalg.norm(c1 - ca)
    moving = idx("C1", "O1") + [i for i, n in enumerate(NAMES) if int(n[-1]) >= 2]
    th = np.radians(60.0)
    rot = XYZ.copy()
    for m in moving:
        v = XYZ[m] - ca
        rot[m] = ca + v * np.cos(th) + np.cross(k, v) * np.sin(th) + k * (k @ v) * (1 - np.cos(th))
    poses = [POSE, Pose(PARAMS.symbols, rot)]
    de = {}
    for label, qm, scale in (("A", QM_A, None), ("B", QM_B, 1.09 / np.linalg.norm(c1 - n2))):
        c = cand(poses)
        Qmmm(PARAMS, qm, 1, link_scale=scale).run([c], {})
        e = c.per_pose["E_qmmm"]
        de[label] = e[1] - e[0]
    assert all(np.isfinite(v) for v in de.values())
    assert abs(de["A"] - de["B"]) < 10.0  # loose sanity bar (measured ~0.65 kcal/mol); NOT a validated tolerance
