# Danuglipron halogen scan (issue #6): runs 5 and 6

> **Run 6 (2026-11-05)** re-ran the whole scan as a smeltery pipeline with 4 poses per candidate and both charge
> models, and is the current result: see [run 6](#run-6-2026-11-05-the-scan-as-a-pipeline-4-poses-both-charge-models)
> below. Run 5 follows it unchanged.

## Run 6 (2026-11-05): the scan as a pipeline, 4 poses, both charge models

Config `src/smeltery/prebuilt/danuglipron-halogen/pipeline.toml`, record
`src/smeltery/prebuilt/danuglipron-halogen/run/run-ccb0c68648fe.json`, input digest `ccb0c68648fe`, every per-SCF
line in `run/calcs.jsonl`. Walked through section by section in [../worked-example.md](../worked-example.md).

What it changed against run 5: the campaign is one config instead of a script, pose 0 is the cryo-EM conformer
itself (run 5 dropped it under A1), there are 4 poses per candidate instead of 2, the AMBER-vs-CHARMM sensitivity
was measured instead of skipped, and a remedy ladder replaces "a failed SCF is final".

**156 SCF legs, all converged, 38.4 CPU-h** (52 poses x vacuum + AMBER field, the vacuum leg shared with CHARMM;
6 workers x 1 thread; `smeltery run --temporal`). `--plan` predicted 21.6 CPU-h, 1.8x low; see "Cost" below.

| analogue | mean ddE | paired SEM | unpaired SEM | unpaired/paired | per pose (0 to 3) | CHARMM shift |
|---|---|---|---|---|---|---|
| `bnz-C34-F` | -4.11 | 0.76 | 3.68 | 4.8 | -2.93, -2.71, -5.03, -5.76 | -0.01 |
| `bzim-C9-Cl` | -2.76 | 0.36 | 3.63 | 10.0 | -2.36, -2.70, -2.19, -3.81 | -0.13 |
| `bzim-C9-F` | -1.71 | 0.50 | 3.74 | 7.5 | -1.18, -1.47, -1.02, -3.19 | -0.04 |
| `pyr-C28-CH3` | -0.91 | 0.66 | 3.54 | 5.3 | -0.03, -2.80, +0.06, -0.85 | -0.14 |
| `bnz-C36-CH3` | -0.35 | 0.08 | 3.34 | 41.5 | -0.28, -0.54, -0.40, -0.17 | -0.30 |
| `bnz-C36-F` | +0.83 | 0.13 | 3.33 | 26.6 | +0.81, +1.10, +0.50, +0.91 | +0.07 |
| `pyr-C28-F` | +1.09 | 0.19 | 3.25 | 17.0 | +0.91, +1.20, +0.68, +1.57 | +0.22 |
| `bnz-C36-Cl` | +1.43 | 0.26 | 3.27 | 12.6 | +1.29, +1.99, +0.78, +1.66 | +0.27 |
| `bzim-C9-CH3` | +1.56 | 2.22 | 2.42 | 1.1 | -0.36, +0.15, -1.68, +8.11 | -0.18 |
| `pyr-C28-Cl` | +2.19 | 0.47 | 3.07 | 6.5 | +1.82, +2.47, +1.12, +3.34 | +0.43 |
| `bnz-C34-CH3` | +2.36 | 1.37 | 3.48 | 2.5 | +0.02, -0.02, +5.06, +4.39 | +0.22 |
| `bnz-C34-Cl` | +5.58 | 5.20 | 5.95 | 1.1 | -2.88, -3.77, +16.34, +12.62 | -0.10 |

All in kcal/mol. The CHARMM shift is the change in the mean when the same pocket is charged by
`pdb2pqr30 --ff=CHARMM` instead of AMBER.

* **Run 5 is reproduced.** Today's poses 1 and 2 are run 5's poses 0 and 1 (same seed, same pose builder), and
  their ddE values agree to the digit printed: `bnz-C34-Cl` -3.77 and +16.34, `bzim-C9-F` -1.47 and -1.02,
  `bnz-C36-CH3` -0.54 and -0.40. Poses 0 and 3 are new.
* **Self-anchor (criterion 1):** parent-against-itself is exactly 0 on all four poses; the parent's own dE_int is
  -17.41, -18.41, -14.53 and -25.74 kcal/mol, so the field is not inert.
* **Pairing (criterion 3):** the paired SEM is below the unpaired SEM for 12 of 12, by 4.8x to 41.5x for the nine
  well-behaved analogues and 1.1x for the two with a disagreeing pose.
* **Charge sensitivity (criterion 4): MEASURED, and small.** No analogue moves more than 0.43 kcal/mol between the
  two charge models (`pyr-C28-Cl`); the largest shift sets a floor of 0.73 kcal/mol. No sign flips. Run 5 skipped
  this (`--skip-model-b`), so this is the first measurement of it on this series.
* **Cut: UNRANKED** at keep 3, z 2.0, floor 1.26. Three analogues have an SEM at or above their own mean
  (`bnz-C34-Cl` 5.20, `bzim-C9-CH3` 2.22, `bnz-C34-CH3` 1.37), each from one pose disagreeing with the other three,
  so no gap near the keep-3 boundary clears the floor. Four poses is not enough to rank these twelve.
* **What it does support:** `bnz-C34-F` is the most stabilised of the twelve and is stabilising on all four of its
  poses, as are `bzim-C9-Cl` and `bzim-C9-F`; `bnz-C36-CH3` is the tightest value in the set (SEM 0.08). The
  pose-to-pose disagreement, not the charge model and not the SCF, is what blocks a ranking.
* **Campaign comparison (criterion 2):** still UNVERIFIED; no per-analogue campaign values are available.

### One SCF reported a false convergence

`pyr-C28-Cl` pose 3's vacuum leg did not converge at rung 0 (200 iterations). Rung 1 (`diis = "adiis"`,
`level_shift = 0.5`) reported convergence after **3 iterations at 0.82 Ha above rung 0's last energy**, which gave
that pose a ddE of -509.6 kcal/mol and made the cut meaningless. An SCF iterate is the energy of a real
wavefunction, so a minimum above one already reached is impossible, not merely odd.

It is a ferric bug in ADIIS with a level shift, fixed upstream in `455b8fbad`; on the installed build (`b22183b`) it
fired on every molecule tried, including the parent's well-behaved pose 0 (3 iterations, 0.77 Ha high). Two things
changed: smeltery now refuses such a rung, records it as not converged with the reason, and climbs
(`refuse_false_convergence`, commit f342d40); and that one leg was rerun at rung 2 on ferric `81f411c`, where it
converged in 13 iterations to 7e-9 Ha of rung 0's last energy. The record keeps both builds under
`results.builds`, so the mixed-build provenance is visible rather than flattened.

### Cost

| item | CPU-h |
|---|---|
| 156 SCF legs, as logged (`results.timing.measure_seconds`) | 38.36 |
| of which pyr-C28-Cl pose 3's vacuum leg: rung 0 (not converged), rung 1 (refused), rung 2 (the value) | 0.52 + 0.01 + 0.25 |
| `--plan`'s prediction before the run | 21.64 |

The prediction is 1.8x low for two compounding reasons, both now disclosed in the estimate itself: at 235 basis
functions these molecules are 2.5x the largest size the cost model was fitted on (93 bf), and they took a median
79 SCF iterations per pose against the 36-iteration reference the model assumes. The run is committed as an
extrapolation sample in `src/smeltery/data/cost_measurements.json`, and `--plan` now prints LOWER BOUND with both
reasons when a pose exceeds the fitted ceiling.

### Deviations and things to know

* **One leg ran on a different ferric build**, as described above, so the config carries
  `[run] allow_mixed_builds = true`. Every other leg is `b22183b`. The two builds agree to 7e-9 Ha where they
  overlap.
* **46 of the 158 logged lines carry no build stamp**: they were written before per-row build identity existed, by
  the first worker of this run. The record marks them `identity: "unrecorded"` rather than assuming.
* **The run was interrupted twice** (a worker restart to pick up a fix, and a refused resume) and resumed from
  `calcs.jsonl` both times. No leg was recomputed except the one that needed it.
* A resumed run was refused once because `pdb2pqr30` resolved to 3.7.1 behind a pyenv shim instead of the venv's
  3.8.0, changing the charges and so the input digest. smeltery now prefers the `pdb2pqr30` beside its own
  interpreter (commit 1303507).

---

# Run 5: the scan completed on rebuilt poses

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
