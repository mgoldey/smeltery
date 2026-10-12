# Danuglipron halogen scan through the funnel (issue #6): report

Plan: [danuglipron_halogen_plan.md](danuglipron_halogen_plan.md), including amendment A1 (committed before the second run).

## Outcome: the scan did NOT complete, twice. Criteria 1 to 4 are NOT MEASURED. No paired ddE exists.

ferric 0.1.0rc7 (PyPI), RHF/STO-3G, 7LCJ AMBER field of 6458 charges, `FieldInteraction` through the example. **Four SCFs did not
converge** with default settings (one attempt each; whether they are deterministic is not tested):

| run | pose (geometry) | molecule | outcome | CPU of the SCF |
|---|---|---|---|---|
| 1 | original pose 0 (cryo-EM bound conformer) | `pyr-C28-F` | SCF did not converge | not metered (lost with the pool) |
| 2 (A1) | original pose 1 (jittered) | `pyr-C28-F` | SCF did not converge | 2250 s |
| 2 (A1) | original pose 1 (jittered) | `pyr-C28-Cl` | SCF did not converge | 2665 s |
| 2 (A1) | original pose 1 (jittered) | `bzim-C9-F` | SCF did not converge | 5151 s |

(The run-2 log, `benchmarks/danuglipron_halogen/results/run2.log`, labels the first kept pose "pose 0"; after A1 drops original pose 0 that
index is original pose 1.) Under the plan's section 5 and A1.5 (a failed SCF is not retried and not helped; the failing pose index is not
silently dropped; fewer than 2 whole poses means the run is not completed), no whole jittered pose exists, so **the run is reported as not completed**.
No paired ddE, SEM, unpaired SEM, ratio, rho, cut, self-anchor check or run record was produced, and none is claimed.

## What was measured

* Run 1: 7 pose-0 SCFs converged (parent, `bzim-C9-F/Cl/CH3`, `pyr-C28-CH3/Cl`, `bnz-C34-CH3`); they are **excluded from every statistic** by A1 and not used.
* Run 2 (original pose 1): 4 converged (parent, `bzim-C9-Cl`, `bzim-C9-CH3`, `pyr-C28-CH3`) and 3 did not (above); the other 21 of the 28 planned SCFs were not
  started because the scheduler stops submitting after a failure. Four converged jittered-pose energies for four molecules do not make a paired difference for any analogue
  except `bzim-C9-Cl`, `bzim-C9-CH3` and `pyr-C28-CH3` against the parent, and a one-pose difference has no SEM; none is reported.
* The cache `benchmarks/danuglipron_halogen/results/halogen-scf-cache.json` holds 14 entries: 11 converged (22,099 CPU-s) and 3 recorded failures (10,066 CPU-s).

## Cost

| item | CPU-h |
|---|---|
| pilots | 0.86 |
| run 1: 7 cached SCFs | 3.65 |
| run 1: in flight at the stop, unmetered (upper bound) | up to 10.0 |
| run 2, all metered (4 converged 8,949 s + 3 failed 10,066 s) | 5.28 |
| **total, upper bound** | **up to 19.9 of the 30 CPU-h cap (26 originally; raised by the user, see A1)** |

Nothing is running. The scheduler fix of A1.5 worked as designed: the three failures and four successes were all cached with their CPU.

## Deviations

* The run followed A1 exactly (`--poses 2 --skip-first-poses 1 --cap-cpu-hours 15.4 --est-scf-cpu-s 2500 --skip-model-b`, 4 workers, nice 10). The cap was not reached (5.28 CPU-h of 15.4 used by the call).
* No SCF was retried or given aids; the run stopped on the first failures as pre-declared.

## What the data support, and what they do not

* Supports: with default ferric settings, RHF/STO-3G in the full pocket field fails to converge for danuglipron analogues at the F/Cl-substituted
  pyridine site (`pyr-C28-F` on two geometries, `pyr-C28-Cl` on one) and for `bzim-C9-F`; and slow SCFs are common (1.2k to 5.2k CPU-s). It does not tell why (field close contacts at
  jittered poses, the neutral-acid choice, basis size, SCF algorithm), and a single attempt per case does not show it is deterministic.
* Does not support: any statement about the halogen scan, pairing, the campaign ranges, the cut, or experiment.

## Decisions for the user

1. **Fix the SCF, not the scan.** Take the failing cases to ferric (issue): `pyr-C28-F` original poses 0 and 1, `pyr-C28-Cl` pose 1, `bzim-C9-F` pose 1, with the 6458-charge field. Whether
   damping, level shifting or an SCF-algorithm switch converges them is a ferric question; using one here is a plan change (the plan forbids aids), so it needs your approval and a new amendment.
2. **Or shrink the scan** to analogues that converge (the CH3 SCFs converged in 6 of 6 attempts, the pilot included; the F and Cl SCFs in 4 of 8), which changes what is being asked and cannot reproduce "12 analogues".
3. **Or change the field**: a distance cutoff (the campaign used none; the field is 6458 charges) would be a plan change, for ferric-side reasons unknown.
4. Budget remaining under the 30 CPU-h cap: about 10 CPU-h at the upper bound of spend, which is roughly one pose of 14 molecules (about 7 CPU-h at the observed mean), not two.
