"""The halogen report's quoted numbers equal the committed cache and logs (compared to rounding)."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "danuglipron_halogen" / "results"
DOC = (ROOT / "docs" / "benchmarks" / "danuglipron_halogen.md").read_text()


def _cache():
    return json.loads((RES / "halogen-scf-cache.json").read_text())


def test_report_counts_and_cpu_equal_the_cache():
    c = _cache()
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
    c = _cache()
    run1 = [v["cpu_s"] for v in c.values() if not v.get("error")][:7]  # the 7 pose-0 SCFs were cached first
    assert math.isclose(round(sum(run1) / 3600, 2), 3.65, rel_tol=1e-12) and "3.65" in DOC
    run2_s = sum(v["cpu_s"] for v in c.values()) - sum(run1)
    assert math.isclose(round(run2_s / 3600, 2), 5.28, rel_tol=1e-12) and "5.28" in DOC


def test_logs_record_the_failures_and_no_run_record_was_produced():
    log1, log2 = (RES / "run1.log").read_text(), (RES / "run2.log").read_text()
    assert "pyr-C28-F pose 0: SCF did not converge" in log1
    for name in ("pyr-C28-F", "pyr-C28-Cl", "bzim-C9-F"):
        assert f"{name} pose 0 FAILED" in log2 and name in DOC
    assert "run record" not in log1 + log2 and not list(RES.glob("danuglipron-halogen-*.json"))
