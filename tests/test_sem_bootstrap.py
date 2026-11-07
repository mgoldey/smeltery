"""The paired SEM is the standard error of the mean paired difference, and pairing beats not pairing."""

import numpy as np

from smeltery.funnel import paired_delta, unpaired_delta
from smeltery.model import Candidate


def test_paired_sem_matches_bootstrap_and_beats_unpaired():
    rng = np.random.default_rng(7)
    n = 40
    shared = rng.normal(0.0, 3.0, n)  # pose effect common to parent and analogue
    parent = -10.0 + shared + rng.normal(0.0, 0.5, n)
    analogue = -12.0 + shared + rng.normal(0.0, 0.5, n)

    def cand(name, values):
        return Candidate(name=name, smiles="C", per_pose={"dE_int": list(values)})

    paired = paired_delta(cand("p", parent), cand("a", analogue), "dE_int")
    unpaired = unpaired_delta(cand("p", parent), cand("a", analogue), "dE_int")

    d = analogue - parent
    boot = [d[rng.integers(0, n, n)].mean() for _ in range(4000)]
    # 15% checks the formula's form (s/sqrt(n) on the paired differences), not ddof.
    assert abs(paired.sem / np.std(boot, ddof=1) - 1.0) < 0.15
    assert paired.sem < 0.5 * unpaired.sem
