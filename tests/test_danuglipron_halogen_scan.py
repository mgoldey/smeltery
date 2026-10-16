"""The run 5 report's quoted numbers equal the committed run record, logs and cache (compared to rounding)."""

import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "benchmarks" / "danuglipron_halogen" / "results"
DOC = (ROOT / "docs" / "benchmarks" / "danuglipron_halogen_scan.md").read_text()
RECORD = json.loads((RES / "danuglipron-halogen-38fa833cb90c.json").read_text())["results"]


def _table_rows():
    rows = {}
    for ln in DOC.splitlines():
        m = re.match(
            r"\| `([\w-]+)` \| ([+-][\d.]+) \| ([+-][\d.]+) \| ([+-][\d.]+) \| ([\d.]+) \| ([\d.]+) \| ([\d.]+) \|$", ln
        )
        if m:
            rows[m.group(1)] = [float(x) for x in m.groups()[1:]]
    return rows


def test_the_report_table_equals_the_run_record():
    rows = _table_rows()
    amb, c3 = RECORD["paired_ddE_amber"], RECORD["criterion_3_pairing"]
    assert set(rows) == set(amb) and len(rows) == 12
    for name, (p0, p1, mean, sem, unp, ratio) in rows.items():
        r = amb[name]
        assert r["n"] == 2
        assert math.isclose(p0, round(r["per_pose"][0], 2), abs_tol=1e-9)
        assert math.isclose(p1, round(r["per_pose"][1], 2), abs_tol=1e-9)
        assert math.isclose(mean, round(r["mean"], 3), abs_tol=1e-9)
        assert math.isclose(sem, round(r["sem"], 3), abs_tol=1e-9)
        assert math.isclose(unp, round(c3[name]["unpaired_sem"], 3), abs_tol=1e-9)
        assert math.isclose(ratio, round(c3[name]["ratio"], 2), abs_tol=1e-9)
        assert r["sem"] < c3[name]["unpaired_sem"] and c3[name]["rho"] == 1.0


def test_the_headline_claims_equal_the_record_and_logs():
    assert RECORD["criterion_1_self_anchor"]["exact_zero"] and RECORD["criterion_1_self_anchor"]["per_pose"] == [
        0.0,
        0.0,
    ]
    p = RECORD["criterion_1_self_anchor"]["parent_dE_int"]
    assert f"{p[0]:.2f}" in DOC and f"{p[1]:.2f}" in DOC
    assert RECORD["cut"]["unranked"] and [len(g) for g in RECORD["cut"]["groups"]] == [6, 6]
    assert len(RECORD["cut"]["adjacent_gaps_at_or_below_floor"]) == 8 and "8 of 11 adjacent gaps" in DOC
    assert RECORD["criterion_2_campaign_comparison"]["rho_range"]["n_inside"] == 0 and "0 of 12 analogues" in DOC
    assert RECORD["criterion_2_campaign_comparison"]["ch3_paired_sem_range"]["n_inside"] == 2
    assert RECORD["criterion_4_sensitivity"].startswith("NOT RUN") and "NOT RUN" in DOC
    assert math.isclose(round(RECORD["cost"]["scf_cpu_hours"], 2), 12.14) and "12.14 CPU-h" in DOC
    swing = RECORD["paired_ddE_amber"]["bnz-C34-Cl"]["per_pose"]
    assert math.isclose(round(swing[1] - swing[0], 1), 20.1) and "20.1 kcal/mol spread" in DOC


def test_logs_and_cache_back_the_convergence_claims():
    log5 = (RES / "run5.log").read_text()
    assert "0 of 28 poses fail" in log5 and "FAILED" not in log5 and "ScfFailed" not in log5
    assert "19 to run" in log5 and "9 reused" in log5 and "max_iter 200" in log5
    assert "(8.58 CPU-h this call)" in log5 and "8.58 CPU-h" in DOC
    cache = list(json.loads((RES / "halogen-scf-cache.json").read_text()).values())
    reused = [v for v in cache if v.get("reused_from_max_iter") == "engine default"]
    assert len(reused) == 9 and not any(v.get("error") for v in reused)
    new = cache[-19:]  # the entries run 5 wrote
    assert len(new) == 19 and not any(v.get("error") for v in new) and all(v["dE"] is not None for v in new)
    geo0 = json.loads((RES / "pose_geometry_relax0.json").read_text())
    geo1 = json.loads((RES / "pose_geometry_relax1.json").read_text())
    bad0 = sum(1 for prs in geo0["problems"].values() for pr in prs if pr)
    bad1 = sum(1 for prs in geo1["problems"].values() for pr in prs if pr)
    assert (bad0, geo0["n_poses"], bad1, geo1["n_poses"]) == (23, 28, 0, 28)
    assert "23 of 28" in DOC and "0 of 28" in DOC
