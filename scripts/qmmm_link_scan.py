"""Link-H-to-charge distance scan for the Qmmm tier (issue #19); plan: docs/benchmarks/qmmm_link_threshold_plan.md."""

import json
import math
import sys
from pathlib import Path

import ferric
import numpy as np

from smeltery.model import Pose
from smeltery.tiers import CC_LINK_SCALE, MmParameters, Qmmm

ROOT = Path(__file__).resolve().parent.parent
PARAMS = MmParameters.from_json(ROOT / "tests/data/gly3_ff14sb.json")
ATOMS = json.loads((ROOT / "tests/data/gly3_ff14sb.json").read_text())["atoms"]
NAMES = [f"{a['name']}{a['resid']}" for a in ATOMS]
QM_A = tuple(NAMES.index(n) for n in ("N1", "H1", "H21", "H31", "CA1", "HA21", "HA31"))
SCALES = [0.50, 0.60, 0.65, CC_LINK_SCALE, 0.78, 0.85, 0.90, 0.95]
MAX_STEPS = 150
RISE_MHA = 1.0


def judge(energies: list[float], converged: bool) -> str:
    e = np.array(energies, dtype=float)
    if not np.all(np.isfinite(e)) or np.any(np.diff(e) > RISE_MHA * 1e-3) or e[-1] > e[0]:
        return "diverged"
    return "converged" if converged else "did_not_converge"


def run_one(scheme: str, scale: float) -> dict:
    tier = Qmmm(PARAMS, QM_A, 1, boundary_scheme=scheme, link_scale=scale, min_link_charge_distance_ang=0.0)
    xyz = np.array([a["xyz"] for a in ATOMS], dtype=float)
    sysm = tier._system(ferric, Pose(PARAMS.symbols, xyz), f"{scheme} s={scale:.4f}")
    d0 = sysm.min_link_to_charge_distance()
    res = ferric.run_optimize_qmmm(
        sysm, "sto-3g", move_mm="none", mm_topology=PARAMS.topology(ferric), max_steps=MAX_STEPS
    )
    e = [float(x) for x in res.energies()]
    row = {
        "scheme": scheme,
        "link_scale": scale,
        "d_start_ang": d0,
        "d_final_ang": res.system().min_link_to_charge_distance(),
        "steps": int(res.steps),
        "reported_converged": bool(res.converged),
        "energy_first_ha": e[0],
        "energy_last_ha": e[-1],
        "max_step_rise_mha": max([0.0] + [(b - a) * 1e3 for a, b in zip(e, e[1:])]),
        "energies_ha": e,
    }
    row["verdict"] = judge(e, row["reported_converged"])
    print(f"{scheme:11s} s={scale:.4f} d0={d0:.3f} steps={row['steps']} {row['verdict']}", flush=True)
    return row


def main(out: Path) -> None:
    runs = [run_one("keep", s) for s in SCALES]
    runs += [run_one(sch, CC_LINK_SCALE) for sch in ("delete-host", "rcd")]
    out.write_text(json.dumps({"ferric": ferric.__version__, "max_steps": MAX_STEPS, "runs": runs}, indent=1))
    assert all(math.isfinite(r["energy_last_ha"]) or r["verdict"] == "diverged" for r in runs)


if __name__ == "__main__":
    main(Path(sys.argv[1]))
