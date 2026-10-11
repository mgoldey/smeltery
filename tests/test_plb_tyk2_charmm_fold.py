"""`run_plb_ddE.fold_base`: a second charge model is compared with the STORED energies, which must enter bit for bit,
and only when the geometry and QM inputs are identical.

Mutation checks done by hand when this file was written (each made at least one test below fail, then reverted):
not copying the stored vacuum energies, shifting the pose index of the stored field energies by one, dropping the
`pose_geometry_sha256` comparison, dropping the `jitter_deg` comparison, allowing a base model that is also a model of
the new run.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "benchmarks" / "plb" / "results" / "tyk2_sto3g.json"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def base():
    return json.loads(BASE.read_text())


@pytest.fixture()
def meta(base):
    i = base["inputs"]
    keys = ("target", "reference", "basis", "cutoff_ang", "seed", "jitter_deg", "jitter_ang", "n_poses", "pivot_ang")
    m = {k: i[k] for k in keys}
    m["pose_geometry_sha256"] = dict(i["pose_geometry_sha256"])
    m["excluded_ligands"] = dict(i["excluded_ligands"])
    m["models"] = ["charmm36"]
    return m


def test_stored_energies_enter_unchanged_with_zero_cpu(base, meta):
    run = _script("run_plb_ddE")
    recs, info = run.fold_base(base, "amber14", meta, "0" * 64)
    res = base["results"]
    for n, vals in res["E_vac_hartree"].items():
        for k, e in enumerate(vals):
            assert recs[("vac", None, n, k)]["energy_hartree"] == e  # exact, not approximate
    for n, vals in res["E_field_hartree"]["amber14"].items():
        for j, e in enumerate(vals):
            r = recs[("field", "amber14", n, base["inputs"]["pose_indices"]["amber14"][j])]
            assert r["energy_hartree"] == e
            assert r["min_dist_to_field_ang"] == res["min_dist_to_field_ang"]["amber14"][n][j]
    assert all(r["cpu_s"] == 0.0 and r["error"] is None for r in recs.values())
    assert len(recs) == 13 * 8 * 2
    assert info["model"] == "amber14" and info["pose_indices"] == list(range(8))


@pytest.mark.parametrize("key", ["seed", "jitter_deg", "jitter_ang", "basis", "cutoff_ang", "n_poses", "target"])
def test_a_changed_geometry_or_qm_input_is_refused(base, meta, key):
    run = _script("run_plb_ddE")
    meta[key] = "changed" if isinstance(meta[key], str) else meta[key] + 1
    with pytest.raises(SystemExit, match=key):
        run.fold_base(base, "amber14", meta, "0" * 64)


def test_different_pose_geometries_are_refused(base, meta):
    run = _script("run_plb_ddE")
    n = next(iter(meta["pose_geometry_sha256"]))
    meta["pose_geometry_sha256"][n] = "f" * 64
    with pytest.raises(SystemExit, match="pose geometries"):
        run.fold_base(base, "amber14", meta, "0" * 64)


def test_the_base_model_must_exist_and_must_not_be_rerun(base, meta):
    run = _script("run_plb_ddE")
    with pytest.raises(SystemExit, match="base-model"):
        run.fold_base(base, "charmm36", meta, "0" * 64)  # not in the base
    both = copy.deepcopy(meta)
    both["models"] = ["amber14"]
    with pytest.raises(SystemExit, match="base-model"):
        run.fold_base(base, "amber14", both, "0" * 64)  # would silently overwrite the stored model
