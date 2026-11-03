"""Pocket loader anchors: exact counts and charges, empty field, Coulomb's law in Bohr, no default cutoff."""

import hashlib
import inspect
import shutil
from pathlib import Path

import ferric
import pytest

from smeltery import ANGSTROM_TO_BOHR, Candidate, PointCharge
from smeltery.pocket import Pdb2PqrUnavailableError, PocketField, load_pocket, parse_pqr, pdb2pqr_version
from smeltery.tiers import FieldInteraction, PairedPoses

DATA = Path(__file__).parent / "data"
PQR, PDB = DATA / "gly3.pqr", DATA / "gly3.pdb"
# Independent of the parser: from `grep -c ^ATOM` and an awk sum of the charge column.
PQR_N_CHARGES = 24
PQR_NET_CHARGE = 0.0
PQR_FIRST = PointCharge(0.2943, (-0.966, 0.493, 1.500))  # N of residue 1, as written in the file
ACID = "OC(=O)c1ccccc1"


def _one_pose(smiles="O", n=1):
    c = Candidate("c", smiles)
    PairedPoses(n_poses=n).run([c], {"parent": c})
    return c


def test_pqr_fixture_loads_exact_count_and_net_charge():
    field = load_pocket(PQR)
    assert len(field) == PQR_N_CHARGES
    assert abs(sum(c.q for c in field) - PQR_NET_CHARGE) < 1e-9
    assert field[0] == PQR_FIRST  # not just the sum: the charge and its Angstrom position
    assert field.provenance["n_charges"] == PQR_N_CHARGES


def test_pqr_records_with_and_without_chain_id_read_identically():
    line = "ATOM      1  N   GLY     1      -0.966   0.493   1.500  0.2943 1.8240\n"
    assert parse_pqr(line) == parse_pqr(line.replace("GLY     1", "GLY A   1")) == [PQR_FIRST]


# A pdb2pqr30 crystal water: HETATM, not ATOM. `parse_pqr` counts both, which is why these are in a
# field at all; a parser reading only ATOM lines drops them and the effect disappears unnoticed.
WATER_LINES = (
    "HETATM 6408  O   HOH   601     116.900 155.671 124.183 -0.8340 1.6612\n"
    "HETATM 6409  H1  HOH   601     117.100 155.800 125.100  0.4170 0.2245\n"
    "HETATM 6410  H2  HOH   601     116.000 155.400 124.000  0.4170 0.2245\n"
)
PROTEIN_LINE = "ATOM      1  N   GLY     1      -0.966   0.493   1.500  0.2943 1.8240\n"


def test_waters_are_parsed_by_default_and_are_hetatm_not_atom():
    """The default must not change: a caller that asks for nothing still gets every charge."""
    assert len(parse_pqr(PROTEIN_LINE + WATER_LINES)) == 4
    assert parse_pqr(PROTEIN_LINE + WATER_LINES)[0] == PQR_FIRST


def test_drop_waters_removes_only_the_water_records():
    kept = parse_pqr(PROTEIN_LINE + WATER_LINES, drop_waters=True)
    assert kept == [PQR_FIRST]  # the protein atom survives, all three water atoms go


@pytest.mark.parametrize("resname", ["HOH", "WAT", "TIP3", "SOL", "DOD"])
def test_drop_waters_covers_each_spelling_of_water(resname):
    line = WATER_LINES.splitlines()[0].replace("HOH", f"{resname:<3s}") + "\n"
    assert len(parse_pqr(line)) == 1
    assert parse_pqr(line, drop_waters=True) == []


def test_drop_waters_still_matches_when_a_writer_puts_the_chain_id_in_column_21():
    """Reading columns 18-21 to catch `TIP3` must not be defeated by a chain ID written at 21."""
    line = "HETATM 6408  O   HOHA 601     116.900 155.671 124.183 -0.8340 1.6612\n"
    assert parse_pqr(line, drop_waters=True) == []


def test_drop_waters_does_not_match_a_residue_merely_containing_a_water_name():
    """`HOH` as a substring of another residue name must not be dropped: matching is on the field."""
    line = "ATOM      1  N   PHO     1      -0.966   0.493   1.500  0.2943 1.8240\n"
    assert len(parse_pqr(line, drop_waters=True)) == 1


def test_load_pocket_defaults_to_keeping_waters():
    assert inspect.signature(load_pocket).parameters["drop_waters"].default is False


def test_load_pocket_records_the_water_decision_in_provenance(tmp_path):
    pqr = tmp_path / "w.pqr"
    pqr.write_text(PROTEIN_LINE + WATER_LINES)
    keep = load_pocket(pqr)
    assert keep.provenance["drop_waters"] is False
    assert "n_waters_dropped" not in keep.provenance  # nothing was dropped, so nothing is claimed
    dropped = load_pocket(pqr, drop_waters=True)
    assert len(dropped) == 1
    assert dropped.provenance["drop_waters"] is True
    assert dropped.provenance["n_waters_dropped"] == 3
    # n_total stays the honest count of what the FILE holds, before any filtering
    assert dropped.provenance["n_total"] == 4


@pytest.mark.needs_ferric
def test_empty_field_gives_exactly_zero_through_field_interaction():
    c = _one_pose(ACID, 2)
    FieldInteraction().run([c], {"field": PocketField([], {"input": "none"})})
    assert c.per_pose["dE_int"] == [0.0, 0.0]


def _shift_in_field(charge):
    """E(Li+ in a point charge) - E(Li+ alone), Hartree. `charge` is a ferric (q, x, y, z) tuple."""
    li = ferric.Molecule.from_xyz_string("1\n\nLi 0.0 0.0 0.0\n", charge=1, multiplicity=1)
    bs = ferric.BasisSet.bundled("sto-3g")
    kw = {"energy_conv": 1e-10, "density_conv": 1e-8}
    vac = ferric.run_rhf(li, bs, **kw)
    fld = ferric.run_rhf(li, bs, point_charges=[charge], **kw)
    assert vac.converged and fld.converged
    return fld.energy - vac.energy


def _assert_coulomb_in_bohr(shift, r_bohr, rel=0.02):
    assert shift == pytest.approx(1.0 / r_bohr, rel=rel), f"shift {shift} vs 1/R = {1.0 / r_bohr}"


@pytest.mark.needs_ferric
def test_unit_charge_at_20_bohr_shifts_a_cation_by_one_over_r_in_bohr():
    r_bohr = 20.0
    pc = PointCharge(1.0, (r_bohr / ANGSTROM_TO_BOHR, 0.0, 0.0))  # Angstrom in, as the loader returns
    shift = _shift_in_field(pc.as_ferric_bohr())
    _assert_coulomb_in_bohr(shift, r_bohr)
    # the same assertion rejects the Angstrom number used as if it were Bohr (R = 10.58 Bohr, 1.89x too strong)
    wrong = _shift_in_field((pc.q, *pc.xyz_ang))
    with pytest.raises(AssertionError):
        _assert_coulomb_in_bohr(wrong, r_bohr)
    assert wrong / shift == pytest.approx(ANGSTROM_TO_BOHR, rel=0.03)


def test_default_cutoff_is_none_and_a_distant_charge_survives(tmp_path):
    assert inspect.signature(load_pocket).parameters["cutoff_ang"].default is None
    far = tmp_path / "far.pqr"
    far.write_text(
        PQR.read_text().replace("TER", "ATOM     99  X   ION     9    1000.000   0.000   0.000  1.0000 1.0000\nTER")
    )
    field = load_pocket(far)
    assert field.provenance["cutoff_ang"] is None
    assert PointCharge(1.0, (1000.0, 0.0, 0.0)) in field
    assert len(field) == PQR_N_CHARGES + 1


@pytest.mark.needs_ferric
def test_cutoff_is_explicit_and_recorded_in_settings():
    with pytest.raises(ValueError, match="center_ang"):
        load_pocket(PQR, cutoff_ang=5.0)
    field = load_pocket(PQR, cutoff_ang=3.0, center_ang=(0.0, 0.0, 0.0))
    assert 0 < len(field) < PQR_N_CHARGES
    assert field.provenance["n_total"] == PQR_N_CHARGES
    tier, c = FieldInteraction(), _one_pose()
    tier.run([c], {"field": field})
    rec = tier.settings()["field"]
    assert rec["cutoff_ang"] == 3.0 and rec["center_ang"] == (0.0, 0.0, 0.0) and rec["n_charges"] == len(field)
    tier.run([c], {"field": load_pocket(PQR)})
    assert tier.settings()["field"]["cutoff_ang"] is None


@pytest.mark.needs_ferric
def test_settings_record_input_digest():
    tier, c = FieldInteraction(), _one_pose()
    tier.run([c], {"field": load_pocket(PQR)})
    assert tier.settings()["field"]["input_sha256"] == hashlib.sha256(PQR.read_bytes()).hexdigest()


def test_pdb_goes_through_pdb2pqr30_and_records_its_version():
    if pdb2pqr_version() is None:
        with pytest.raises(Pdb2PqrUnavailableError, match="pip install pdb2pqr"):
            load_pocket(PDB)
        pytest.skip("pdb2pqr30 not installed (pip install pdb2pqr); PDB path not exercised")
    field = load_pocket(PDB)
    assert len(field) == PQR_N_CHARGES  # same peptide, same AMBER assignment as the committed PQR
    assert abs(sum(c.q for c in field) - PQR_NET_CHARGE) < 1e-3
    assert field.provenance["pdb2pqr_version"] == pdb2pqr_version()
    assert field.provenance["pdb2pqr_version"]
    assert field.provenance["input_sha256"] == hashlib.sha256(PDB.read_bytes()).hexdigest()


def test_broken_pdb2pqr30_shim_is_not_installed(tmp_path, monkeypatch):
    """A pyenv-style shim that exits 127 means 'not installed', not CalledProcessError."""
    shim = tmp_path / "pdb2pqr30"
    shim.write_text("#!/bin/sh\nexit 127\n")
    shim.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:/usr/bin:/bin")
    assert shutil.which("pdb2pqr30") == str(shim)
    assert pdb2pqr_version() is None
    with pytest.raises(Pdb2PqrUnavailableError, match=r"pip install pdb2pqr.*|--version fails") as e:
        load_pocket(PDB)
    assert "pip install pdb2pqr" in str(e.value) and str(shim) in str(e.value)


# ---------------------------------------------------------------- the reference-pose control


def test_min_distance_to_field_is_the_closest_approach():
    from smeltery.pocket import min_distance_to_field_ang

    field = [PointCharge(1.0, (0.0, 0.0, 0.0)), PointCharge(1.0, (10.0, 0.0, 0.0))]
    assert min_distance_to_field_ang([(3.0, 0.0, 0.0)], field) == pytest.approx(3.0)
    assert min_distance_to_field_ang([(3.0, 0.0, 0.0), (9.5, 0.0, 0.0)], field) == pytest.approx(0.5)


def test_min_distance_needs_both_atoms_and_charges():
    from smeltery.pocket import min_distance_to_field_ang

    with pytest.raises(ValueError, match="both atoms and charges"):
        min_distance_to_field_ang([], [PointCharge(1.0, (0.0, 0.0, 0.0))])
    with pytest.raises(ValueError, match="both atoms and charges"):
        min_distance_to_field_ang([(0.0, 0.0, 0.0)], [])


def test_reference_pose_control_passes_a_pose_in_ordinary_contact():
    from smeltery.pocket import check_reference_pose

    field = [PointCharge(-0.834, (0.0, 0.0, 0.0))]
    assert check_reference_pose([(2.1, 0.0, 0.0)], field) == pytest.approx(2.1)


def test_reference_pose_control_blames_the_field_not_the_pose():
    """The message matters: this structure is experimental, so a failure means the FIELD is wrong."""
    from smeltery.pocket import check_reference_pose

    field = [PointCharge(-0.834, (0.0, 0.0, 0.0))]
    with pytest.raises(ValueError, match="the FIELD is wrong, not the pose"):
        check_reference_pose([(0.4, 0.0, 0.0)], field, name="crystal parent")


def test_reference_pose_control_catches_crystal_waters_left_in_a_real_field():
    """Regression for the 2026-10-27 defect: waters in the field put the CRYSTAL parent at 1.905 A.

    The control is the cheap check that would have caught it before any SCF was spent. Both figures
    are from this pocket: 1.905 A with waters, 2.126 A without.
    """
    pocket = Path("/home/matt/qc/external/gen-compare/fixtures/7LCJ_pocket.pdb")
    parent = Path("/home/matt/qc/smeltery/tests/data/sbdd/parent.sdf")
    if not (pocket.exists() and parent.exists()) or shutil.which("pdb2pqr30") is None:
        pytest.skip("needs the study-local 7LCJ pocket fixture and pdb2pqr30")
    from rdkit import Chem

    from smeltery.pocket import check_reference_pose, min_distance_to_field_ang

    mol = next(Chem.SDMolSupplier(str(parent)))
    # The SDF holds 41 HEAVY atoms only. The pipeline scores a pose with hydrogens added (71 atoms), and
    # the 1.905 A / 2.126 A figures quoted for this pocket are hydrogen contacts: on heavy atoms alone the
    # parent is 2.457 A away whether or not waters are present, because the waters it brushes are reached
    # by its hydrogens. Hydrogens are therefore what this control must be given.
    heavy = mol.GetConformer().GetPositions()
    with_h = Chem.AddHs(mol, addCoords=True)
    coords = with_h.GetConformer().GetPositions()
    assert len(heavy) == 41 and len(coords) > len(heavy)
    centre = tuple(float(v) for v in heavy.mean(axis=0))

    with_waters = load_pocket(pocket, cutoff_ang=12.0, center_ang=centre)
    without = load_pocket(pocket, cutoff_ang=12.0, center_ang=centre, drop_waters=True)
    # Heavy atoms cannot see the defect: identical to 3 decimals either way.
    assert min_distance_to_field_ang(heavy, with_waters) == pytest.approx(
        min_distance_to_field_ang(heavy, without), abs=0.001
    )
    # With hydrogens, dropping the waters moves the parent further from the field -- the signal.
    near = min_distance_to_field_ang(coords, with_waters)
    far = min_distance_to_field_ang(coords, without)
    assert far > near, f"dropping waters should move the parent away from the field, got {near} -> {far}"
    # At the 1.0 A default both pass: the parent is in CONTACT with water, not inside it. The control
    # earns its keep at a threshold set from the experimental structure itself.
    check_reference_pose(coords, with_waters, name="crystal parent")
    with pytest.raises(ValueError, match="the FIELD is wrong"):
        check_reference_pose(coords, with_waters, min_ang=far, name="crystal parent")
    check_reference_pose(coords, without, min_ang=near, name="crystal parent")
