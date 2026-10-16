"""The halogen report's quoted numbers equal the committed cache and logs (compared to rounding)."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "danuglipron_halogen" / "results"
DOC = (ROOT / "docs" / "benchmarks" / "danuglipron_halogen.md").read_text()


def _cache():
    return json.loads((RES / "halogen-scf-cache.json").read_text())


def _first_14():
    return dict(list(_cache().items())[:14])  # the entries written by runs 1 and 2, in insertion order


def test_report_counts_and_cpu_equal_the_cache():
    c = _first_14()
    ok = [v for v in c.values() if not v.get("error")]
    bad = [v for v in c.values() if v.get("error")]
    assert (
        (len(c), len(ok), len(bad)) == (14, 11, 3)
        and "14 entries: 11 converged" in DOC
        and "3 recorded failures" in DOC
    )
    ok_s, bad_s = sum(v["cpu_s"] for v in ok), sum(v["cpu_s"] for v in bad)
    assert math.isclose(round(ok_s), 22099, rel_tol=1e-12) and "22,099 CPU-s" in DOC
    assert math.isclose(round(bad_s), 10066, rel_tol=1e-12) and "10,066 CPU-s" in DOC
    assert sorted(round(v["cpu_s"]) for v in bad) == [2250, 2665, 5151]


def test_report_run2_cost_equals_cache_minus_run1():
    c = _first_14()
    run1 = [v["cpu_s"] for v in c.values() if not v.get("error")][:7]  # the 7 pose-0 SCFs were cached first
    assert math.isclose(round(sum(run1) / 3600, 2), 3.65, rel_tol=1e-12) and "3.65" in DOC
    run2_s = sum(v["cpu_s"] for v in c.values()) - sum(run1)
    assert math.isclose(round(run2_s / 3600, 2), 5.28, rel_tol=1e-12) and "5.28" in DOC


def test_logs_record_the_failures_and_no_run_record_was_produced():
    log1, log2 = (RES / "run1.log").read_text(), (RES / "run2.log").read_text()
    assert "pyr-C28-F pose 0: SCF did not converge" in log1
    for name in ("pyr-C28-F", "pyr-C28-Cl", "bzim-C9-F"):
        assert f"{name} pose 0 FAILED" in log2 and name in DOC
    assert "run record" not in log1 + log2  # runs 1 and 2 wrote none; the one record on disk is run 5's


def test_run3_entries_equal_the_report():
    c = list(_cache().values())[:25]  # entries written by runs 1 to 3, in insertion order
    assert len(c) == 25 and len(c) - 14 == 11
    reused, new = c[14:18], c[18:]
    assert all(v.get("reused_from_energy_conv") == 1e-10 and not v.get("error") for v in reused)
    ok_new = [v for v in new if not v.get("error")]
    bad_new = [v for v in new if v.get("error")]
    assert (len(ok_new), len(bad_new)) == (3, 4)
    assert sorted(round(v["cpu_s"]) for v in ok_new) == [1850, 2490, 2945]
    assert sorted(round(v["cpu_s"]) for v in bad_new) == [4955, 6339, 6547, 7554]
    assert [v["error"].split(" pose")[0] for v in bad_new] and {v["error"].split(" pose")[0] for v in bad_new} == {
        "bzim-C9-F",
        "bnz-C34-Cl",
        "bnz-C34-F",
        "bnz-C36-F",
    }
    log3 = (RES / "run3.log").read_text()
    assert "4 reused from energy_conv 1e-10" in log3 and "ScfFailed" in log3
    assert "9.08 CPU-h this call" in log3 and "9.08" in DOC and "does not by itself make" in DOC
    assert all(v["dE"] is None for v in bad_new)
    assert not any(v.get("dE") is not None and v["dE"] > 100 for v in ok_new)  # the +106.4 value is a REUSED entry
    assert any(round(v["dE"], 1) == 106.4 for v in reused) and "106.4" in DOC
