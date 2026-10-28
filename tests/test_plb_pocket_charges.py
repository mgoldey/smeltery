"""scripts/plb_pocket_charges.py: the guard that stops it silently deleting charges, and an end-to-end run.

The fixture tests/data/plb/mini_cdk2.pdb holds 6 real residues of the PLB cdk2 protein (ACE cap, SER, MET, GLU, the
phosphothreonine TPO160 and one crystal water; CC BY 4.0, header inside). OpenMM is optional: the end-to-end tests
skip without it, the guard tests do not need it.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "data" / "plb" / "mini_cdk2.pdb"


@pytest.fixture(scope="module")
def tool():
    spec = importlib.util.spec_from_file_location("plb_pocket_charges", ROOT / "scripts" / "plb_pocket_charges.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plb_pocket_charges"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_guard_refuses_a_deleted_residue_inside_the_field_sphere(tool):
    deleted = {"TPO:A:160": np.array([[0.0, 0.0, 12.0], [0.0, 0.0, 20.0]])}
    with pytest.raises(tool.PrepError, match="TPO:A:160"):
        tool.check_deleted_outside_cutoff(deleted, (0, 0, 0), 15.0, 1.0)


def test_guard_margin_is_part_of_the_test(tool):
    deleted = {"X:A:1": np.array([[0.0, 0.0, 15.5]])}
    tool.check_deleted_outside_cutoff(deleted, (0, 0, 0), 15.0, 0.25)  # 15.5 > 15.25
    with pytest.raises(tool.PrepError):
        tool.check_deleted_outside_cutoff(deleted, (0, 0, 0), 15.0, 1.0)  # 15.5 <= 16.0


def test_guard_returns_each_residues_minimum_distance(tool):
    d = tool.check_deleted_outside_cutoff(
        {"A": np.array([[30.0, 0, 0], [40.0, 0, 0]]), "B": np.array([[0, 0, 25.0]])}, (0, 0, 0), 15.0, 1.0
    )
    assert d == {"A": pytest.approx(30.0), "B": pytest.approx(25.0)}


def test_pqr_line_round_trips_through_the_smeltery_loader(tool, tmp_path):
    from smeltery.pocket import parse_pqr

    line = tool.format_pqr_line(12, "CA", "SER", "A", "0", (1.5, -2.25, 30.125), -0.1234)
    (c,) = parse_pqr(line)
    assert c.q == pytest.approx(-0.1234) and c.xyz_ang == pytest.approx((1.5, -2.25, 30.125))


def test_the_fixture_is_the_documented_residues():
    text = FIXTURE.read_text()
    assert "CC BY 4.0" in text and "fd88824f9114244f95a14b485e6d6c96c1de716d" in text
    res = {
        (line[17:20].strip(), line[22:27].strip()) for line in text.splitlines() if line.startswith(("ATOM", "HETATM"))
    }
    assert res == {("ACE", "-1"), ("SER", "0"), ("MET", "1"), ("GLU", "2"), ("TPO", "160"), ("HOH", "2117")}


@pytest.mark.parametrize("ff", ["amber14", "charmm36"])
def test_end_to_end_records_every_decision(tool, tmp_path, ff):
    pytest.importorskip("openmm")
    out = tmp_path / f"{ff}.pqr"
    center = tuple(
        float(v) + o for v, o in zip((4.056, 54.613, 73.927), (80.0, 0.0, 0.0))
    )  # 80 A from TPO160's N: the TPO and the cap are far outside a 15 A sphere
    prov = tool.prepare(
        FIXTURE, out, ff=ff, center_ang=center, cutoff_ang=15.0, delete_residues=("ACE", "TPO"), drop_waters=True
    )
    assert set(prov["deleted_residues_min_distance_to_center_ang"]) == {"ACE:A:-1", "TPO:A:160"}
    assert all(d > 16.0 for d in prov["deleted_residues_min_distance_to_center_ang"].values())
    assert prov["waters_dropped"] is True and list(prov["waters_dropped_min_distance_to_center_ang"]) == ["HOH:A:2117"]
    assert prov["input_sha256"] == tool.sha256_file(FIXTURE) and prov["output_sha256"] == tool.sha256_file(out)
    assert json.loads(Path(str(out) + ".json").read_text()) == prov
    assert prov["n_atoms_written"] == sum(1 for line in out.read_text().splitlines() if line.startswith("ATOM"))
    assert "NO protonation is changed" in prov["protonation"] and prov["openmm_version"]
    # SER-MET-GLU with the cap and TPO deleted: GLU carries -1, the rest are neutral (the chain ends are not capped
    # any more, so the sum is a property of the force field's templates, here near -1 for both)
    assert prov["net_charge_written_e"] == pytest.approx(-1.0, abs=0.05)


def test_end_to_end_refuses_to_delete_the_tpo_when_it_is_inside_the_sphere(tool, tmp_path):
    pytest.importorskip("openmm")
    out = tmp_path / "x.pqr"
    with pytest.raises(tool.PrepError, match="TPO:A:160"):
        tool.prepare(
            FIXTURE,
            out,
            ff="amber14",
            center_ang=(4.056, 54.613, 73.927),
            cutoff_ang=15.0,
            delete_residues=("ACE", "TPO"),
            drop_waters=True,
        )
    assert not out.exists()  # nothing is written on a refusal


def test_an_untemplated_residue_that_is_not_declared_fails_loudly(tool, tmp_path):
    pytest.importorskip("openmm")
    with pytest.raises(tool.PrepError, match="no template"):
        tool.prepare(  # TPO is not deleted: neither force field has a template for it
            FIXTURE,
            tmp_path / "y.pqr",
            ff="amber14",
            center_ang=(84.0, 54.6, 73.9),
            cutoff_ang=15.0,
            delete_residues=("ACE",),
            drop_waters=True,
        )


def test_a_residue_named_for_deletion_that_is_not_in_the_file_is_an_error(tool, tmp_path):
    pytest.importorskip("openmm")
    with pytest.raises(tool.PrepError, match="not in the file"):
        tool.prepare(
            FIXTURE,
            tmp_path / "z.pqr",
            ff="amber14",
            center_ang=(84, 54.6, 73.9),
            cutoff_ang=15.0,
            delete_residues=("ACE", "TPO", "ZN"),
            drop_waters=True,
        )


def test_the_cli_exits_2_on_a_refusal(tool, tmp_path, capsys):
    pytest.importorskip("openmm")
    rc = tool.main(
        [
            str(FIXTURE),
            str(tmp_path / "o.pqr"),
            "--ff",
            "amber14",
            "--center",
            "4.056",
            "54.613",
            "73.927",
            "--cutoff",
            "15",
            "--delete-residues",
            "ACE,TPO",
            "--drop-waters",
        ]
    )
    assert rc == 2 and "REFUSED" in capsys.readouterr().err


# ---------------------------------------------------------------- the --lj and --prep extensions


def test_assign_cysteines_pairs_by_distance_and_never_says_cym(tool):
    """CYX for a disulfide, neutral CYS otherwise. CYM is a protonation claim and is never assigned."""

    class _A:
        def __init__(self, name, index):
            self.name, self.index = name, index

    class _R:
        def __init__(self, name, index, atoms):
            self.name, self.index, self._a = name, index, atoms

        def atoms(self):
            return iter(self._a)

    class _T:
        def __init__(self, rs):
            self._r = rs

        def residues(self):
            return iter(self._r)

    # SG of residues 0 and 1 are 2.03 A apart (a disulfide); residue 2's SG is far away (free)
    rs = [_R("CYS", 0, [_A("SG", 0)]), _R("CYS", 1, [_A("SG", 1)]), _R("CYS", 2, [_A("SG", 2)])]
    xyz = [(0.0, 0.0, 0.0), (0.0, 0.0, 2.03), (0.0, 0.0, 30.0)]
    templates, pairs, free = tool.assign_cysteines(_T(rs), xyz)
    assert pairs == [(0, 1, 2.03)]
    assert free == [2]
    assert sorted(templates.values()) == ["CYS", "CYX", "CYX"]
    assert "CYM" not in set(templates.values())


def test_assign_cysteines_threshold_is_exclusive_of_a_long_contact(tool):
    class _A:
        def __init__(self, name, index):
            self.name, self.index = name, index

    class _R:
        def __init__(self, name, index, atoms):
            self.name, self.index, self._a = name, index, atoms

        def atoms(self):
            return iter(self._a)

    class _T:
        def __init__(self, rs):
            self._r = rs

        def residues(self):
            return iter(self._r)

    rs = [_R("CYS", 0, [_A("SG", 0)]), _R("CYS", 1, [_A("SG", 1)])]
    _, pairs, free = tool.assign_cysteines(_T(rs), [(0.0, 0.0, 0.0), (0.0, 0.0, 3.0)], 2.5)
    assert pairs == [] and free == [0, 1]  # 3.0 A is not a disulfide


def test_prep_is_refused_for_charmm36(tool, tmp_path):
    """The CYX/CYS template names assigned by --prep are AMBER's; charmm36 must not get them silently."""
    with pytest.raises(tool.PrepError, match="amber14 only"):
        tool.prepare(
            FIXTURE,
            tmp_path / "o.pqr",
            ff="charmm36",
            center_ang=(84.0, 54.6, 73.9),
            cutoff_ang=15.0,
            delete_residues=(),
            drop_waters=True,
            prep=True,
        )


def test_lj_sidecar_carries_sigma_epsilon_per_atom_in_pqr_order(tool, tmp_path):
    pytest.importorskip("openmm")
    out, lj = tmp_path / "o.pqr", tmp_path / "o.lj.json"
    center = (84.056, 54.613, 73.927)
    prov = tool.prepare(
        FIXTURE,
        out,
        ff="amber14",
        center_ang=center,
        cutoff_ang=15.0,
        delete_residues=("ACE", "TPO"),
        drop_waters=True,
        lj_path=lj,
    )
    d = json.loads(lj.read_text())
    n_pqr = sum(1 for line in out.read_text().splitlines() if line.startswith("ATOM"))
    assert len(d["atoms"]) == n_pqr == prov["n_atoms_written"]  # one entry per PQR line, same order
    assert d["units"] == {"sigma": "angstrom", "epsilon": "kcal/mol", "charge": "e"}
    assert d["pqr_sha256"] == tool.sha256_file(out)
    # Every entry carries a real well depth or is a polar hydrogen; sigma is always positive.
    assert all(a["sigma_ang"] > 0 for a in d["atoms"])
    assert all(a["eps_kcal"] >= 0 for a in d["atoms"])
    assert d["summary"]["n_epsilon_zero_not_hydrogen"] == 0
    # The charge column must agree with the PQR it is a sidecar for.
    from smeltery.pocket import parse_pqr

    assert [a["q"] for a in d["atoms"]] == pytest.approx([c.q for c in parse_pqr(out.read_text())])


def test_without_lj_nothing_extra_is_written_or_claimed(tool, tmp_path):
    """The default path is unchanged: no sidecar, and no LJ keys in the provenance."""
    pytest.importorskip("openmm")
    out = tmp_path / "o.pqr"
    prov = tool.prepare(
        FIXTURE,
        out,
        ff="amber14",
        center_ang=(84.056, 54.613, 73.927),
        cutoff_ang=15.0,
        delete_residues=("ACE", "TPO"),
        drop_waters=True,
    )
    assert not list(tmp_path.glob("*.lj.json"))
    assert "lj_summary" not in prov and "lj_output" not in prov
    assert prov["prep"] is False
    assert "NO protonation is changed" in prov["protonation"]


def test_prep_repairs_a_stripped_pocket_that_cannot_otherwise_be_typed(tool, tmp_path):
    """The real reason --prep exists: a crystal pocket with no hydrogens fails to type without it.

    The fixture is stripped of hydrogens here to stand in for an unprepped crystal structure (the
    committed fixture is already protonated, so it would not exercise this path).
    """
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    stripped = tmp_path / "noh.pdb"
    kept = [
        line
        for line in FIXTURE.read_text().splitlines()
        # drop hydrogens by the element column, which is authoritative, not by the atom name
        if not (line.startswith(("ATOM", "HETATM")) and line[76:78].strip() == "H")
    ]
    stripped.write_text("\n".join(kept) + "\n")
    center = (84.056, 54.613, 73.927)
    common = dict(ff="amber14", center_ang=center, cutoff_ang=15.0, delete_residues=("ACE", "TPO"), drop_waters=True)

    # Without --prep the missing hydrogens make the residues untemplatable, and it must say so.
    with pytest.raises(tool.PrepError, match="no template"):
        tool.prepare(stripped, tmp_path / "a.pqr", **common)

    # With --prep it types, and the provenance states that hydrogens were ADDED rather than kept.
    out = tmp_path / "b.pqr"
    prov = tool.prepare(stripped, out, **common, prep=True)
    assert prov["n_atoms_written"] > sum(
        1 for line in stripped.read_text().splitlines() if line.startswith(("ATOM", "HETATM"))
    )
    assert "hydrogens ADDED" in prov["protonation"] and "pH 7.0" in prov["protonation"]
    assert prov["prep"]["loops_modelled"] is False  # never invent density
    assert prov["prep"]["pdbfixer_version"] != "unknown"
    assert prov["prep"]["hydrogens_added_at_ph"] == 7.0


def test_prep_refuses_a_nonstandard_residue_it_was_not_told_to_delete(tool, tmp_path):
    """pdbfixer's own remedy for TPO is to substitute THR, losing the phosphate. That must not happen."""
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    with pytest.raises(tool.PrepError, match="TPO->THR"):
        tool.fix_unprepped_pocket(FIXTURE, 7.0, declared_deletions=("ACE",))
    # Declaring it makes it the caller's explicit decision, guarded by distance in `prepare`.
    _, _, report = tool.fix_unprepped_pocket(FIXTURE, 7.0, declared_deletions=("ACE", "TPO"))
    assert report["loops_modelled"] is False


def test_lj_is_refused_for_charmm36_rather_than_writing_placeholder_zeros(tool, tmp_path):
    """charmm36 keeps its real LJ in a separate LennardJonesForce; the NonbondedForce holds placeholders.

    Reading per-atom sigma/epsilon there gives eps=0 for EVERY atom -- a field with no Pauli repulsion,
    which is the exact failure --lj exists to prevent. It must refuse, not write a plausible file of zeros.
    """
    pytest.importorskip("openmm")
    out, lj = tmp_path / "o.pqr", tmp_path / "o.lj.json"
    with pytest.raises(tool.PrepError, match="placeholder"):
        tool.prepare(
            FIXTURE,
            out,
            ff="charmm36",
            center_ang=(84.056, 54.613, 73.927),
            cutoff_ang=15.0,
            delete_residues=("ACE", "TPO"),
            drop_waters=True,
            lj_path=lj,
        )
    assert not out.exists() and not lj.exists()  # nothing is written on a refusal


def test_charmm36_without_lj_still_works(tool, tmp_path):
    """The refusal is specific to --lj: charmm36 charges are fine and must keep working."""
    pytest.importorskip("openmm")
    prov = tool.prepare(
        FIXTURE,
        tmp_path / "o.pqr",
        ff="charmm36",
        center_ang=(84.056, 54.613, 73.927),
        cutoff_ang=15.0,
        delete_residues=("ACE", "TPO"),
        drop_waters=True,
    )
    assert prov["force_field"] == "charmm36" and prov["n_atoms_written"] > 0


def test_histidine_census_reads_protonation_off_the_hydrogens(tool):
    """Template-independent: HD1 only is HID, HE2 only is HIE, both is HIP."""

    class _A:
        def __init__(self, name):
            self.name = name

    class _R:
        def __init__(self, name, atom_names):
            self.name, self._a = name, [_A(n) for n in atom_names]

        def atoms(self):
            return iter(self._a)

    class _T:
        def __init__(self, rs):
            self._r = rs

        def residues(self):
            return iter(self._r)

    topo = _T(
        [
            _R("HIS", ["CA", "ND1", "HD1", "NE2"]),  # delta tautomer
            _R("HIE", ["CA", "ND1", "NE2", "HE2"]),  # epsilon tautomer, already named
            _R("HSP", ["CA", "ND1", "HD1", "NE2", "HE2"]),  # CHARMM name, protonated cation
            _R("SER", ["CA", "OG", "HG"]),  # not a histidine: ignored
        ]
    )
    assert tool.histidine_census_from_hydrogens(topo) == {"HID": 1, "HIE": 1, "HIP": 1}


def test_histidine_census_is_reported_even_when_the_template_census_is_unavailable(tool, tmp_path):
    """A pocket with a disulfide makes getMatchingTemplates raise; the census must not silently become {}."""
    pytest.importorskip("openmm")
    pytest.importorskip("pdbfixer")
    pocket = Path("/home/matt/qc/external/gen-compare/fixtures/7LCJ_pocket.pdb")
    if not pocket.exists():
        pytest.skip("7LCJ pocket fixture is study-local, not in this repo")
    prov = tool.prepare(
        pocket,
        tmp_path / "h.pqr",
        ff="amber14",
        center_ang=(125.426, 157.012, 113.301),
        cutoff_ang=12.0,
        delete_residues=(),
        drop_waters=False,
        prep=True,
    )
    assert prov["prep"]["n_cys_disulfide_CYX"] == 8  # four disulfides: the ambiguity that breaks the census
    census = prov["histidine_templates_matched"]
    assert census and sum(census.values()) == 7  # not {}, which would read as "no histidines"
