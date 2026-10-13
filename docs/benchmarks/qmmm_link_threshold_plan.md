# Plan: measure the QM/MM link-to-charge distance threshold (issue #19)

Status: **PLAN, committed before any scan run.** `MIN_LINK_CHARGE_DISTANCE_ANG` (0.874 A) is the midpoint of two numbers from the
issue text (0.443 A diverged, 1.305 A converged) that a 40-step re-run did not reproduce. This study measures the boundary instead.

## What has been seen

* The earlier 40-step re-run (comment at `MIN_LINK_CHARGE_DISTANCE_ANG`): keep at 0.433 A, rcd at 0.90 A, delete-host at 1.47 A all descended with
  no step increase and none converged in 40 steps. No other optimisation has been run here. ferric 0.1.0rc7 (PyPI).

## Run

* System: the Gly3 fixture (`tests/data/gly3_ff14sb.json`, AMBER ff14SB), QM region `QM_A` of `tests/test_qmmm.py` (charge +1, cut C-C bond CA1-C1), STO-3G RHF,
  `ferric.run_optimize_qmmm(..., move_mm="none", max_steps=150)` with the full `MmTopology`. Only QM atoms and the link H move.
* Scheme `keep` (the host charge stays, so the link-H-to-charge distance d is set by the link scale: d = |CA1-C1| (1 - s)).
  Link scales s = 0.50, 0.60, 0.65, 0.7124 (the default 1.09/1.53), 0.78, 0.85, 0.90, 0.95, one run each.
* Controls: `delete-host` and `rcd` at s = 0.7124.
* Recorded per run: d at the start, converged, steps, energy trajectory, final d.

## Judging (fixed now)

A run **diverged** if any of: non-finite energy; any single step raising the energy by more than 1.0 mHa; or the final energy above the starting energy.
A run **did not converge** if it neither diverged nor reported `converged` in 150 steps.

* If at least one keep run diverged: the threshold is the smallest starting d such that no run with d at or above it diverged, rounded UP to the next 0.05 A,
  and the tier constant is set to it with this study as its source.
* If none diverged: the issue's divergence is **not reproduced** (twice), the threshold stays an unmeasured placeholder, the constant keeps its value, and
  the docs say so. Nothing is chosen from a convergence count.
* The scan is one system, one basis, one force field. It bounds nothing for other cuts (C-N, other residues) and is reported as that.

## Not done

No second system, no other basis, no DFT, no dynamics. No change to the tier's refusal logic beyond the constant.
