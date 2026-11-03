"""What `smeltery run` leaves under OUT (docs/gui-contract.md section 1): per-pose values with units, one SDF per
candidate, the field and the receptor, and that the shared GUI fixture has the same shape as the real thing.

Everything but the last test is ferric-free: the plan is built for real (RDKit poses, the field placed from the
parent), and the SCF tier is replaced by fixed per-pose numbers through `run_plan(..., measure=...)`.
"""

import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from gui_fixture import make_run_dir
from rdkit import Chem

from smeltery import cli
from smeltery.cli import StageError, build_plan, load_config, main, run_plan, write_outputs
from smeltery.model import Pose
from smeltery.record import RunRecord
from smeltery.tiers import FieldInteraction

ROOT = Path(__file__).resolve().parent.parent
POCKET_PDB = ROOT / "tests" / "data" / "pocket_pep5.pdb"

CONFIG = """
campaign = "outputs"
parent = "benzoic"

[candidates]
benzoic = "OC(=O)c1ccccc1"
4-F = "OC(=O)c1ccc(F)cc1"
"4 Cl" = "OC(=O)c1ccc(Cl)cc1"

[poses]
n_poses = 3

[[pocket.site]]
q = 1.0
atom = 0
from_atom = 1
distance = 3.0

[[pocket.site]]
q = -0.5
atom = 2
from_atom = 1
distance = 3.0

[cut]
keep = 1
z = 2.0
floor = 0.0
"""

SHIFT = {"benzoic": 0.0, "4-F": 1.5, "4 Cl": 4.0, "4_Cl": 4.0}


def fill(plan):
    """Stand-in for the SCFs: fixed dE_int per pose (kcal/mol), parent noise shared by the analogues."""
    for c in plan.candidates:
        n = len(c.poses)
        c.per_pose["dE_int"] = [-60.0 + 0.7 * i + SHIFT[c.name] + 0.01 * i * (c is not plan.parent) for i in range(n)]


def make_plan(tmp_path, text=CONFIG):
    p = tmp_path / "c.toml"
    p.write_text(text)
    return build_plan(load_config(p), out_dir=tmp_path / "out")


def run_and_write(plan, out_dir):
    rec, out = run_plan(plan, measure=fill)
    return write_outputs(plan, rec, out_dir), out


def read_sdf(path):
    return [m for m in Chem.SDMolSupplier(str(path), removeHs=False, sanitize=True)]


def test_the_record_carries_per_pose_values_units_and_the_field(tmp_path):
    plan = make_plan(tmp_path)
    digest = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest
    path, _ = run_and_write(plan, tmp_path / "out")
    rec = json.loads(path.read_text())
    assert path.name == f"run-{digest[:12]}.json" and rec["input_digest"] == digest  # outputs are not inputs
    res = rec["results"]
    assert set(res["per_pose"]) == {"benzoic", "4-F", "4 Cl"}  # the parent too
    assert res["candidate_order"] == ["benzoic", "4-F", "4 Cl"]  # config order; the JSON keys are sorted
    for c in plan.candidates:
        assert res["per_pose"][c.name] == {"dE_int": c.per_pose["dE_int"]}
    assert res["units"] == {"dE_int": "kcal/mol"}
    assert res["field"] == [[c.q, *c.xyz_ang] for c in plan.field]
    assert [q for q, *_ in res["field"]] == [1.0, -0.5]
    assert res["receptor"] is None  # sites: no receptor exists
    assert "decisions_in" not in res
    assert set(res["poses"]) == set(res["per_pose"])


def test_pose_sdfs_round_trip_atom_order_coordinates_and_properties(tmp_path):
    plan = make_plan(tmp_path)
    out_dir = tmp_path / "out"
    path, _ = run_and_write(plan, out_dir)
    res = json.loads(path.read_text())["results"]
    assert res["poses"]["4 Cl"]["file"] == "poses/4_Cl.sdf"  # sanitised
    for c in plan.candidates:
        entry = res["poses"][c.name]
        f = out_dir / entry["file"]
        assert entry["sha256"] == hashlib.sha256(f.read_bytes()).hexdigest()
        assert entry["n_poses"] == len(c.poses) == 3
        ref = Chem.AddHs(Chem.MolFromSmiles(c.smiles))
        mols = read_sdf(f)
        assert len(mols) == len(c.poses)
        for i, (m, pose) in enumerate(zip(mols, c.poses, strict=True)):
            assert tuple(a.GetSymbol() for a in m.GetAtoms()) == pose.symbols
            np.testing.assert_allclose(m.GetConformer().GetPositions(), pose.coords_ang, atol=1e-4)
            # bonds come from the SMILES, not perceived: same molecule, same bond list
            assert Chem.MolToSmiles(Chem.RemoveHs(m)) == Chem.MolToSmiles(Chem.MolFromSmiles(c.smiles))
            assert {(b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in m.GetBonds()} == {
                (b.GetBeginAtomIdx(), b.GetEndAtomIdx()) for b in ref.GetBonds()
            }
            assert m.GetProp("_Name") == f"{c.name} pose {i}"
            assert m.GetProp("smeltery_candidate") == c.name
            assert m.GetProp("smeltery_pose_index") == str(i)
            assert m.GetProp("smeltery_parent") == ("1" if c is plan.parent else "0")
            assert m.GetProp("dE_int") == f"{c.per_pose['dE_int'][i]:.10g}"
            if c is plan.parent:
                assert not m.HasProp("paired_ddE")
            else:
                paired = res["paired_ddE"][c.name]["per_pose"][i]
                assert m.GetProp("paired_ddE") == f"{paired:.10g}"
                assert float(m.GetProp("paired_ddE")) == pytest.approx(
                    c.per_pose["dE_int"][i] - plan.parent.per_pose["dE_int"][i], abs=1e-8
                )


def test_a_pose_not_in_smiles_with_h_order_is_a_stage_error_and_nothing_is_written(tmp_path):
    plan = make_plan(tmp_path)
    c = plan.candidates[1]
    p0 = c.poses[0]
    c.poses[0] = Pose(tuple(reversed(p0.symbols)), p0.coords_ang[::-1].copy())
    rec, _ = run_plan(plan, measure=fill)
    with pytest.raises(StageError, match=r"4-F pose 0 .*SMILES-with-H order"):
        write_outputs(plan, rec, tmp_path / "w")
    assert not (tmp_path / "w" / "poses").exists() and not list((tmp_path / "w").glob("run-*.json"))


def test_two_names_that_sanitise_to_one_file_are_a_stage_error(tmp_path, monkeypatch, capsys):
    text = CONFIG.replace('"4 Cl" = "OC(=O)c1ccc(Cl)cc1"', '"4 Cl" = "OC(=O)c1ccc(Cl)cc1"\n4_Cl = "OC(=O)c1ccc(Cl)cc1"')
    plan = make_plan(tmp_path, text)
    rec, _ = run_plan(plan, measure=fill)
    with pytest.raises(StageError, match=r"'4 Cl' and '4_Cl' both sanitise to pose file '4_Cl.sdf'"):
        write_outputs(plan, rec, tmp_path / "w")
    # through the CLI: exit 3, naming the stage
    monkeypatch.setattr(FieldInteraction, "run", lambda self, cands, ctx: fill(_PlanLike(cands)))
    p = tmp_path / "clash.toml"
    p.write_text(text)
    assert main(["run", str(p), "--out", str(tmp_path / "cli")]) == 3
    assert "stage 'outputs' failed" in capsys.readouterr().err


class _PlanLike:
    """What `fill` reads from a plan, built from the candidate list FieldInteraction.run receives."""

    def __init__(self, cands):
        self.candidates, self.parent = cands, cands[0]


def test_a_quantity_no_tier_declares_is_refused(tmp_path):
    plan = make_plan(tmp_path)

    def extra(p):
        fill(p)
        p.parent.per_pose["mystery"] = [0.0] * len(p.parent.poses)

    with pytest.raises(StageError, match="'mystery', which no tier that ran declares"):
        run_plan(plan, measure=extra)


def test_the_receptor_is_relative_inside_out_and_absolute_outside(tmp_path):
    plan = make_plan(tmp_path)
    plan.receptor_pdb = POCKET_PDB  # as build_plan sets it for a [pocket] file that is a .pdb
    sha = hashlib.sha256(POCKET_PDB.read_bytes()).hexdigest()
    path, _ = run_and_write(plan, tmp_path / "a")
    assert json.loads(path.read_text())["results"]["receptor"] == {"file": str(POCKET_PDB.resolve()), "sha256": sha}

    inside = tmp_path / "b" / "structure" / "receptor.pdb"  # as [structure] writes it
    inside.parent.mkdir(parents=True)
    shutil.copy(POCKET_PDB, inside)
    plan = make_plan(tmp_path)
    plan.receptor_pdb = inside
    path, _ = run_and_write(plan, tmp_path / "b")
    assert json.loads(path.read_text())["results"]["receptor"] == {"file": "structure/receptor.pdb", "sha256": sha}


def test_a_pqr_pocket_file_has_no_receptor_and_a_pdb_one_does(tmp_path):
    from smeltery.cli import _load_field  # the path build_plan derives the receptor from

    plan = make_plan(tmp_path)
    cfg = {"pocket": {"file": str(ROOT / "tests" / "data" / "gly3.pqr")}, "candidates": {"a": "C"}}
    _, path = _load_field(cfg, plan.parent, {}, [])
    assert path.suffix == ".pqr"
    # build_plan keeps only a .pdb as the receptor
    assert (path if path.suffix.lower() == ".pdb" else None) is None


def test_forcefield_values_are_per_pose_quantities_with_units_and_sdf_properties(tmp_path):
    plan = make_plan(tmp_path, CONFIG + "\n[gates]\nforcefield = true\n")
    path, _ = run_and_write(plan, tmp_path / "out")
    res = json.loads(path.read_text())["results"]
    assert res["units"] == {"dE_int": "kcal/mol", "E_mmff": "kcal/mol", "E_mmff_relaxed": "kcal/mol"}
    for c in plan.candidates:
        assert set(res["per_pose"][c.name]) == {"dE_int", "E_mmff", "E_mmff_relaxed"}
        for m in read_sdf(tmp_path / "out" / res["poses"][c.name]["file"]):
            i = int(m.GetProp("smeltery_pose_index"))
            assert m.GetProp("E_mmff") == f"{c.per_pose['E_mmff'][i]:.10g}"
            assert m.HasProp("E_mmff_relaxed")


def test_main_writes_the_outputs(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(FieldInteraction, "run", lambda self, cands, ctx: fill(_PlanLike(cands)))
    p = tmp_path / "c.toml"
    p.write_text(CONFIG)
    out_dir = tmp_path / "out"
    assert main(["run", str(p), "--out", str(out_dir)]) == 0
    (path,) = out_dir.glob("run-*.json")
    res = json.loads(path.read_text())["results"]
    assert {"per_pose", "units", "poses", "field", "receptor"} <= set(res)
    assert sorted(f.name for f in (out_dir / "poses").iterdir()) == ["4-F.sdf", "4_Cl.sdf", "benzoic.sdf"]
    assert f"run record: {path}" in capsys.readouterr().out


def test_the_gui_fixture_has_the_shape_the_writer_produces(tmp_path):
    """tests/gui_fixture.py is what the GUI data layer and viewers are tested against: same results keys, same
    entry shapes, same SDF property names and file-name rule as a real run of the same kind (sites, dE_int)."""
    plan = make_plan(tmp_path)
    path, _ = run_and_write(plan, tmp_path / "real")
    real = json.loads(path.read_text())
    fixture_dir = make_run_dir(tmp_path / "fx")
    (fpath,) = fixture_dir.glob("run-*.json")
    fx = json.loads(fpath.read_text())

    assert set(fx) == set(real)
    assert set(fx["results"]) == set(real["results"])
    assert set(fx["inputs"]) == set(real["inputs"])
    assert fx["results"]["units"] == real["results"]["units"]
    assert {type(v) for v in fx["results"]["per_pose"].values()} == {dict}
    for key in ("poses", "per_pose", "paired_ddE"):
        shapes = {frozenset(v) for v in real["results"][key].values()}
        assert {frozenset(v) for v in fx["results"][key].values()} == shapes, key
    assert all(len(x) == 4 for x in fx["results"]["field"]) and all(len(x) == 4 for x in real["results"]["field"])
    assert fx["results"]["receptor"] is None is real["results"]["receptor"]
    assert [set(r) for r in fx["results"]["funnel"]] == [set(r) for r in real["results"]["funnel"]]
    for name, entry in fx["results"]["poses"].items():
        assert entry["file"] == f"poses/{cli.pose_file_name(name)}"

    def props(run_dir, rec, name):
        (m, *_) = read_sdf(run_dir / rec["results"]["poses"][name]["file"])
        return set(m.GetPropNames()), m.GetProp("_Name")

    fx_parent, real_parent = fx["inputs"]["parent"], real["inputs"]["parent"]
    fx_analogue = next(n for n in fx["results"]["poses"] if n != fx_parent)
    real_analogue = next(n for n in real["results"]["poses"] if n != real_parent)
    assert props(fixture_dir, fx, fx_parent)[0] == props(path.parent, real, real_parent)[0]
    assert props(fixture_dir, fx, fx_analogue)[0] == props(path.parent, real, real_analogue)[0]
    assert props(fixture_dir, fx, fx_analogue)[1] == f"{fx_analogue} pose 0"


@pytest.mark.needs_ferric
def test_a_real_run_writes_sdfs_that_match_its_poses(tmp_path):
    text = (
        CONFIG.replace('"4 Cl" = "OC(=O)c1ccc(Cl)cc1"\n', "")
        .replace("benzoic", "ethanol")
        .replace('"OC(=O)c1ccccc1"', '"CCO"')
        .replace('4-F = "OC(=O)c1ccc(F)cc1"', 'propanol = "CCCO"\nbutanol = "CCCCO"')
        .replace("n_poses = 3", "n_poses = 3\nrelax_unmapped = true")
        .replace("atom = 0\n", "atom = 2\n")
    )
    p = tmp_path / "c.toml"
    p.write_text(text)
    out_dir = tmp_path / "out"
    assert main(["run", str(p), "--out", str(out_dir)]) == 0
    (path,) = out_dir.glob("run-*.json")
    res = json.loads(path.read_text())["results"]
    assert set(res["per_pose"]) == {"ethanol", "propanol", "butanol"}
    for name, entry in res["poses"].items():
        f = out_dir / entry["file"]
        assert hashlib.sha256(f.read_bytes()).hexdigest() == entry["sha256"]
        mols = read_sdf(f)
        assert len(mols) == entry["n_poses"] == len(res["per_pose"][name]["dE_int"])
        for m, v in zip(mols, res["per_pose"][name]["dE_int"], strict=True):
            assert float(m.GetProp("dE_int")) == pytest.approx(v, rel=1e-9)
