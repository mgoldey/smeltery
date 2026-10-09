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
