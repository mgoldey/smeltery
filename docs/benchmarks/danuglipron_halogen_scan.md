# Danuglipron halogen scan (issue #6), run 5: the scan completed on rebuilt poses

Plan: [danuglipron_halogen_plan.md](danuglipron_halogen_plan.md) with amendments A1 to A4. A3 (rebuilt poses, geometry gate) and A4 (SCF iteration limit) were committed before the SCFs they describe.
Earlier runs and their failures are in [danuglipron_halogen.md](danuglipron_halogen.md); they were measured on poses with the halogen stranded away from its carbon and are superseded by this report, not carried into it.

## Outcome: every SCF converged. The scan completed with 2 poses per molecule; it supports no ranking.

ferric 0.1.0rc7 (PyPI, commit `cddfd1aa`), RHF/STO-3G, 7LCJ AMBER field of 6,458 charges net -1.00, 14 molecules (parent, parent-self, 12 analogues) x 2 poses = 28 poses.
The geometry gate passed 28 of 28 poses before any SCF. All SCFs converged at `energy_conv` 1e-10, `density_conv` 1e-8, `max_iter` 200. Run record
`benchmarks/danuglipron_halogen/results/danuglipron-halogen-38fa833cb90c.json`, log `benchmarks/danuglipron_halogen/results/run5.log`.
Command: `examples/danuglipron_halogen.py --poses 2 --skip-first-poses 1 --skip-model-b --relax-unmapped --max-iter 200 --cap-cpu-hours 10 --est-scf-cpu-s 2500 --workers 6`.

**Paired ddE (AMBER, kcal/mol), per pose, pose 0 and pose 1 in post-skip numbering:**

| analogue | pose 0 | pose 1 | mean | paired SEM | unpaired SEM | unpaired/paired |
|---|---|---|---|---|---|---|
| `bzim-C9-F` | -1.47 | -1.02 | -1.245 | 0.228 | 2.907 | 12.74 |
| `bzim-C9-Cl` | -2.70 | -2.19 | -2.445 | 0.253 | 2.926 | 11.57 |
| `bzim-C9-CH3` | +0.15 | -1.68 | -0.764 | 0.916 | 2.192 | 2.39 |
| `pyr-C28-F` | +1.20 | +0.68 | +0.940 | 0.257 | 2.566 | 10.00 |
| `pyr-C28-Cl` | +2.47 | +1.12 | +1.795 | 0.674 | 2.314 | 3.43 |
| `pyr-C28-CH3` | -2.80 | +0.06 | -1.371 | 1.430 | 3.886 | 2.72 |
| `bnz-C34-F` | -2.71 | -5.03 | -3.869 | 1.158 | 2.090 | 1.80 |
| `bnz-C34-Cl` | -3.77 | +16.34 | +6.286 | 10.056 | 12.150 | 1.21 |
| `bnz-C34-CH3` | -0.02 | +5.06 | +2.522 | 2.540 | 4.880 | 1.92 |
| `bnz-C36-F` | +1.10 | +0.50 | +0.801 | 0.301 | 2.538 | 8.44 |
| `bnz-C36-Cl` | +1.99 | +0.78 | +1.384 | 0.606 | 2.352 | 3.88 |
| `bnz-C36-CH3` | -0.54 | -0.40 | -0.472 | 0.070 | 2.792 | 39.68 |

* **Self-anchor (criterion 1):** parent-self ddE is exactly 0.0 on both poses; parent `dE_int` is -18.41 and -14.53 kcal/mol, so the field is not inert.
* **Pairing (criterion 3):** the paired SEM is below the unpaired SEM for 12 of 12 analogues. With 2 poses each SEM has 1 degree of freedom and rho is +1.0000 for every analogue by construction.
* **Campaign comparison (criterion 2):** UNVERIFIED, no campaign per-analogue values are available. The rho range (0.78 to 0.9997) holds 0 of 12 analogues, which is a consequence of rho = +1.0000 at n = 2 and not evidence about the scan.
  The CH3 paired SEM range (1.0 to 3.5) holds 2 of 4 (`bnz-C34-CH3`, `pyr-C28-CH3`); with a 1-dof SEM that is not informative.
* **Second charge model (criterion 4):** NOT RUN (`--skip-model-b`): sign flips and Spearman between charge models are unmeasured.
* **Cut at the declared floor 1.26 kcal/mol (`docs/limits.md` L6):** UNRANKED. keep=3 falls inside an unresolved group of 6; 8 of 11 adjacent gaps are at or below the floor and none is ranked across.
* **One large swing:** `bnz-C34-Cl` per-pose ddE is -3.77 and +16.34 kcal/mol, a 20.1 kcal/mol spread (paired SEM 10.06). It was not investigated; with 2 poses it cannot be called a cause, an outlier or a property of the molecule.

## How it got here (details in the plan and `danuglipron_halogen_ch3_diag_plan.md`)

1. Runs 1 to 3 failed to converge for 7 F/Cl SCFs. A fragment test then showed that in 5 of 6 failed poses the halogen sat 3.3 to 8.2 Angstrom from its carbon, because `PairedPoses` defaults to `relax_unmapped=False`
   (`docs/benchmarks/danuglipron_halogen_accel.md`). The old poses fail the new geometry gate in 23 of 28 cases (`benchmarks/danuglipron_halogen/results/pose_geometry_relax0.json`); the rebuilt ones in 0 of 28 (`pose_geometry_relax1.json`).
2. Run 4 (A3, rebuilt poses) converged 8 of 10 pose-0 analogues and left `pyr-C28-CH3` and `bzim-C9-CH3` unconverged. Diagnosis (`benchmarks/danuglipron_halogen/ch3_diag/`): both converge in 43 and 64 iterations when ferric is called directly with `max_iter=200`; the tier's own call
   stopped `pyr-C28-CH3` at ferric's default limit of 100 iterations unconverged and converged it at 111 with `max_iter=200`. The failures were the iteration limit that `FieldInteraction` did not set.
3. A4 added `FieldInteraction(max_iter=...)`, ran everything still to do at 200 and re-attempted those two SCFs once. Both converged, as did all 17 other new SCFs.

## Deviations and things to know

* **A4 overrides "a failed SCF is final" for two SCFs** (`pyr-C28-CH3` and `bzim-C9-CH3` pose 0), on the stated ground that the cause was the iteration limit and not the SCF. Nine SCFs that converged under ferric's own limit were reused as they were; the cache marks them `reused_from_max_iter`.
* **Charge order affects the iteration count.** One measured pose needed 111 iterations with charges in PQR file order and 64 nearest-ligand-first, with converged energies equal to about 1e-9 Hartree. The scan uses the tier's PQR order, unchanged. Whether this matters beyond iteration count is not measured.
* **Budget.** This call metered 8.58 CPU-h against a cap of 10 (my number; the project owner approved the plan without naming one). The SCFs behind the table total 12.14 CPU-h including reused ones. Earlier spend was up to 29.0 (runs 1 to 3, an upper bound), 13.2 (accelerator and ladder study), 4.3 (run 4) and 2.9 metered in the diagnosis plus unmetered lost
  attempts (a recording bug and a stalled launch); the study is at about 60 CPU-h against the 30 CPU-h of the original plan, a disclosed overrun.
* **Pose 0 was dropped (A1)** and the surviving two poses are indexed 0 and 1 above. The parent's and self-pair's pose-0 SCFs come from run 4's cache.
* The diagnosis ran ferric on one pose per question; it does not show the same holds for every pose.

## What the data support, and what they do not

* Supports: with sound geometry and an iteration limit of 200, RHF/STO-3G in the full 6,458-charge pocket field converges for all 28 poses of this series; the paired difference is tighter than the unpaired one for all 12 analogues.
* Does not support: any ranking of the analogues (UNRANKED at the declared floor), any value of rho or an SEM range from 2 poses, any comparison with the campaign, any statement about experiment, or a cause for the `bnz-C34-Cl` swing.
