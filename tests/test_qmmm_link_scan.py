"""The committed link-distance scan (issue #19): verdicts recompute from the stored energies."""

import json
from pathlib import Path

import numpy as np

from smeltery.tiers import MIN_LINK_CHARGE_DISTANCE_ANG

ROOT = Path(__file__).resolve().parent.parent
SCAN = json.loads((ROOT / "benchmarks/qmmm_link/scan.json").read_text())


def _diverged(e):
    e = np.array(e)
    return bool(not np.all(np.isfinite(e)) or np.any(np.diff(e) > 1e-3) or e[-1] > e[0])


def test_no_run_diverged_and_the_verdicts_recompute_from_the_energies():
    assert len(SCAN["runs"]) == 10
    for r in SCAN["runs"]:
        assert _diverged(r["energies_ha"]) == (r["verdict"] == "diverged")
        assert r["verdict"] == "converged"
        assert r["steps"] == len(r["energies_ha"]) - 1


def test_the_scan_covers_the_issue_distance_and_reaches_below_it():
    d = [r["d_start_ang"] for r in SCAN["runs"] if r["scheme"] == "keep"]
    assert min(d) < 0.443 < max(d)


def test_the_placeholder_threshold_is_unchanged_because_nothing_diverged():
    assert abs(MIN_LINK_CHARGE_DISTANCE_ANG - 0.874) < 1e-12
    assert "not reproduced" in (ROOT / "docs/benchmarks/qmmm_link_threshold.md").read_text()
