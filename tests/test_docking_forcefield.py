"""dock -> force field -> quantum: the quantum tier must score the DOCKED pose (issue #18).

The defect this guards (ferric PR #325): a force-field stage that relaxes poses in
place, so the quantum tier scores a geometry docking never proposed. Real Vina on the
committed pentapeptide fixture, then `ForceField`, then ferric RHF. CI's `docking`
job runs it and fails if it skips for a missing module.
"""

from __future__ import annotations

import copy
import importlib.util
import pathlib
import shutil

import numpy as np
import pytest

_MISSING = [m for m in ("vina", "meeko", "rdkit") if importlib.util.find_spec(m) is None]
pytestmark = [
    pytest.mark.needs_ferric,
    pytest.mark.skipif(
        bool(_MISSING),
        reason=f"needs the docking extra: pip install 'smeltery[docking]' ({', '.join(_MISSING)} missing)",
    ),
]

DATA = pathlib.Path(__file__).parent / "data"


def _openmm_stack_missing() -> str:
    missing = []
    for m in ("openmm", "openff.toolkit", "openmmforcefields"):
        try:
            if importlib.util.find_spec(m) is None:
                missing.append(m)
        except (ImportError, ValueError):
            missing.append(m)
    if not missing and shutil.which("sqm") is None:
        missing.append("sqm (AmberTools)")
    return ", ".join(missing)


_OPENMM_MISSING = _openmm_stack_missing()


@pytest.mark.parametrize(
    "path",
    [
        "mmff",
        pytest.param(
            "openmm",
            marks=pytest.mark.skipif(
                bool(_OPENMM_MISSING),
                reason=(
                    f"needs the OpenMM/OpenFF environment ({_OPENMM_MISSING} missing): "
                    "`micromamba create -n smeltery-ff -f environment-openff.yml` (docs/environments.md)"
                ),
            ),
        ),
    ],
)
def test_quantum_tier_scores_the_docked_pose_not_a_force_field_relaxed_one(path):
    from smeltery import Candidate, PointCharge
    from smeltery.docking import Box, Docking, VinaProvider
    from smeltery.tiers import FieldInteraction, ForceField

    box = Box((0.4, -0.1, -0.1), (22.0, 16.0, 16.0))
    cand = Candidate("ethanol", "CCO")
    Docking(VinaProvider(n_poses=2)).run([cand], {"receptor": DATA / "pocket_pep5.pdbqt", "box": box})
    assert cand.poses, "docking produced no poses"

    field = [PointCharge(0.4, (3.0, 0.0, 0.0)), PointCharge(-0.4, (-3.0, 1.0, 0.0))]
    ctx = {"field": field}
    docked = copy.deepcopy(cand)

    ForceField(path=path).run([cand], ctx)
    for before, after in zip(docked.poses, cand.poses, strict=True):
        assert np.array_equal(before.coords_ang, after.coords_ang)  # the FF stage did not move the pose

    after_ff, reference = FieldInteraction(), FieldInteraction()
    after_ff.run([cand], ctx)
    reference.run([docked], ctx)
    assert cand.per_pose["dE_int"] == docked.per_pose["dE_int"]  # bit-identical: same geometry reached the SCF
