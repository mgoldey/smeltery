"""Flexible-receptor plumbing, automatic boxes, ensemble score. No Vina/Meeko needed."""

from __future__ import annotations

import math
import sys
import types

import pytest

from smeltery.docking import (
    Box, DockResult, Docking, DockingTarget, Receptor, box_from_coords,
    box_from_ligand, box_from_residues, ensemble_score, vina_dock,
)
from smeltery.docking.vina_dock import _parse_pdbqt_models, parse_flex_blocks
from smeltery.model import Candidate

PDB = (
    "ATOM      1  CA  ARG A  45      10.000  10.000  10.000  1.00  0.00           C\n"
    "ATOM      2  CB  ARG A  45      12.000  10.000  10.000  1.00  0.00           C\n"
    "ATOM      3  CA  LEU A  46      30.000  20.000  10.000  1.00  0.00           C\n"
    "HETATM    4  C1  LIG A 200      20.000  20.000  20.000  1.00  0.00           C\n"
    "HETATM    5  C2  LIG A 200      22.000  24.000  20.000  1.00  0.00           C\n"
    "HETATM    6  O   HOH A 300       0.000   0.000   0.000  1.00  0.00           O\n"
)

OUT = (
    "MODEL 1\nREMARK VINA RESULT:    -7.5      0.000      0.000\n"
    "ATOM      1  C   LIG A   1       1.000   2.000   3.000  0.00  0.00     0.000 C \n"
    "TORSDOF 0\nBEGIN_RES ARG A  45\n"
    "ATOM      9  CB  ARG A  45       5.000   5.000   5.000  0.00  0.00     0.000 C \n"
    "END_RES ARG A  45\nENDMDL\n"
    "MODEL 2\nREMARK VINA RESULT:    -6.0      0.000      0.000\n"
    "ATOM      1  C   LIG A   1       1.500   2.000   3.000  0.00  0.00     0.000 C \n"
    "ENDMDL\n"
)


def test_parser_keeps_flex_residue_atoms_out_of_the_ligand():
    (m1, m2) = _parse_pdbqt_models(OUT)
    assert len(m1[0]) == 1 and m1[1] == [(1.0, 2.0, 3.0)] and m1[2] == -7.5
    assert len(m2[0]) == 1


def test_flex_blocks_are_aligned_per_model():
    b = parse_flex_blocks(OUT)
    assert len(b) == 2 and "BEGIN_RES ARG A  45" in b[0] and "END_RES" in b[0]
    assert b[1] == ""


def test_box_from_ligand_ignores_water_and_pads():
    box = box_from_ligand(_w(PDB), padding=8.0)
    assert box.center == (21.0, 22.0, 20.0)
    assert box.size == (20.0, 20.0, 20.0)  # 18, 20, 16 -> floored at min_size 20


def test_box_from_ligand_needs_resname_when_ambiguous(tmp_path):
    f = tmp_path / "a.pdb"
    f.write_text(PDB + "HETATM    7  C1  GOL A 400       5.000   5.000   5.000  1.00  0.00           C\n")
    with pytest.raises(ValueError, match="resname"):
        box_from_ligand(f)
    assert box_from_ligand(f, "LIG").center == (21.0, 22.0, 20.0)


def test_box_from_residues_and_missing(tmp_path):
    f = _w(PDB)
    assert box_from_residues(f, ["A:45"], padding=8).center == (11.0, 10.0, 10.0)
    with pytest.raises(ValueError, match="not found"):
        box_from_residues(f, ["B:1"])
    with pytest.raises(ValueError, match="chain:resnum"):
        box_from_residues(f, ["45"])


def test_box_from_coords_min_size():
    assert box_from_coords([[0, 0, 0]], padding=1.0).size == (20.0, 20.0, 20.0)


def test_ensemble_score_bounds_and_nan():
    assert ensemble_score([-7.0]) == pytest.approx(-7.0)
    many = ensemble_score([-7.0, -7.0, -7.0])
    assert many < -7.0  # more good poses -> better
    assert ensemble_score([-7.0, 0.0]) == pytest.approx(-7.0, abs=1e-3)  # bad pose barely moves it
    with pytest.raises(ValueError):
        ensemble_score([float("nan")])
    with pytest.raises(ValueError):
        ensemble_score([])


class _FakeVina:
    calls: dict = {}

    def __init__(self, **kw): ...

    def set_receptor(self, *a, **k):
        type(self).calls = {"args": a, "kw": k}

    def set_ligand_from_string(self, *a, **k): ...
    def compute_vina_maps(self, *a, **k): ...
    def dock(self, *a, **k): ...
    def poses(self, n_poses=1):
        return OUT


def test_flexible_receptor_reaches_vina_and_flex_comes_back(monkeypatch, tmp_path):
    mod = types.ModuleType("vina")
    mod.Vina = _FakeVina
    monkeypatch.setitem(sys.modules, "vina", mod)
    monkeypatch.setattr(vina_dock, "_ligand_pdbqt_from_rdkit", lambda mol: "L")
    rigid, flex = tmp_path / "r_rigid.pdbqt", tmp_path / "r_flex.pdbqt"
    rigid.write_text("x"); flex.write_text("x")
    run = vina_dock.dock_ligand(object(), Receptor(rigid, flex), (0, 0, 0))
    assert _FakeVina.calls["kw"] == {
        "rigid_pdbqt_filename": str(rigid), "flex_pdbqt_filename": str(flex)}
    assert "BEGIN_RES" in run.poses[0].flex_pdbqt and run.poses[1].flex_pdbqt == ""
    # rigid path unchanged
    vina_dock.dock_ligand(object(), rigid, (0, 0, 0))
    assert _FakeVina.calls["args"] == (str(rigid),)


def test_missing_flex_file_is_an_error_not_a_rigid_fallback(tmp_path, monkeypatch):
    mod = types.ModuleType("vina")
    mod.Vina = _FakeVina
    monkeypatch.setitem(sys.modules, "vina", mod)
    r = tmp_path / "r.pdbqt"; r.write_text("x")
    run = vina_dock.dock_ligand(object(), Receptor(r, tmp_path / "nope"), (0, 0, 0))
    assert not run.ok and "not found" in run.error


def test_tier_records_flex_sidechains_sorted_with_poses():
    import numpy as np
    from smeltery.model import Pose

    class P:
        name = "p"
        def settings(self): return {}
        def score_unit(self): return "kcal/mol"
        def dock(self, mol, receptor, box, seed, exhaustiveness=4):
            ps = [Pose(("H",), np.zeros((1, 3)))] * 2
            return DockResult(poses=ps, scores=[-1.0, -9.0], flex_receptor=["a", "b"])

    c = Candidate("m", "C")
    Docking(P()).run([c], {"receptor": "x", "box": Box((0, 0, 0))})
    assert c.per_pose["dock_score"] == [-9.0, -1.0] and c.receptor_flex == ["b", "a"]


def test_dockresult_flex_length_validated():
    with pytest.raises(ValueError):
        DockResult(poses=[], scores=[], flex_receptor=["a"])


def test_target_ctx_rigid_is_path_flexible_is_receptor(tmp_path):
    box = Box((0, 0, 0))
    assert DockingTarget(Receptor(tmp_path / "r.pdbqt"), box).ctx()["receptor"] == tmp_path / "r.pdbqt"
    fx = Receptor(tmp_path / "a", tmp_path / "b")
    assert DockingTarget(fx, box).ctx()["receptor"] is fx


def _w(text, _n=[0]):
    import pathlib, tempfile
    d = pathlib.Path(tempfile.mkdtemp()) / "t.pdb"
    d.write_text(text)
    return d
