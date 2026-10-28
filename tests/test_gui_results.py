"""smeltery.gui.results against the contract-shaped fixture run (tests/gui_fixture.py)."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from gui_fixture import CANDIDATES, FLOOR, N_POSES, PARENT, Z, make_run_dir

from smeltery.funnel import resolved
from smeltery.gui import (
    ddE_table,
    funnel_rows,
    load_run,
    per_pose_table,
    pose_sdf,
    receptor_pdb,
    resolved_matrix,
)
from smeltery.model import Measurement


@pytest.fixture
def ranked(tmp_path):
    return load_run(make_run_dir(tmp_path))


@pytest.fixture
def unranked(tmp_path):
    return load_run(make_run_dir(tmp_path, unranked=True))


def test_gui_imports_no_toolkit():
    code = (
        "import sys, smeltery.gui; "
        "bad = [m for m in ('molviewspec', 'marimo', 'anywidget', 'mols2grid', 'plotly') if m in sys.modules]; "
        "print(bad)"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "[]"


def test_run_properties(ranked):
    assert ranked.campaign == "gui-fixture"
    assert ranked.parent == PARENT
    assert ranked.candidates == CANDIDATES
    # the record is written with sort_keys, so "config order" reads back as key order
    assert ranked.analogues == [n for n in ranked.candidates if n != PARENT]
    assert sorted(ranked.analogues) == sorted(n for n in CANDIDATES if n != PARENT)
    assert (ranked.z, ranked.floor, ranked.keep) == (Z, FLOOR, 1)
    assert "explicit" in ranked.floor_source
    assert ranked.groups == [["4-F"], ["4-Cl", "4-Br"]]
    assert ranked.survivors == ["4-F"] and not ranked.unranked


def test_load_run_from_json_file(tmp_path):
    out = make_run_dir(tmp_path)
    (path,) = out.glob("run-*.json")
    run = load_run(path)
    assert run.out_dir == out and run.campaign == "gui-fixture"


def test_load_run_needs_exactly_one_record(tmp_path):
    out = make_run_dir(tmp_path)
    (path,) = out.glob("run-*.json")
    (out / "run-000000000000.json").write_text(path.read_text())
    with pytest.raises(ValueError, match=r"exactly one.*run-000000000000\.json"):
        load_run(out)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError, match="nothing"):
        load_run(empty)


def test_load_run_names_a_missing_pose_key(tmp_path):
    out = make_run_dir(tmp_path)
    (path,) = out.glob("run-*.json")
    rec = json.loads(path.read_text())
    for k in ("per_pose", "units", "poses", "field", "receptor"):
        del rec["results"][k]
    path.write_text(json.dumps(rec))
    with pytest.raises(ValueError, match=r"results\.per_pose.*predates pose output.*re-run"):
        load_run(out)


def test_ddE_table_ranked(ranked):
    rows = ddE_table(ranked)
    assert [r["candidate"] for r in rows] == ["4-F", "4-Cl", "4-Br"]
    assert [r["group"] for r in rows] == [0, 1, 1]
    assert [r["survivor"] for r in rows] == [True, False, False]
    paired = ranked.record["results"]["paired_ddE"]
    for r in rows:
        p = paired[r["candidate"]]
        assert r["smiles"] == CANDIDATES[r["candidate"]]
        assert (r["mean"], r["sem"], r["n"]) == (p["mean"], p["sem"], N_POSES)
        assert r["lo"] == pytest.approx(p["mean"] - Z * p["sem"])
        assert r["hi"] == pytest.approx(p["mean"] + Z * p["sem"])
        assert r["unpaired_sem"] == ranked.record["results"]["unpaired_sem"][r["candidate"]]


def test_ddE_table_unranked(unranked):
    assert unranked.unranked and unranked.survivors is None
    rows = ddE_table(unranked)
    assert [r["survivor"] for r in rows] == [None, None, None]
    assert [r["group"] for r in rows] == [0, 1, 1]


@pytest.mark.parametrize("unranked_cut", [False, True])
def test_resolved_matrix_reproduces_groups(tmp_path, unranked_cut):
    run = load_run(make_run_dir(tmp_path, unranked=unranked_cut))
    names, m = resolved_matrix(run)
    assert names == [r["candidate"] for r in ddE_table(run)]
    group_of = {n: gi for gi, g in enumerate(run.groups) for n in g}
    for i in range(len(names)):
        assert m[i][i] is False
        for j in range(len(names)):
            assert m[i][j] == m[j][i]
    for i in range(len(names) - 1):
        # the cut's own rule: consecutive in cut order and unresolved <=> same tie group
        assert (not m[i][i + 1]) == (group_of[names[i]] == group_of[names[i + 1]])
    assert m[0][1] and not m[1][2]


def test_resolved_matrix_uses_the_funnel_predicate(ranked):
    names, m = resolved_matrix(ranked)
    paired = ranked.record["results"]["paired_ddE"]
    ms = {n: Measurement(p["mean"], p["sem"], p["n"], tuple(p["per_pose"])) for n, p in paired.items()}
    for i, a in enumerate(names):
        for j, b in enumerate(names):
            if i != j:
                assert m[i][j] == resolved(ms[a], ms[b], ranked.z, ranked.floor)
    # a floor larger than every gap resolves nothing: the matrix follows the run's floor
    ranked.record["inputs"]["floor"]["value"] = 1e6
    _, m = resolved_matrix(ranked)
    assert not any(any(row) for row in m)


def test_per_pose_table_matches_paired(ranked):
    rows = per_pose_table(ranked)
    assert len(rows) == 3 * N_POSES
    assert [r["candidate"] for r in rows[::N_POSES]] == ["4-F", "4-Cl", "4-Br"]
    paired = ranked.record["results"]["paired_ddE"]
    for r in rows:
        assert r["paired"] == pytest.approx(paired[r["candidate"]]["per_pose"][r["pose"]], abs=1e-9)
        assert r["paired"] == r["value"] - r["parent_value"]


def test_per_pose_table_unknown_quantity(ranked):
    with pytest.raises(KeyError, match=r"E_gfn2.*dE_int"):
        per_pose_table(ranked, "E_gfn2")


def test_funnel_rows_unchanged(ranked):
    assert funnel_rows(ranked) == ranked.record["results"]["funnel"]


def test_pose_sdf_reads_and_checks(ranked):
    text = pose_sdf(ranked, "4-F")
    assert text.count("$$$$") == N_POSES
    assert "4-F pose 0" in text
    assert receptor_pdb(ranked) is None
    with pytest.raises(KeyError, match="nope"):
        pose_sdf(ranked, "nope")


def test_pose_sdf_sha_mismatch(ranked):
    path = ranked.out_dir / ranked.record["results"]["poses"]["4-Cl"]["file"]
    path.write_text(path.read_text().replace("4-Cl pose 0", "4-Cl pose X"))
    with pytest.raises(ValueError, match=r"4-Cl\.sdf.*sha256"):
        pose_sdf(ranked, "4-Cl")


def test_pose_sdf_missing_file(ranked):
    path = ranked.out_dir / ranked.record["results"]["poses"]["4-Br"]["file"]
    path.unlink()
    with pytest.raises(ValueError, match=r"4-Br\.sdf"):
        pose_sdf(ranked, "4-Br")


def test_candidates_are_in_config_order(tmp_path):
    run = load_run(make_run_dir(tmp_path))
    assert list(run.candidates) == list(CANDIDATES)
    assert run.analogues == [n for n in CANDIDATES if n != run.parent]
