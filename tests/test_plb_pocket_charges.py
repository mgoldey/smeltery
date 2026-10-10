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
