"""One ATOM/HETATM parser for prep and autobox: first model, altloc blank or A, one water set."""

from __future__ import annotations

import pytest

from smeltery.docking.autobox import box_from_ligand, box_from_residues
from smeltery.pdb_records import WATER_RESIDUES, iter_atoms
from smeltery.prep import PrepError, reference_ligand_centroid


def _rec(serial, name, res, chain, num, xyz, element, rec="HETATM", alt=" "):
    return (
        f"{rec:<6}{serial:>5} {name:<4}{alt}{res:>3} {chain}{num:>4}    "
        f"{xyz[0]:>8.3f}{xyz[1]:>8.3f}{xyz[2]:>8.3f}  1.00  0.00          {element:>2}"
    )


TWO_MODELS = "\n".join(
    [
        "MODEL        1",
        _rec(1, "C1", "LIG", "A", 1, (0.0, 0.0, 0.0), "C"),
        _rec(2, "C2", "LIG", "A", 1, (2.0, 0.0, 0.0), "C"),
        "ENDMDL",
        "MODEL        2",
        _rec(1, "C1", "LIG", "A", 1, (50.0, 0.0, 0.0), "C"),
        _rec(2, "C2", "LIG", "A", 1, (52.0, 0.0, 0.0), "C"),
        "ENDMDL",
    ]
)
ALTLOC = "\n".join(
    [
        _rec(1, "C1", "LIG", "A", 1, (0.0, 0.0, 0.0), "C", alt="A"),
        _rec(2, "C1", "LIG", "A", 1, (40.0, 0.0, 0.0), "C", alt="B"),
        _rec(3, "C2", "LIG", "A", 1, (2.0, 0.0, 0.0), "C"),
    ]
)
SOL = "\n".join(
    [
        _rec(1, "O", "SOL", "A", 9, (30.0, 0.0, 0.0), "O"),
        _rec(2, "C1", "LIG", "A", 1, (0.0, 0.0, 0.0), "C"),
    ]
)


def test_iter_atoms_first_model_by_default_all_models_on_request():
    assert len(list(iter_atoms(TWO_MODELS))) == 2
    assert len(list(iter_atoms(TWO_MODELS, first_model=False))) == 4


def test_box_from_ligand_uses_the_first_model_only(tmp_path):
    pdb = tmp_path / "ens.pdb"
    pdb.write_text(TWO_MODELS)
    box = box_from_ligand(pdb, padding=0.0, min_size=0.0)
    assert box.center == (1.0, 0.0, 0.0)
    assert box.size == (2.0, 0.0, 0.0)


def test_box_from_residues_uses_the_first_model_only(tmp_path):
    pdb = tmp_path / "ens.pdb"
    pdb.write_text(TWO_MODELS.replace("HETATM", "ATOM  "))
    box = box_from_residues(pdb, ["A:1"], padding=0.0, min_size=0.0)
    assert box.center == (1.0, 0.0, 0.0)


def test_box_and_prep_agree_on_altloc_blank_or_a(tmp_path):
    pdb = tmp_path / "alt.pdb"
    pdb.write_text(ALTLOC)
    box = box_from_ligand(pdb, padding=0.0, min_size=0.0)  # the B conformer at x=40 is not boxed
    assert box.size == (2.0, 0.0, 0.0)
    centre, info = reference_ligand_centroid(ALTLOC, "LIG")
    assert centre == box.center
    assert info["n_heavy_atoms"] == 2


def test_sol_is_water_in_prep_and_autobox(tmp_path):
    assert "SOL" in WATER_RESIDUES
    pdb = tmp_path / "sol.pdb"
    pdb.write_text(SOL)
    assert box_from_ligand(pdb).center == (0.0, 0.0, 0.0)  # SOL is not a second ligand needing resname=
    with pytest.raises(Exception, match="no such HETATM residue"):
        reference_ligand_centroid(SOL, "SOL")


def test_bad_coordinates_keep_each_callers_error_type(tmp_path):
    bad = "HETATM    1  C1  LIG A   1       x.xxx   0.000   0.000  1.00  0.00           C"
    pdb = tmp_path / "bad.pdb"
    pdb.write_text(bad)
    with pytest.raises(ValueError, match="bad coordinates"):
        box_from_ligand(pdb)
    with pytest.raises(PrepError, match="bad coordinates"):
        reference_ligand_centroid(bad, "LIG")


def test_element_falls_back_to_the_atom_name_when_the_column_is_blank():
    line = _rec(1, "ZN", "ZN", "A", 1, (0.0, 0.0, 0.0), "").rstrip()
    assert next(iter_atoms(line)).element == "ZN"
    line = _rec(1, "CA", "ALA", "A", 1, (0.0, 0.0, 0.0), "", rec="ATOM").rstrip()
    assert next(iter_atoms(line)).element == "C"
