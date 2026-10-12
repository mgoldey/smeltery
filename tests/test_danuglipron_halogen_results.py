"""The halogen report's quoted numbers equal the committed cache and log (compared to rounding)."""

import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "danuglipron_halogen" / "results"
DOC = ROOT / "docs" / "benchmarks" / "danuglipron_halogen.md"


def test_report_cost_and_counts_equal_the_cache():
    cache = json.loads((RES / "halogen-scf-cache.json").read_text())
    text = DOC.read_text()
    cpu_s = sum(v["cpu_s"] for v in cache.values())
    assert len(cache) == 7 and "Seven SCFs" in text
    assert math.isclose(round(cpu_s / 3600, 2), 3.65, rel_tol=1e-12) and "3.65 CPU-h" in text
    assert f"{round(cpu_s):,}" in text  # 13,151
    assert math.isclose(min(v["cpu_s"] for v in cache.values()), 1182, abs_tol=0.5)
    assert math.isclose(max(v["cpu_s"] for v in cache.values()), 3485, abs_tol=0.5)


def test_report_names_the_failure_that_the_log_records():
    log = (RES / "run1.log").read_text()
    assert "pyr-C28-F pose 0: SCF did not converge" in log
    assert "pyr-C28-F pose 0: SCF did not converge" in DOC.read_text()
    assert "run record" not in log  # no run record was produced, and the report says none was
