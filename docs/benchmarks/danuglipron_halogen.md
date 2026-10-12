# Danuglipron halogen scan through the funnel (issue #6): report

Plan: [danuglipron_halogen_plan.md](danuglipron_halogen_plan.md), committed before the run and not edited since.

## Outcome: the run did NOT complete. Criteria 1 to 4 are NOT MEASURED.

The production run (`--poses 3 --workers 4 --cap-cpu-hours 23 --skip-model-b`, ferric 0.1.0rc7 from PyPI, `nice -n 10`) stopped with

> `RuntimeError: pyr-C28-F pose 0: SCF did not converge`

(log: `benchmarks/danuglipron_halogen/results/run1.log`). Pose 0 is the cryo-EM bound conformer, so this is the unjittered
geometry of the pyridine-site fluoro analogue. The failing SCF's CPU time was not recorded (it raised before returning).

Under the plan's section 5 rule (an SCF that fails is not retried and not given convergence aids; the analysis uses only whole poses
completed before the failing pose index; fewer than 2 poses means the run is reported as not completed), the run is **not completed**:
the failing index is 0, so zero whole poses exist. No paired ddE, SEM, ratio, rho, cut, self-anchor or run record was produced, and none
is claimed. The example's checks (self-anchor, field non-zero) were never reached.

## What was measured

Seven SCFs finished before the stop (all pose 0, AMBER field, 6458 charges), cached in `benchmarks/danuglipron_halogen/results/halogen-scf-cache.json`:
parent, `bzim-C9-F`, `bzim-C9-Cl`, `bzim-C9-CH3`, `pyr-C28-CH3`, `pyr-C28-Cl`, `bnz-C34-CH3`. They cost **3.65 CPU-h** (13,151 CPU-s;
1182 to 3485 CPU-s each; `pyr-C28-Cl` at 3485 CPU-s is twice the others, which fits a hard SCF). Pilots cost 0.86 CPU-h before.
The CPU of SCFs that were in flight at the stop (up to 4, including the failing one) is unmetered, so the total spent is
at least 4.51 CPU-h, below the 26 CPU-h cap. Seven energies on one pose give no paired statistic, so none is reported.

## Deviations

* No procedure was changed after seeing the failure. Nothing was retried.
* The example's 12-analogue set, 3 poses, declared 1.26 kcal/mol floor and ranges were unchanged.

## What this does and does not support

* Supports: ferric RHF/STO-3G in the full 7LCJ field does not converge on pose 0 of `pyr-C28-F` with default settings, on one attempt. Not
  tested: whether it is deterministic, whether other settings fix it, or whether other analogues fail.
* Does not support: any statement about the halogen scan, pairing, the campaign ranges or the cut.

## Decisions for the user

1. Drop pose 0 for all molecules (as the tyk2 study's rule did) and run the jittered poses only? That departs from the plan (pose 0 is the bound
   conformer), so it needs your approval; the 7 cached pose-0 SCFs would then be unused.
2. Or investigate the non-converging SCF in ferric (ferric issue) before spending more.
3. Remaining budget: about 21 CPU-h; a full 3-pose run needs about 18 more.
