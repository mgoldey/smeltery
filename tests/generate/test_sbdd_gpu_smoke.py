"""One real run through the external DiffSBDD environment. Skipped unless the four variables below are set."""

import os

import pytest

from smeltery.generate import ScaffoldGrowth

REQUIRED = ("SMELTERY_SBDD_PYTHON", "SMELTERY_SBDD_DIR", "SMELTERY_SBDD_CKPT", "SMELTERY_SBDD_POCKET")

pytestmark = [
    pytest.mark.gpu,
    pytest.mark.skipif(
        any(k not in os.environ for k in REQUIRED),
        reason="set " + ", ".join(REQUIRED) + " to run the real-model smoke test",
    ),
]


def test_a_real_run_completes_and_reports(parent_candidate, scaffold_indices):
    gen = ScaffoldGrowth(
        python=os.environ["SMELTERY_SBDD_PYTHON"],
        diffsbdd_dir=os.environ["SMELTERY_SBDD_DIR"],
        checkpoint=os.environ["SMELTERY_SBDD_CKPT"],
        n_samples=4,
        add_n_nodes=9,
        device=os.environ.get("SMELTERY_SBDD_DEVICE"),
        pose_source="model",
    )
    result = gen.generate(parent_candidate, scaffold_indices, os.environ["SMELTERY_SBDD_POCKET"], seed=1)
    run = result.provenance["runs"][0]
    print(
        f"\nSMOKE n_raw={result.n_raw} accepted={len(result.candidates)} rejected={result.rejection_counts()} "
        f"wall={run['wall_time_s']}s device={run['device']} free_mib={run['cuda_free_mib']}"
    )
    # A run that accepts nothing is a result: the measured yield on this scaffold is about 1 in 16.
    assert result.n_raw == 4
    assert len(run["checkpoint_sha256"]) == 64 and run["diffsbdd_commit"]
    assert run["diffsbdd_dirty"] is False, (
        "the DiffSBDD checkout has local changes; the runner patches at runtime instead"
    )
