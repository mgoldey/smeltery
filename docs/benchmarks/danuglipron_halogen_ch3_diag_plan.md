# Plan: why do `pyr-C28-CH3` and `bzim-C9-CH3` pose 0 not converge on rebuilt poses?

Written before any SCF of this diagnosis is run. Parent plan and amendments: `docs/benchmarks/danuglipron_halogen_plan.md` (A3 is the rebuilt-pose scan).

## What is known (run 4, `benchmarks/danuglipron_halogen/results/run4.log`)

Rebuilt poses (`relax_unmapped=True`) passed the geometry gate (28 of 28). 8 of 10 pose-0 SCFs converged at the tier default 1e-10 in 1,183 to 1,642 CPU-s. `pyr-C28-CH3` and `bzim-C9-CH3` pose 0 did not
converge before the 2,500 CPU-s per-job limit (2,109 and 2,129 CPU-s). Under A3 a failed SCF is final: this plan does not change that status and no diagnostic energy replaces a failed result.
`bnz-C34-CH3` pose 0 converged (1,399 CPU-s). Pose 1 has not been run for any analogue.

## Questions, in the order they are asked (each stage runs only if the previous one leaves the question open)

1. **Geometry detail (no SCF).** Rebuild the poses of `parent`, `pyr-C28-CH3`, `bzim-C9-CH3`, `bnz-C34-CH3` with the run-4 settings and check the rebuilt pose-0 coordinates reproduce run 4's cache keys
   (so these are the poses that failed). Report for each: longest bond / covalent-radii sum, shortest non-bonded heavy-atom distance, shortest ligand-to-point-charge distance, number of ligand atoms
   within 2.0 Å of a charge. The ligand-to-charge distance is the check the geometry gate does not make.
2. **Vacuum SCF of the whole molecule** (STO-3G RHF, `energy_conv` 1e-10, `density_conv` 1e-8, `max_iter` 200, 1,500 CPU-s limit each) for the two failures and the converged control `bnz-C34-CH3`.
   - Fails in vacuum too: the difficulty is in the molecule at this geometry, not the field.
   - Converges in vacuum for the failures: the field is involved; go to 3.
3. **Field with the N charges nearest the ligand** (N = 50, 1000; the 6,458-charge case is run 4's failure) for the two failures, same settings and limit. Reports where, between 0 and 6,458 charges, convergence is lost.
4. **Control for stage 2/3:** the converged `bnz-C34-CH3` is run at the same settings so a failure is not an artefact of the diagnostic harness. Its in-field energy must equal run 4's cached dE_int to 1e-6 kcal/mol (tolerance, not `==`).

## Not done here

No rung of a convergence ladder, AURORA, TRAH or looser threshold is run to rescue the two failures: that would turn a recorded failure into a different measurement. If the cause is intrinsic, a
single ladder probe on one failure is a separate, later plan. No ddE is computed from any diagnostic.

## Budget

At most 3 CPU-h in total; each SCF is limited to 1,500 CPU-s by `RLIMIT_CPU`; 3 workers. A limit hit is recorded as "not converged within the limit" and is not retried.

## Outcome language

A diagnosis is stated as what was measured (for example "converges in vacuum in k iterations, fails in the 6,458-charge field"), not as a cause beyond that. It is not evidence about ferric unless a
stage isolates ferric from smeltery's geometry, which none of these does.

## Addendum A (written after stage 2/3 results for `pyr-C28-CH3` at 0/50/1,000 charges and `bzim-C9-CH3` at 1,000, before any 6,458-charge SCF of a failure)

**Seen:** both failing molecules converge with up to 1,000 charges in 40 to 60 iterations, 588 to 720 CPU-s; run 4's failure was at 6,458 charges, which the plan above had only for the control.

**Added (project owner's instruction):** `pyr-C28-CH3` and `bzim-C9-CH3` pose 0 are run with all 6,458 charges, same settings as the other stage-3 SCFs (`energy_conv` 1e-10, `density_conv` 1e-8,
`max_iter` 200, 1,500 CPU-s limit, STO-3G RHF, charge 0). Two SCFs, at most 2 x 1,500 CPU-s = 0.83 CPU-h; this takes the diagnosis to at most about 4 CPU-h against the 3 CPU-h written above, a disclosed
overrun (the first attempt's SCFs were lost to a recording bug, `scf_attempt1_crashed.log`). Interpretation rules, fixed now: converges within 200 iterations and 1,500 CPU-s: run 4's failure is not reproduced
at these settings, and it is reported as "the failure needs the 2,500 CPU-s limit or a different setting to explain" and not as a cause; does not converge: the failure reproduces in this harness without the
scan runner, and it is reported as such. Neither outcome changes the A3 status of the two SCFs (failed, final).
