# QM/MM link-to-charge distance scan (issue #19)

Plan: `qmmm_link_threshold_plan.md` (committed before the run). Data: `benchmarks/qmmm_link/scan.json`; script `scripts/qmmm_link_scan.py`.
ferric 0.1.0rc7, Gly3 fixture, AMBER ff14SB, RHF/STO-3G, QM region = N-terminal ammonium + CH2 (charge +1), one cut C-C bond, QM atoms and link H
optimised (150-step cap), MM atoms fixed. One run per row.

| scheme | link scale | start d (A) | final d (A) | steps | max step rise (mHa) | E(last) - E(first) (kcal/mol) | plan verdict |
|---|---|---|---|---|---|---|---|
| keep | 0.5000 | 0.753 | 0.904 | 77 | 0.0 | -90.0 | converged |
| keep | 0.6000 | 0.602 | 0.680 | 139 | 0.0 | -46.5 | converged |
| keep | 0.6500 | 0.527 | 0.585 | 66 | 0.013 | -38.5 | converged |
| keep | 0.7124 (default) | 0.433 | 0.478 | 62 | 0.0025 | -37.0 | converged |
| keep | 0.7800 | 0.331 | 0.374 | 62 | 0.0 | -47.1 | converged |
| keep | 0.8500 | 0.226 | 0.274 | 67 | 0.0005 | -91.4 | converged |
| keep | 0.9000 | 0.151 | 0.201 | 125 | 0.0 | -201.0 | converged |
| keep | 0.9500 | 0.075 | 0.121 | 145 | 0.0 | -663.9 | converged |
| delete-host | 0.7124 | 1.472 | 1.468 | 59 | 0.0 | -17.3 | converged |
| rcd | 0.7124 | 0.902 | 0.907 | 83 | 0.0 | -19.4 | converged |

## Result under the plan's rule

**No run diverged.** All ten converged inside 150 steps with no energy rise above 0.013 mHa. The plan says: if none diverged, the issue's
divergence is not reproduced, the constant keeps its value, and it stays an unmeasured placeholder. That is the outcome. It is the second
non-reproduction (the 40-step re-run was the first) and it reaches d = 0.075 A, far below the issue's 0.443 A.

## Observation, not a result (made after seeing the table)

The relaxation energy grows steeply as the start distance shrinks: -37 kcal/mol at 0.433 A (the issue's value), -201 at 0.151 A, -664 at 0.075 A,
and the link H moves toward the charge (final d is larger than start d in every keep run, so it is the QM density, not the H, that finds the
charge). A large relaxation energy is a symptom of over-polarization, but this was not a pre-declared criterion, one system cannot calibrate it, and no
threshold is derived from it. The 0.874 A gate stays a placeholder. If a threshold is wanted, it should come from a pre-declared criterion
(for example a relaxation-energy bound) on more than one cut.

## What this does not show

One system, one cut (C-C), one basis, one force field, fixed MM atoms, a single run per row. It does not say a close charge is harmless; it says
this optimiser does not diverge on this system. The gate remains a conservative refusal.
