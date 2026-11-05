"""The importer registry and the 2026-10-16 danuglipron importer: honest, byte-preserving, no invented numbers."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from smeltery.bench import importers
from smeltery.bench.verdict import verdict_of
from smeltery.gui.results import load_run

ROOT = Path(__file__).resolve().parent.parent
ORIGINAL = ROOT / "benchmarks" / "danuglipron_halogen" / "results" / "danuglipron-halogen-38fa833cb90c.json"
IMPORTED = ROOT / "src" / "smeltery" / "prebuilt" / "danuglipron-halogen" / "imported"

pytestmark = pytest.mark.skipif(not ORIGINAL.exists(), reason="the 2026-10-16 record is not in this checkout")


@pytest.fixture(scope="module")
def imported(tmp_path_factory):
    out = tmp_path_factory.mktemp("imported")
    return importers.import_run(ORIGINAL, out), out


def test_the_imported_run_is_unranked_as_recorded(imported):
    path, _ = imported
    record = json.loads(path.read_text())
    v = verdict_of(record)
    assert (v.state, v.winner, v.floor) == ("UNRANKED", None, 1.26)
    assert path.name == "run-38fa833cb90c.json"


def test_paired_means_equal_the_originals(imported):
    path, _ = imported
    original = json.loads(ORIGINAL.read_text())["results"]["paired_ddE_amber"]
    got = json.loads(path.read_text())["results"]["paired_ddE"]
    assert got.keys() == original.keys() and len(got) == 12
    for name, m in original.items():
        assert got[name]["mean"] == m["mean"]
        assert got[name]["sem"] == m["sem"]
        assert got[name]["per_pose"] == m["per_pose"]


def test_unpaired_sem_and_cut_groups_equal_the_originals(imported):
    path, _ = imported
    original = json.loads(ORIGINAL.read_text())["results"]
    got = json.loads(path.read_text())["results"]
    assert got["unpaired_sem"] == {k: v["unpaired_sem"] for k, v in original["criterion_3_pairing"].items()}
    assert got["cut"]["groups"] == original["cut"]["groups"]
    assert got["cut"]["survivors"] is None and got["cut"]["unranked"] is True
    assert set(got["imported"]["derived"]) == {"inputs.cut", "results.funnel"}


def test_the_original_is_byte_identical_after_import(imported):
    _, out = imported
    before = ORIGINAL.read_bytes()
    assert (out / "original" / ORIGINAL.name).read_bytes() == before
    assert ORIGINAL.read_bytes() == before


def test_the_committed_import_is_the_importers_output(imported):
    path, _ = imported
    assert (IMPORTED / path.name).read_text() == path.read_text()
    assert (IMPORTED / "original" / ORIGINAL.name).read_bytes() == ORIGINAL.read_bytes()


def test_missing_keys_are_listed_not_invented(imported):
    path, _ = imported
    results = json.loads(path.read_text())["results"]
    missing = {"per_pose", "units", "poses", "field", "receptor", "stages", "calcs", "config"}
    assert missing <= set(results["not_recorded"])
    assert not missing & set(results)
    assert not set(results["not_recorded"]) & set(results)
    meta = results["imported"]
    assert meta["importer"] == "paired_amber_2026_10"
    assert meta["original"] == f"original/{ORIGINAL.name}"
    import hashlib

    assert meta["sha256"] == hashlib.sha256(ORIGINAL.read_bytes()).hexdigest()


def test_bench_reads_the_import_without_pose_output(imported):
    _, out = imported
    run = load_run(out)
    assert run.parent == "parent" and len(run.analogues) == 12
    assert run.unranked and run.floor == 1.26
    assert not run.has_pose_output


def test_an_unknown_shape_has_no_importer(tmp_path):
    assert importers.find({"campaign": "x", "inputs": {}, "results": {}}) is None
    assert importers.find({"results": {"paired_ddE": {}}}) is None
    bad = tmp_path / "r.json"
    bad.write_text("{}")
    with pytest.raises(ValueError, match="no importer"):
        importers.import_run(bad, tmp_path / "out")


def test_the_original_is_recognised():
    assert importers.find(json.loads(ORIGINAL.read_text())).name == "paired_amber_2026_10"


def test_a_missing_pose_key_says_the_run_was_imported(imported):
    from smeltery.gui.results import pose_output

    _, out = imported
    run = load_run(out)
    msg = r"imported from paired_amber_2026_10.*results\.per_pose was not recorded"
    with pytest.raises(ValueError, match=msg) as e:
        pose_output(run, "per_pose")
    assert "predates" not in str(e.value) and "re-run" not in str(e.value)
    run.record["results"].pop("imported")
    with pytest.raises(ValueError, match="predates pose output"):
        pose_output(run, "per_pose")
