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
    return float(pose.coords_ang[1, 0]), 100.0, None


def test_run_field_tier_stops_at_the_cpu_cap_pose_major_and_resumes(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _fake_one_pose)
    cache = tmp_path / "c.json"
    with pytest.raises(ex.CpuCapReached):  # 2 done = 200 s; another 100 s in flight would reach 300 > 250
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 1, cache, cap_cpu_s=250.0, est_cpu_s=100.0)
    done = json.loads(cache.read_text())
    assert len(done) == 2 and all(v["cpu_s"] == 100.0 for v in done.values())
    assert sorted(v["dE"] for v in done.values()) == [0.7, 0.7]  # both candidates' pose 0 first, not A's poses 0..2
    cands = _fake_cands()
    ex.run_field_tier(cands, _fake_field(), "T", 1, cache)  # no cap: resumes from the cache
    assert cands[0].per_pose["dE_int"] == pytest.approx([0.7, 0.8, 0.9])
    assert cands[1].per_pose["cpu_s"] == [100.0] * 3


def test_cap_counts_every_in_flight_scf_at_its_estimate(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _fake_one_pose)
    with pytest.raises(
        ex.CpuCapReached
    ):  # 2 workers: after 0 spent, 2 in flight at 100 each = 200 <= 250, a third is refused
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 2, tmp_path / "c.json", cap_cpu_s=250.0, est_cpu_s=100.0)
    assert len(json.loads((tmp_path / "c.json").read_text())) == 2


_RUN_LOG: list[Path] = []


def _failing_one_pose(name, smiles, pose, field):
    x = float(pose.coords_ang[1, 0])
    with open(_RUN_LOG[0], "a") as f:  # side effect visible from the forked worker
        f.write(f"{name}{x:.1f}\n")
    if name == "A" and x > 0.75:  # A's poses 1 and 2 fail to converge, quickly
        return None, 100.0, f"{name} pose: SCF did not converge"
    time.sleep(0.6 if (name == "B" and x > 0.75) else 0.1)  # B's pose 1 is still in flight when A's fails
    return x, 100.0, None


def test_a_failed_scf_keeps_the_inflight_results_and_is_never_retried(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _failing_one_pose)
    _RUN_LOG[:] = [tmp_path / "ran.txt"]
    cache = tmp_path / "c.json"
    with pytest.raises(ex.ScfFailed, match="did not converge"):
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 2, cache)
    ran = (tmp_path / "ran.txt").read_text().split()
    assert "A0.9" not in ran and "B0.9" not in ran  # nothing submitted after the failure
    entries = list(json.loads(cache.read_text()).values())
    assert sum(1 for e in entries if e.get("error")) == 1  # the failure, with its CPU metered
    assert [e["cpu_s"] for e in entries if e.get("error")] == [100.0]
    assert sorted(e["dE"] for e in entries if e["dE"] is not None) == pytest.approx(
        [0.7, 0.7, 0.8]
    )  # A0, B0 and the in-flight B1
    before = (tmp_path / "ran.txt").read_text()
    with pytest.raises(ex.ScfFailed):  # a second call: cached failure, not retried, no SCF runs
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 2, cache)
    assert "A0.8" in before and (tmp_path / "ran.txt").read_text().count("A0.8") == 1


def test_real_worker_returns_a_failed_scf_with_its_cpu_instead_of_raising(monkeypatch):
    class Boom:
        def run(self, cands, ctx):
            sum(i * i for i in range(300000))  # burn measurable CPU
            raise RuntimeError("SCF did not converge")

    monkeypatch.setattr(ex, "FieldInteraction", Boom)
    pose = _fake_cands()[0].poses[0]
    de, cpu, err = ex._one_pose("A", "C", pose, None)
    assert de is None and cpu > 0.0 and err == "SCF did not converge"


def test_a_cached_failure_stops_a_later_call_even_when_nothing_is_left_to_run(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _fake_one_pose)
    cache = tmp_path / "c.json"
    ex.run_field_tier(_fake_cands(), _fake_field(), "T", 1, cache)
    data = json.loads(cache.read_text())
    k = next(iter(data))
    data[k] = {"dE": None, "cpu_s": 5.0, "error": "SCF did not converge"}
    cache.write_text(json.dumps(data))
    with pytest.raises(ex.ScfFailed, match="cached SCF failure"):
        ex.run_field_tier(_fake_cands(), _fake_field(), "T", 1, cache)


def test_run_field_tier_cache_is_keyed_on_the_force_field(tmp_path, monkeypatch):
    monkeypatch.setattr(ex, "_one_pose", _fake_one_pose)
    cache = tmp_path / "c.json"
    ex.run_field_tier(_fake_cands(), _fake_field(), "AMBER", 1, cache)
    assert len(json.loads(cache.read_text())) == 6
    ex.run_field_tier(_fake_cands(), _fake_field(), "CHARMM", 1, cache)
    assert len(json.loads(cache.read_text())) == 12
