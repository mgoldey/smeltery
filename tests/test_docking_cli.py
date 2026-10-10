"""`smeltery run` with a docked parent: real Vina, the committed pentapeptide pocket (issue #17).

The parent (ethanol) is docked; propanol and butanol are PAIRED to its docked poses, not docked themselves.
Skips (with the install command) unless the `docking` extra is installed; CI's `docking` job installs it
(with `posebusters`) and fails if any of these skip for a missing module.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys

import numpy as np
import pytest

_MISSING = [m for m in ("vina", "meeko", "rdkit") if importlib.util.find_spec(m) is None]
pytestmark = [
    pytest.mark.skipif(
        bool(_MISSING),
        reason=f"needs the docking extra: pip install 'smeltery[docking]' ({', '.join(_MISSING)} missing)",
    ),
    # the pocket file is a PDB, so charges come from pdb2pqr30; CI's docking job installs it and fails on this skip
    pytest.mark.skipif(shutil.which("pdb2pqr30") is None, reason="pdb2pqr30 not installed (pip install pdb2pqr)"),
]

DATA = pathlib.Path(__file__).parent / "data"
RECEPTOR = DATA / "pocket_pep5.pdbqt"
POCKET = DATA / "pocket_pep5.pdb"

CONFIG = f"""
campaign = "dock-cli"
parent = "ethanol"

[candidates]
ethanol = "CCO"
propanol = "CCCO"
butanol = "CCCCO"

[poses]
relax_unmapped = true

[docking]
receptor = "{RECEPTOR}"
box_center = [0.4, -0.1, -0.1]
box_size = [22.0, 16.0, 16.0]
seeds = [7]
exhaustiveness = 4
n_poses = 3

[pocket]
file = "{POCKET}"

[cut]
keep = 1
z = 2.0
floor = 0.0
"""


def _write(tmp_path, text=CONFIG, name="dock.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def _cli(*args):
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1"}
    return subprocess.run([sys.executable, "-m", "smeltery", *args], capture_output=True, text=True, env=env)


def _plan(tmp_path, text=CONFIG):
    from smeltery.cli import build_plan, load_config

    return build_plan(load_config(_write(tmp_path, text)))


def test_the_parent_is_docked_and_analogues_pair_to_its_docked_poses_exactly(tmp_path):
    plan = _plan(tmp_path)
    parent = plan.parent
    assert 1 <= len(parent.poses) <= 3
    assert plan.poses.parent_poses_given is True
    for cand in plan.candidates:
        if cand is parent:
            continue
        assert len(cand.poses) == len(parent.poses)
        for i, pose in enumerate(cand.poses):
            for a, p in plan.poses.scaffold_maps[cand.name]:
                # the core is an exact copy of the DOCKED parent pose, even after the substituent was relaxed
                assert np.array_equal(pose.coords_ang[a], parent.poses[i].coords_ang[p])
    assert [e["stage"] for e in plan.stage_log] == ["docking", "poses", "pocket"]


def test_the_parent_poses_are_the_docked_ones_not_a_fresh_ensemble(tmp_path):
    from smeltery import Candidate
    from smeltery.cli import _dock_parent, load_config

    plan = _plan(tmp_path)
    fresh = Candidate("ethanol", "CCO")
    _dock_parent(load_config(_write(tmp_path, name="again.toml")), fresh, [], {})  # an independent docking run
    assert len(fresh.poses) == len(plan.parent.poses)
    for a, b in zip(fresh.poses, plan.parent.poses, strict=True):
        assert np.array_equal(a.coords_ang, b.coords_ang)  # same seed, same poses: not re-embedded by the pairing


def test_the_inputs_name_what_the_stages_read_by_digest(tmp_path):
    plan = _plan(tmp_path)
    assert plan.inputs["docking"]["receptor_sha256"] == hashlib.sha256(RECEPTOR.read_bytes()).hexdigest()
    assert plan.inputs["docking"]["box"] == {"center": [0.4, -0.1, -0.1], "size": [22.0, 16.0, 16.0]}
    assert plan.inputs["pocket"]["input_sha256"] == hashlib.sha256(POCKET.read_bytes()).hexdigest()
    assert "field" not in plan.inputs  # a pocket file is identified by its digest, not by thousands of charges
    assert [t["name"] for t in plan.tiers] == ["docking", "paired_poses", "field_interaction"]


def test_changing_a_docking_knob_changes_the_digest_and_a_rerun_reproduces_it(tmp_path):
    from smeltery.record import RunRecord

    def digest(text):
        plan = _plan(tmp_path, text)
        return RunRecord("c", plan.inputs, plan.tiers, {}).input_digest

    base = digest(CONFIG)
    assert digest(CONFIG) == base
    assert digest(CONFIG.replace("exhaustiveness = 4", "exhaustiveness = 8")) != base
    assert digest(CONFIG.replace("seeds = [7]", "seeds = [8]")) != base
    assert digest(CONFIG.replace("box_size = [22.0, 16.0, 16.0]", "box_size = [20.0, 16.0, 16.0]")) != base


def test_a_missing_receptor_is_a_config_error(tmp_path):
    p = _write(tmp_path, CONFIG.replace(str(RECEPTOR), str(tmp_path / "nope.pdbqt")))
    r = _cli("run", str(p), "--plan")
    assert r.returncode == 2 and "does not exist" in r.stderr


def test_posebusters_refuses_unrelaxed_analogues_of_a_docked_parent_with_the_reason(tmp_path):
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    text = CONFIG.replace("[poses]\nrelax_unmapped = true\n", "") + "\n[gates]\nposebusters = true\n"
    r = _cli("run", str(_write(tmp_path, text)), "--plan")
    assert r.returncode == 3, r.stdout + r.stderr
    assert "stage 'posebusters' failed" in r.stderr and "relax_unmapped = true" in r.stderr


@pytest.mark.needs_ferric
def test_end_to_end_with_every_gate_runs_records_each_stage_and_reproduces_the_plan_digest(tmp_path):
    pytest.importorskip("posebusters", reason="needs the posebusters extra")
    text = CONFIG + "\n[gates]\nposebusters = true\nforcefield = true\n"
    cfg = _write(tmp_path, text)
    planned = _cli("run", str(cfg), "--plan")
    assert planned.returncode == 0, planned.stdout + planned.stderr
    digest = planned.stdout.split("input_digest")[1].split()[0]

    out = tmp_path / "out"
    r = _cli("run", str(cfg), "--out", str(out))
    assert r.returncode == 0, r.stdout + r.stderr
    (path,) = out.glob("run-*.json")
    rec = json.loads(path.read_text())
    assert rec["input_digest"] == digest  # the plan and the run describe the same inputs
    assert [e["stage"] for e in rec["results"]["stages"]] == ["docking", "poses", "pocket", "posebusters", "forcefield"]
    assert [t["name"] for t in rec["tiers"]] == ["docking", "paired_poses", "forcefield", "field_interaction"]
    ddE = rec["results"]["paired_ddE"]
    assert set(ddE) == {"propanol", "butanol"}
    assert all(np.isfinite(v["mean"]) and v["n"] == len(rec["results"]["stages"][0]["scores"]) for v in ddE.values())
    assert "stages: docking" in r.stdout and "tie groups" in r.stdout


@pytest.mark.skipif(shutil.which("xtb") is None, reason="the `xtb` binary is not on PATH")
def test_the_xtb_gate_records_a_finite_energy_per_pose(tmp_path):
    plan = _plan(tmp_path, CONFIG + "\n[gates]\nxtb = true\n")
    entry = plan.stage_log[-1]
    assert entry["stage"] == "xtb"
    energies = [x for v in entry["E_gfn2"].values() for x in v]
    assert len(energies) == 3 * len(plan.parent.poses) and all(np.isfinite(energies))
    assert [t["name"] for t in plan.tiers][-2:] == ["gfn2", "field_interaction"]
