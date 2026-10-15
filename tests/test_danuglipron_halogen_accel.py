"""The accelerator report's quoted numbers equal the committed records."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "danuglipron_halogen" / "results"
DOC = (ROOT / "docs" / "benchmarks" / "danuglipron_halogen_accel.md").read_text()
ACCEL = json.loads((RES / "halogen_accel_results.json").read_text())
PROBE = json.loads((RES / "halogen_ladder_probe.json").read_text())


def test_aurora_and_trah_counts_and_cpu_equal_the_record():
    rs = ACCEL["results"]
    aur = [r for r in rs if r["variant"] == "aurora"]
    trah = [r for r in rs if r["variant"] == "trah"]
    assert (len(aur), len(trah)) == (6, 2)
    assert not any(r.get("converged") for r in rs)
    assert all(r.get("killed_at_cpu_limit") and r["cpu_s"] == 5000.0 for r in rs)
    assert sorted(r["case"] for r in trah) == ["pyr-C28-Cl", "pyr-C28-F"]
    assert len(ACCEL["not_run"]) == 4 and all(v == "trah" for v, _ in ACCEL["not_run"])
    assert round(ACCEL["total_cpu_s"] / 3600, 2) == 11.11 and "11.11 CPU-h" in DOC
    assert "0 of 6" in DOC and "0 of 2" in DOC


def test_ladder_probe_equals_the_record_and_is_called_truncated():
    res = PROBE["results"]
    assert sorted(res) == ["A_rung1", "B_rung2", "C_rung3"] and PROBE["case"] == "bnz-C34-Cl"
    assert all(r.get("killed_at_cpu_limit") and r["cpu_s"] == 2500.0 for r in res.values())
    assert round(sum(r["cpu_s"] for r in res.values()) / 3600, 2) == 2.08 and "2.08" in DOC
    assert "13.19 together" in DOC and "inconclusive" in DOC and "truncated" in DOC


def test_the_plans_precede_the_run_and_state_the_caps():
    plan = (ROOT / "docs" / "benchmarks" / "danuglipron_halogen_accel_plan.md").read_text()
    assert "12 CPU-h" in plan and "5,000 CPU-s" in plan
    assert ACCEL["settings"]["study_cap_cpu_s"] == 12 * 3600 and ACCEL["settings"]["job_cpu_limit_s"] == 5000
