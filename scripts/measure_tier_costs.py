"""Record wall-clock cost of the tiers that are cheap enough to time in seconds.

Writes docs/tiers/measurements.json, which the tier pages quote and
tests/test_tier_docs.py checks them against. Not measured here, and so UNMEASURED on
their pages: Docking (needs a prepared receptor), Rescoring (provider-dependent),
SurfaceEsp (needs a ferric with `esp_on_surface`, which the PyPI wheel lacks), and
FieldInteraction (its measurement is src/smeltery/data/cost_measurements.json,
`scripts/measure_cost.py`). ForceField's timing is docs/environments.md.

    RAYON_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 uv run python scripts/measure_tier_costs.py

Single process, one thread, wall time via perf_counter, median of REPEATS. The load
average is recorded because these are wall times on a shared box.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import time
from pathlib import Path

import rdkit

from smeltery import Candidate, PointCharge
from smeltery.tiers import Gfn2, PairedPoses, xtb_version

REPEATS = 5
PARENT = ("benzoic acid", "OC(=O)c1ccccc1")
ANALOGUE = ("4-fluorobenzoic acid", "OC(=O)c1ccc(F)cc1")
FIELD = [PointCharge(1.0, (3.0, 0.0, 0.0)), PointCharge(-0.5, (0.0, 3.5, 0.0))]


def _median(fn) -> tuple[float, list[float]]:
    secs = []
    for _ in range(REPEATS):
        t = time.perf_counter()
        fn()
        secs.append(time.perf_counter() - t)
    return statistics.median(secs), secs


def main() -> None:
    out = {
        "machine": f"{platform.machine()}, python {platform.python_version()}, rdkit {rdkit.__version__}, one thread",
        "load_average_1m_at_start": os.getloadavg()[0],
        "repeats": REPEATS,
        "statistic": "median",
        "tiers": {},
    }

    def pair() -> None:
        parent, analogue = Candidate(*PARENT), Candidate(*ANALOGUE)
        PairedPoses(n_poses=6).run([parent, analogue], {"parent": parent})

    med, secs = _median(pair)
    out["tiers"]["paired_poses"] = {
        "seconds": med,
        "all_repeats": secs,
        "unit_of_work": "PairedPoses(n_poses=6).run: parent ensemble of 6 poses plus 6 paired analogue poses",
        "system": f"{PARENT[0]} (15 atoms) paired with {ANALOGUE[0]} (15 atoms)",
        "command": "PairedPoses(n_poses=6).run([parent, analogue], {'parent': parent})",
    }

    cand = Candidate(*PARENT)
    PairedPoses(n_poses=1).run([cand], {"parent": cand})
    gfn2 = Gfn2()
    gfn2_out: dict = {}
    for label, ctx in (("vacuum", {"field": []}), ("two_point_charges", {"field": FIELD})):
        med, secs = _median(lambda ctx=ctx: gfn2.run([cand], ctx))
        gfn2_out[label] = {"seconds": med, "all_repeats": secs}
    gfn2_out.update(
        {
            "unit_of_work": "Gfn2().run on one pose: one xtb GFN2 single point in a subprocess",
            "system": f"{PARENT[0]}, 15 atoms, 1 pose",
            "xtb_version": xtb_version(),
            "command": "Gfn2().run([cand], {'field': FIELD})",
        }
    )
    out["tiers"]["gfn2"] = gfn2_out
    path = Path(__file__).resolve().parents[1] / "docs" / "tiers" / "measurements.json"
    path.write_text(json.dumps(out, indent=2) + "\n")
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
