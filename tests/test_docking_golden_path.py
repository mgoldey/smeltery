"""The docking leg of the golden path (issue #33): the `docking` tier is reached by the real CLI.

The `test` job has no docking extra and no pdb2pqr30, so tests/test_golden_path_smoke.py can only DELEGATE the
docking tier here; CI's `docking` job installs both and fails if anything below skips for a missing module.

Same tiny discipline as the main smoke test: ethanol is docked into the committed pentapeptide pocket (Vina,
exhaustiveness 4, 3 poses: test_docking_cli.py's settings, which pass PoseBusters; exhaustiveness 2 with 2 poses
did NOT, propanol pose 0 clashed with the protein), propanol and butanol are paired to its docked poses, then
PoseBusters + MMFF94 and the field-interaction SCFs in STO-3G. The asserted outcome is the honest one (UNRANKED,
tie group, funnel counts), not a ranking. G5 is exercised on the REAL rank-only docking tier: Vina's score is not
a free energy, so `paired_delta(..., tier=Docking(...))` must refuse it.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from golden_path import Ctx, config, gates_block, xtb_available

_MISSING = [m for m in ("vina", "meeko", "rdkit") if importlib.util.find_spec(m) is None]
pytestmark = [
    pytest.mark.skipif(
        bool(_MISSING),
        reason=f"needs the docking extra: pip install 'smeltery[docking]' ({', '.join(_MISSING)} missing)",
    ),
    pytest.mark.skipif(shutil.which("pdb2pqr30") is None, reason="pdb2pqr30 not installed (pip install pdb2pqr)"),
    pytest.mark.skipif(
        importlib.util.find_spec("posebusters") is None,
        reason="needs the posebusters extra: pip install 'smeltery[posebusters]'",
    ),
]

DATA = Path(__file__).parent / "data"

DOCKING = f"""[docking]
receptor = "{DATA / "pocket_pep5.pdbqt"}"
box_center = [0.4, -0.1, -0.1]
box_size = [22.0, 16.0, 16.0]
seeds = [7]
exhaustiveness = 4
n_poses = 3
"""
POCKET = f'[pocket]\nfile = "{DATA / "pocket_pep5.pdb"}"\n'


@pytest.fixture(scope="module")
def docked_run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("docking-golden")
    ctx = Ctx(tmp, None)
    text = config(docking=DOCKING, pocket=POCKET, gates=gates_block())
    out = tmp / "out"
    trace = tmp / "trace.json"
    r = ctx.cli(text, "--out", out, trace=trace)
    assert r.returncode == 0, f"{r.stdout[-1500:]}\n{r.stderr[-1500:]}"
    (path,) = out.glob("run-*.json")
    return r, json.loads(path.read_text()), json.loads(trace.read_text())


@pytest.mark.needs_ferric
def test_docking_tier_is_reached(docked_run):
    _, rec, trace = docked_run
    assert "docking" in trace["completed"] and "docking" not in trace["failed"]
    assert {"docking", "paired_poses", "forcefield", "field_interaction"} <= set(trace["completed"])
    assert [t["name"] for t in rec["tiers"]][0] == "docking"


@pytest.mark.needs_ferric
def test_the_docked_golden_path_reports_the_honest_outcome(docked_run):
    r, rec, trace = docked_run
    assert "UNRANKED" in r.stdout
    cut = rec["results"]["cut"]
    assert cut["unranked"] is True and cut["survivors"] is None
    assert [sorted(g) for g in cut["groups"]] == [["butanol", "propanol"]]
    stages = [(e["stage"], e["kind"], e["candidates_in"], e["candidates_out"]) for e in rec["results"]["funnel"]]
    assert stages[0] == ("docking", "search", 1, 1)
    assert ("posebusters", "gate", 3, 3) in stages and ("forcefield", "recorded", 3, 3) in stages
    assert stages[-1] == ("cut", "rank", 2, None)
    assert ("xtb", "recorded", 3, 3) in stages or not xtb_available()
    assert set(trace["gates_applied"]) == {"G1", "G2", "G3", "G4"}  # docking adds no gate the CLI applies


def test_a_rank_only_docking_score_cannot_be_differenced():
    """G5 on the real tier: Docking declares is_delta_g=False, so paired_delta(tier=Docking) refuses."""
    from golden_path import _poses

    from smeltery import Candidate, IncomparableError, paired_delta
    from smeltery.docking import Docking, VinaProvider

    tier = Docking(VinaProvider())
    assert tier.is_delta_g is False
    parent, a = Candidate("p", "C", _poses()), Candidate("a", "C", _poses())
    for c in (parent, a):
        c.per_pose[tier.QUANTITY] = [-5.0, -6.0]
    with pytest.raises(IncomparableError, match="not a free energy"):
        paired_delta(parent, a, tier.QUANTITY, tier)
