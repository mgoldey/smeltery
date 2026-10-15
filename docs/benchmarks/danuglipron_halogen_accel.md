# Halogen scan: AURORA, TRAH and single ladder rungs on the unconverged SCFs (issue #6, ferric#418)

Plans, committed before any run: `danuglipron_halogen_accel_plan.md` and `danuglipron_halogen_ladder_probe_plan.md`. Runners: `examples/danuglipron_halogen_accel.py`, `examples/danuglipron_halogen_ladder_probe.py`. Records: `benchmarks/danuglipron_halogen/results/halogen_accel_results.json`, `halogen_ladder_probe.json`.

**Answer: none of the three routes converged a single SCF within its CPU limit. The F/Cl failures are not rescued by what was tried, and the study cannot say why.**

## Result

| Route | Cases | Converged | Limit per job | Note |
|---|---|---|---|---|
| AURORA (`aurora=True`) | 6 (4 DIIS-failed, 2 DIIS-converged at 1e-6) | 0 of 6 | 5,000 CPU-s | killed at the limit; DIIS had converged `pyr-C28-F` and `pyr-C28-Cl` in 2,490 and 2,945 s |
| TRAH (`trah_trigger=1e-2`) | 2 (`pyr-C28-F`, `pyr-C28-Cl`) | 0 of 2 | 5,000 CPU-s | the other 4 cases were **not run**: the 12 CPU-h cap allowed two jobs |
| ladder rung 1 (ADIIS) | 1 (`bnz-C34-Cl`) | 0 of 1 | 2,500 CPU-s | truncated test |
| ladder rung 2 (ADIIS + shift 0.5) | 1 | 0 of 1 | 2,500 CPU-s | truncated test |
| ladder rung 3 (ADIIS + shift 1.0 + SOSCF) | 1 | 0 of 1 | 2,500 CPU-s | truncated test |

CPU spent: 11.11 CPU-h on AURORA/TRAH and 2.08 on the probe, 13.19 together. The accel study's cap was 12 CPU-h; the probe was run on top of it at the project owner's request. The scan's running total moves from 29.0 to about 42.2 CPU-h, against the 30 CPU-h of the earlier plans.

## What this does and does not show

* **Does show:** on the two cases DIIS did converge, AURORA and TRAH were slower, not faster: both were killed at 5,000 CPU-s where DIIS needed about 2,500. They are no help on these SCFs as run.
* **Does not show:** that the failures are intractable. Every job was killed by the CPU limit, so there is **no iteration count and no convergence trace**; "stuck" and "just slow" cannot be told apart. The four DIIS-failed cases never saw TRAH.
* **The ladder probe is a truncated test.** 2,500 CPU-s is roughly 50 iterations, short of rungs 2 and 3's caps (60 and 80), and Python cannot warm-start each rung from the last one's density as the real ladder does, so the three probes are single rungs from the MINAO guess. Its outcome is **inconclusive**, not a negative for the ladder. No ferric issue is filed on this evidence, as the probe plan fixed.
* **Deviation:** the dev ferric wheel's default `max_iter` is 200; the plan assumed rc7's 100. The CPU limit (about 100 iterations at roughly 50 CPU-s each) bound first, so it probably did not change the outcome, but it is not shown.
* **Build:** unreleased ferric `0.1.0.dev0`, Wheels run 38630999922 on `773081a`, in a separate environment from smeltery's rc7 pin. The poses are the frozen ones rebuilt in the project environment (all six matched the cached DIIS records).

## What would decide it

An SCF trace per iteration (not available from a killed job), or a rerun with a limit set by iterations rather than by CPU so a failure reports its own convergence history. That, or ferric exposing its ladder through `run_rhf`, is the next step; the scan stays incomplete until a decision on cost.
