# Plan: AMBER ff14SB against CHARMM36 pocket charges on PLB tyk2 (issue #29, second system)

Status: **PLAN, committed before any CHARMM36 SCF.** Anything done differently is listed under "Deviations" in the report (`docs/benchmarks/plb_tyk2_charmm.md`).
Context: [plb_cdk2.md](plb_cdk2.md) did this comparison on cdk2 (n = 8, 5 poses); [plb_tyk2.md](plb_tyk2.md) is the merged tyk2 run (AMBER only, 8 poses). This repeats cdk2's
comparison on tyk2 and changes **one thing** relative to the merged tyk2 run: the pocket charge model.

## 1. What is compared

* **Model A, AMBER ff14SB: not rerun.** The stored per-pose energies in `benchmarks/plb/results/tyk2_sto3g.json` (`amber14`, 8 poses, and the shared vacuum energies) are reused as they are.
* **Model B, CHARMM36**: `scripts/plb_pocket_charges.py --ff charmm36` on the same `protein.pdb` (sha256 `9090684f...`), same field centre (-7.9646, -9.0535, -17.2246), cutoff 15 A, ACE/NME deleted (guard: both lie
  beyond cutoff + 1 A), the same two crystal waters dropped, protonation untouched. Result: 1013 charges inside the cutoff, as for AMBER; histidines matched HSD (6) and HSE (7), the same tautomers as AMBER's HID/HIE.
  Net charge of the cropped sphere +0.895 e (AMBER +1.402 e). No other difference from the AMBER field is intended; the PQR's digest and the force-field file digest go in the record.
* **Identical everything else**: same 13 ligands, same 8 poses (seed 1, same rigid-jitter code: the run script's `pose_geometry_sha256` must equal the stored run's, else the assembly refuses), RHF/STO-3G, `energy_conv` 1e-10,
  `density_conv` 1e-8, same ferric (0.1.0rc7, pinned in the stored record; the assembled record states the identity actually used), one thread per process.
* Only the field SCFs (13 ligands x 8 poses = 104) are run. dE_int(B) = E_RHF(field B) - E_RHF(vac), the vacuum energy being the stored one (it does not depend on the charge model).

## 2. Statistics (the code that produced cdk2's, `plb_measure.charge_model_comparison`; nothing new is invented)

n = 12 analogues, ddE_i = mean over the poses of [dE_int(i,k) - dE_int(parent,k)] per model, over the poses **both** models completed. Reported:
1. **ddE sign flips**: analogues whose mean ddE changes sign between A and B, k/12.
2. **Pair-order flips**: of the 66 analogue pairs, those whose order by mean ddE differs between A and B, k/66.
3. **Spearman between A and B** of the 12 mean ddE (95% bootstrap CI over ligands, 10,000 resamples, seed as the analysis code), and Kendall tau-b.
4. **charge_sensitivity floor** = max - min over the 12 ligands of (ddE_B - ddE_A), kcal/mol (`gates.charge_sensitivity`). This is a **range** over n values, so it grows with n: tyk2 (12 values) and cdk2 (8 values) are not like for like, and that is stated beside the numbers.
5. Descriptive extras, same as cdk2: raw dE_int shift (mean +/- sd), raw dE_int Spearman across ligand-pose, the floor next to the mean paired pose SEM, the size of the smallest |ddE| (sign flips on near-zero ddE mean little), and the CHARMM36 ddE against experiment with the same statistics as AMBER's
   (secondary; it cannot change the primary label of `plb_tyk2.md`, which stays the AMBER endpoint).
6. Side by side with cdk2's published values (0/8, 1/28, +0.976, 1.26 kcal/mol, n = 8). The two systems are never pooled or tested against each other.

## 3. Language fixed for each outcome

Descriptive only; no hypothesis test is run on these counts.
* **"The two systems agree"** (on the charge-model sensitivity): tyk2 Spearman between models >= 0.90, AND sign flips <= 1 of 12, AND pair-order flips <= 6 of 66 (10%), AND the tyk2 floor lies within a factor 2 of cdk2's 1.26 (0.63 to 2.52 kcal/mol).
* Otherwise **"the two systems disagree"**, said per statistic, with which system is more sensitive. A floor outside the factor-2 band is reported as a disagreement even if the other three agree, together with the note that the range grows with n.
* **What the floor supports in either case**: a lower bound on charge-model systematic error for the AMBER/CHARMM36 pair of point-charge pockets on two kinase pocket fields. The floor to quote is the larger of the two systems', as a lower bound ("at least X kcal/mol on the worst of two systems"), unless the data say the two differ so much that a single number misleads; then both are
  quoted and no single number is. It supports: pairwise ddE differences below the quoted floor should be called unranked **for this pair of models**. It does not support: a published default `FieldInteraction.systematic_floor` (none is set or changed; #29 asks for Lowdin/Hirshfeld/external ligand-side models, which are not run),
  a statement about other targets, other charge models, or the ligand side, or any statement that CHARMM36 or AMBER is closer to the truth.
* If the floor exceeds the mean paired pose SEM, say that charge-model disagreement, not pose noise, is the larger uncertainty on this series; if smaller, the reverse (descriptive, point estimates).

## 4. SCF non-convergence rule (fixed now)

An SCF that does not converge (ferric `converged` False or any error) is **not retried and given no convergence aids**. Its pose index is dropped for **every ligand and both models** (so A and B always use the same geometries; the stored AMBER values at that pose are simply left out of the comparison file; the stored AMBER file itself is unchanged),
recorded under `dropped_failures` with the reason. If the CPU cap stops the run, whole poses (pose-major order) completed for all 13 ligands are used. If fewer than 4 whole poses are available the comparison is reported as run but flagged low-pose, and with fewer than 2 as NOT measured.

## 5. Budget (cap 6 CPU-hours for everything run for this study, including the pilot)

At most 4 worker processes at `nice -n 10`, one thread each (`OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=1`). From the merged run's measured costs (`plb_tyk2_plan.md` section 7: one SCF per ligand, summed over the 13 ligands, is about 1557 CPU-s = 0.43 CPU-h), the 13 field SCFs of one pose cost about 0.43 CPU-h and **8 poses about 3.5 CPU-h** (the merged run's own stage 1 totalled twice that for vacuum + field). A pilot (2 SCFs: parent and `lig_ejm_48`, pose 1, CHARMM36 field) times the field and prints **CPU seconds only**, never energies, and is kept out of the results.
The run takes `--max-cpu-hours 5.5`, so the worst case with 4 SCFs in flight is under 6. Nothing is left running when the work ends. If the pilot projects more than 5.5 CPU-h, the pose count is reduced from the end (pose-major) before starting, and the report says so.

## 6. What this study will not do

No rerun of any AMBER SCF, no new geometry, no dielectric screening, no ligand-side charge models, no default floor constant and no `FieldInteraction.systematic_floor` change, no pooling of cdk2 and tyk2, no tuning after seeing results (every threshold above is fixed here).
Tests added: the assembled record must validate, equal the stored AMBER values exactly, and the numbers quoted in the report must equal the analysis file; mutation-checked by hand (listed in the test file).
