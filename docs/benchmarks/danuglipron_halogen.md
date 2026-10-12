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
The CPU of SCFs that were in flight at the stop (the failing one and up to 3 others, discarded because the pool raised before
caching them) is unmetered. **It is not small.** The run log was created at 04:05 and the traceback at 07:29 (3.41 h wall, 4 workers);
the last cached SCF was written at 06:09, so for the final 80 minutes all four workers were inside SCFs that never returned a cached
result. The upper bound on CPU is 4 x 3.41 = 13.7 CPU-h for the run (of which 3.65 is cached), so up to about 10.0 CPU-h is unmetered, and
**the total spent, pilots included, is up to about 14.6 CPU-h** (not the 4.5 CPU-h first assumed). It is probably close to that bound,
since the workers were busy throughout. Seven energies on one pose give no paired statistic, so none is reported.

## Amendment A1 considered and NOT run (cost accounting)

The proposal was to drop pose 0 for every molecule and run jittered poses 1..N, N the largest of {3, 2} that fits the 26 CPU-h cap.
With the spend bounded at 14.6 CPU-h, 11.4 CPU-h remain. One pose index of the 14 molecules costs about 7 CPU-h or more (the seven cached
SCFs averaged 1879 s, against the 1500 s assumed in the plan, and the pyridine-site SCFs appear to be the expensive ones): N = 2 needs about
14 CPU-h and N = 3 about 21, neither fits, and N = 1 gives no SEM and so no criterion 2 to 4. **No new SCF was started.** Spending past the
cap, or on a pose count that cannot answer the criteria, is not done without an explicit decision.

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
3. Remaining budget is about 11.4 CPU-h of the 26 (not 21): enough for neither 2 nor 3 jittered poses. Raise the cap to about 30 CPU-h for
   2 jittered poses (amendment A1: pose 0 dropped for every molecule before any ddE exists, cached pose-0 SCFs excluded from all statistics),
   or about 38 for 3.
4. Fix the scheduler so a failure does not discard the in-flight SCFs' results (cache them before raising); that would have saved the unmetered work.
