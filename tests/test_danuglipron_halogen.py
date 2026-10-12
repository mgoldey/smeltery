"""Fast anchors for examples/danuglipron_halogen.py (no SCF: the full run is the example itself)."""

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from rdkit import Chem

from smeltery import Candidate, Measurement, Pose

_SPEC = importlib.util.spec_from_file_location(
    "danuglipron_halogen", Path(__file__).parent.parent / "examples" / "danuglipron_halogen.py"
)
ex = importlib.util.module_from_spec(_SPEC)
sys.modules["danuglipron_halogen"] = ex
_SPEC.loader.exec_module(ex)


def _data():
    try:
        return ex.find_data_dir()
    except FileNotFoundError:
        pytest.skip("danuglipron test data (experiments/danuglipron/data) not available")


def test_twelve_distinct_analogues_each_one_substitution():
    smi = ex.analogue_smiles()
    assert len(smi) == 12 and len(set(smi.values())) == 12
    parent_heavy = Chem.MolFromSmiles(ex.PARENT_SMILES).GetNumHeavyAtoms()
    for name, s in smi.items():
        mol = Chem.MolFromSmiles(s)
        assert mol is not None, name
        assert mol.GetNumHeavyAtoms() == parent_heavy + 1, name  # F, Cl and CH3 each add one heavy atom


def test_bound_pose_is_relabelled_without_moving_atoms():
    pose = ex.bound_pose_in_smiles_order(_data() / "conf_00_cryo_em.xyz")
    pmol = Chem.AddHs(Chem.MolFromSmiles(ex.PARENT_SMILES))
    assert pose.symbols == tuple(a.GetSymbol() for a in pmol.GetAtoms())
    for b in pmol.GetBonds():  # every bond of the SMILES topology has a bonded-length distance
        d = np.linalg.norm(pose.coords_ang[b.GetBeginAtomIdx()] - pose.coords_ang[b.GetEndAtomIdx()])
        assert 0.9 < d < 1.9, (b.GetBeginAtomIdx(), b.GetEndAtomIdx(), d)


def test_self_pairing_copies_every_coordinate_and_analogue_core_is_exact():
    bound = ex.bound_pose_in_smiles_order(_data() / "conf_00_cryo_em.xyz")
    parent = Candidate("parent", ex.PARENT_SMILES)
    selfp = Candidate("self", ex.PARENT_SMILES)
    ana = Candidate("F", ex.analogue_smiles()["bzim-C9-F"])
    ex.BoundPosePairedPoses(bound, n_poses=3).run([parent, selfp, ana], {"parent": parent})
    assert len(parent.poses) == 3
    assert not np.array_equal(parent.poses[0].coords_ang, parent.poses[1].coords_ang)  # poses really differ
    for p, s in zip(parent.poses, selfp.poses, strict=True):
        assert p.symbols == s.symbols and np.array_equal(p.coords_ang, s.coords_ang)
    # analogue keeps all parent atoms but the replaced H exactly, so most coordinates are bit-identical
    for p, a in zip(parent.poses, ana.poses, strict=True):
        shared = sum(any(np.array_equal(x, y) for y in p.coords_ang) for x in a.coords_ang)
        assert shared >= len(p.symbols) - 1


def test_campaign_comparison_is_unverified_without_recorded_values(monkeypatch):
    paired = {"A": Measurement.from_samples([1.0, 2.0, 3.0])}
    rows, status = ex.compare_to_campaign(paired)
    assert status.startswith("UNVERIFIED") and rows[0]["recorded"] is None and rows[0]["agrees"] is None
    monkeypatch.setitem(ex.CAMPAIGN_RECORDED, "A", (2.1, 0.5))
    rows, status = ex.compare_to_campaign(paired)
    assert rows[0]["agrees"] is True and status.startswith("AGREES")
    monkeypatch.setitem(ex.CAMPAIGN_RECORDED, "A", (5.0, 0.5))
    assert ex.compare_to_campaign(paired)[0][0]["agrees"] is False


def test_range_report_is_closed_and_names_the_outside():
    r = ex.range_report({"a": 0.78, "b": 0.9997, "c": 0.7799, "d": 1.0}, 0.78, 0.9997)
    assert r["inside"] == ["a", "b"] and r["outside"] == ["c", "d"]
    assert (r["n_inside"], r["n_outside"]) == (2, 2)


def test_unranked_pairs_counts_a_gap_equal_to_the_floor_as_unresolved():
    paired = {n: Measurement.from_samples([v, v + 0.1]) for n, v in {"A": 0.0, "B": 1.0, "C": 3.0}.items()}
    got = ex.unranked_pairs(paired, 1.0)
    assert [(a, b) for a, b, _ in got] == [("A", "B")]  # gap exactly 1.0 is NOT above the floor; 2.0 is
    assert ex.unranked_pairs(paired, 0.5) == []
    assert len(ex.unranked_pairs(paired, 2.0)) == 2


def test_pairing_tier_records_the_raised_mcs_limit():
    bound = ex.bound_pose_in_smiles_order(_data() / "conf_00_cryo_em.xyz")
    pairing = ex.make_pairing(bound, 4)
    assert pairing.n_poses == 4 and pairing.settings()["mcs_timeout_s"] == ex.MCS_TIMEOUT_S > 10


def _fake_field():
    return SimpleNamespace(provenance={"input_sha256": "x", "pdb2pqr_ff": "AMBER", "cutoff_ang": None})


def _fake_cands():
    return [
        Candidate(n, "C", [Pose(("H", "H"), np.array([[0.0, 0, 0], [0.7 + 0.1 * i, 0, 0]])) for i in range(3)])
        for n in ("A", "B")
    ]


def _fake_one_pose(name, smiles, pose, field):
    return float(pose.coords_ang[1, 0]), 100.0


def test_run_field_tier_stops_at_the_cpu_cap_pose_major_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _fake_one_pose)
    cache = tmp_path / "c.json"
    with pytest.raises(ex.CpuCapReached):
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 1, cache, cap_cpu_s=150.0)
    done = json.loads(cache.read_text())
    assert len(done) == 2 and all(v["cpu_s"] == 100.0 for v in done.values())
    assert sorted(v["dE"] for v in done.values()) == [0.7, 0.7]  # both candidates' pose 0 first, not A's poses 0..2
    cands = _fake_cands()
    ex.run_field_tier(cands, _fake_field(), "T", 1, cache)  # no cap: resumes from the cache
    assert cands[0].per_pose["dE_int"] == pytest.approx([0.7, 0.8, 0.9])
    assert cands[1].per_pose["cpu_s"] == [100.0] * 3


_RUN_LOG: list[Path] = []


def _failing_one_pose(name, smiles, pose, field):
    with open(_RUN_LOG[0], "a") as f:  # side effect visible from the forked worker
        f.write(f"{name}{pose.coords_ang[1, 0]:.1f}\n")
    time.sleep(0.3)  # slow enough that the parent sees the failure while later tasks are still queued
    if name == "A" and pose.coords_ang[1, 0] > 0.75:  # A's poses 1 and 2 fail to converge
        raise RuntimeError("SCF did not converge")
    return float(pose.coords_ang[1, 0]), 100.0


def test_a_failed_scf_aborts_without_running_the_rest_of_the_queue(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _failing_one_pose)
    _RUN_LOG[:] = [tmp_path / "ran.txt"]
    with pytest.raises(RuntimeError, match="did not converge"):
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 1, tmp_path / "c.json")
    # pose-major, one worker: A0 B0 A1(fails) then at most the one prefetched task; A2 and B2 never run
    ran = (tmp_path / "ran.txt").read_text().split()
    assert len(ran) <= 4 and "A0.9" not in ran and "B0.9" not in ran


def test_run_field_tier_cache_is_keyed_on_the_force_field(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _fake_one_pose)
    cache = tmp_path / "c.json"
    ex.run_field_tier(_fake_cands(), _fake_field(), "AMBER", 1, cache)
    assert len(json.loads(cache.read_text())) == 6
    ex.run_field_tier(_fake_cands(), _fake_field(), "CHARMM", 1, cache)
    assert len(json.loads(cache.read_text())) == 12
