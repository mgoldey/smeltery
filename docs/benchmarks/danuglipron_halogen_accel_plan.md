# Danuglipron halogen scan: AURORA and TRAH on the unconverged SCFs (issue #6, ferric#418)

Written before any accelerated SCF was run. This extends `danuglipron_halogen_plan.md` (A1, A2); it does not change any of its rules.

## Why

Run 3 (A2, `energy_conv` 1e-6 Ha) left 4 molecule-poses unconverged, all F or Cl, and no paired ddE could be built for them. ferric PR #419 (ferric#418) exposes two opt-in accelerators in Python: AURORA (`aurora=True`, closed-shell RHF, same fixed point as DIIS, documented as a speed-up) and TRAH (`trah_trigger=`, documented as a convergence rescue that costs more wall time). This study asks one question: **does either converge the SCFs that DIIS did not?**

## What is run

* **Cases (6, fixed now from the committed cache `benchmarks/danuglipron_halogen/results/halogen-scf-cache.json`):** every molecule-pose with a recorded DIIS non-convergence in the AMBER pass, in the run's own pose numbering (`--poses 2 --skip-first-poses 1`, pose index 0): `pyr-C28-F`, `pyr-C28-Cl`, `bzim-C9-F`, `bnz-C34-Cl`, `bnz-C36-F`, `bnz-C34-F`, in that order (ascending DIIS CPU of the recorded failure). `pyr-C28-F` and `pyr-C28-Cl` failed at 1e-10 and then converged under DIIS at 1e-6, so they have a DIIS energy to compare with; the other four failed under DIIS at both thresholds.
* **Same inputs as run 3:** the same seeded poses, the 7LCJ AMBER field (no cutoff), RHF/STO-3G, `energy_conv` 1e-6, `density_conv` 1e-8, ferric's default `max_iter` (100). Only the field SCF takes an accelerator; the vacuum SCF is unchanged (DIIS).
* **Variants:** AURORA `aurora=True`. TRAH `trah_trigger=1e-2` (engaged once the DIIS error falls below 1e-2; ferric gives no recommended value, this one value is chosen now and not scanned).
* **Order:** Stage 1 runs AURORA on all 6 cases. Stage 2 runs TRAH on exactly the cases that AURORA did not converge (none if it converged all). Nothing is selected or dropped on any energy.
* **ferric build:** the released rc7 has no switches. This uses the unreleased `0.1.0.dev0` wheel from ferric Wheels run 38630999922 on commit `773081a24984d5ca43153311dc4ccfcb369b095c` (cp312 wheel sha256 `9fd4605ead1a69e0d8b4778802d02a016610acfeeb25a1e4f5f68f58648ad064`), in a separate environment. smeltery's pin stays at rc7. The record states the build.

## Budget and stopping

* **Cap: 12 CPU-h for this study** (set by the project owner on 2026-10-14), all stages together, failed and killed jobs included. A job is started only if CPU spent so far plus 5,000 CPU-s for every job in flight and for the new one stays within 43,200 CPU-s; jobs left unstarted are reported as not run.
* **Per-job limit: 5,000 CPU-s** (process CPU limit, killed beyond it). A killed job counts as "not converged within the cap", which is not the same as DIIS hitting its 100-iteration limit; it is reported as what it is. Worst case Stage 1 is 30,000 CPU-s, so Stage 2 may fit only two TRAH jobs; the rest are then reported as not run.
* This takes the study total (29.0 CPU-h so far against the 30 CPU-h of A1/A2) over that figure by up to 12; the extra is the owner's explicit allowance, disclosed here.

## Outcome language (fixed now)

* **Per case and variant:** converged (ferric's `converged` is true) or not, iteration count, CPU-s, and dE_int (field minus vacuum, kcal/mol).
* **Agreement check:** for `pyr-C28-F` and `pyr-C28-Cl`, an accelerated dE_int must equal the cached DIIS dE_int to within 2e-3 kcal/mol (two energies at 1e-6 Ha is about 1.3e-3). Outside that, the case counts as converged to a different solution, **not** as a rescue, and is reported as such.
* **No reference case:** for the four DIIS-failed cases there is no DIIS energy, so agreement cannot be checked. A converged accelerated SCF there is reported as "converged, unverified against DIIS", with no stability analysis (it may be a saddle or a different state). The sanity band of the earlier runs (the field must be non-inert, |dE_int| > 1 kcal/mol) is applied; the one reused value of +106.4 kcal/mol in run 3 is a reason not to read a converged number as right.
* **Headline statement:** "AURORA converged k of 4 DIIS-failed cases; TRAH converged j of the m it was run on", with the agreement check on the two comparable cases.

## What this does not do

It does not compute any paired ddE, re-run any other SCF, change a default, or complete the scan. Even if every case converges, completing #6 would be a separate decision (the accelerated SCFs would have to be mixed into the existing 18 converged ones, one more change of treatment) for the project owner. n is 6 cases, one pose each; nothing here supports a statement about ranking, the campaign or experiment.
