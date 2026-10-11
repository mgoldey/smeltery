# Plan: smeltery's paired ddE against the PLB experimental ddG on tyk2, and a screening study of the pocket field

Status: **PLAN, written and committed before any production run.** It repeats the cdk2 protocol
([plb_plan.md](plb_plan.md), report [plb_cdk2.md](plb_cdk2.md)) on the series the cdk2 report named as the next step
(PLB tyk2, 12 analogues), with the SAME analysis code, and adds ONE pre-declared screening analysis for issue #28. Anything
done differently from this plan is listed under "Deviations" in the report (`docs/benchmarks/plb_tyk2.md`), with the reason.
Issues: #27 (does the paired ddE track experiment, with more power than n = 8) and #28 (dielectric screening). #29's
charge-model comparison is **not** run here (section 9).

## 0. What is already known, what has been seen, and what this study may not do

* cdk2 (n = 8): Spearman rho +0.690, exact p 0.069, power 40% at r = 0.7; not resolved. A trivial descriptor (Crippen logP,
  rho +0.79) did at least as well as ddE. The sd of ddE across ligands was 4.2 times the sd of experimental ddG (slope +3.00,
  CI +0.37 to +7.47). Pose-ensemble noise (mean paired SEM 2.07 kcal/mol at 5 poses) was the dominant uncertainty. Those
  numbers are context. **The cdk2 and tyk2 results are never pooled**: they are different targets, assays and fields, and each is
  analysed on its own.
* The benchmark's own power table (the manifest's `suitability.power` block) says no pair in tyk2's series differs
  by the 4.07 kcal/mol noise floor (0 of 78 pairs; the largest difference from the parent is 1.92 kcal/mol, the whole span 3.45),
  so pairwise ranking is impossible a priori even at slope 1. The right statistics are trend statistics over ligands.
* **No knob is tuned after seeing agreement with experiment.** Basis, cutoff, poses, jitter, seeds, the eps set, the charge model and every
  statistic are fixed here. The analysis code is committed with synthetic-data tests before the production results are assembled;
  the run log prints timings only, never energies.
* **Disclosure of what has been seen.** (a) The manifest's tyk2 ddG values, errors and structures (they are the public experiment). (b) One timing pilot
  computed `lig_ejm_42` (the parent) and `lig_ejm_48` at pose 1, vacuum and AMBER field, and printed **CPU seconds only**: 104/108 s
  and 145/150 s (vacuum/field). The energies were not printed or stored. (c) The cdk2 results above. **The eps set {2, 4, 8} was chosen knowing
  that cdk2's computed spread was about 4 times the experimental one**: to bracket the range in which a first-order uniform scaling would bring that ratio
  to about 1. No tyk2 ddE or dE_int has been seen.

## 1. Questions, fixed in advance

| # | question | statistic (section 5) |
|---|---|---|
| Q1 (#27) | Does smeltery's paired ddE track the experimental ddG across the tyk2 ligands? | Spearman rho of ddE against ddG, n = 12 analogues, permutation p (Monte Carlo, section 5), 95% bootstrap CI over ligands; Kendall tau-b the same way. **Primary endpoint: rho, AMBER ff14SB, unscreened field.** |
| Q2 (#27) | Does the overshoot survive pairing? | OLS slope of ddE on ddG with bootstrap CI, sd(ddE)/sd(ddG); read **only if Q1's answer is "tracks"** |
| Q3 (#27) | Does ddE carry more rank information than a trivial descriptor? | the same statistics for heavy-atom count and Crippen logP |
| Q4 (#27) | How many of the 78 pairs does ddE resolve (z = 2) at the achieved pose SEM, and are the resolved ones ordered correctly? | `funnel.resolved` with floor 0 and 4.07 |
| Q5 (#28) | Does dividing the pocket field's charges by a uniform eps move the slope toward 1, and does the rank correlation change? | section 4 (the single pre-declared screening analysis) |

Only ONE tier is measured: the QM `FieldInteraction` (RHF/STO-3G). Docking, force-field and xtb tiers are not run on this series
and nothing is said about them.

### Hypotheses written down before running (issue #27's note, unchanged)

* H_cancel: pairing cancels the overshoot; slope(ddE on ddG) near 1. H_overshoot: slope near 4 to 5. H_null: slope near 0.
  Slope is interpreted only if Q1 says "tracks". If the 95% CI excludes 1 and contains 4.5, "overshoot survives"; if it
  contains 1 and excludes 4.5, "cancels"; both or neither, "not resolved".
* Regressing ddE on ddG is justified because the experimental error is small against the spread: median sigma 0.25 kcal/mol,
  mean sigma^2 0.062 against a variance of 1.25 (sd 1.12) for the 12 analogues, an attenuation of the slope of about 5%.

## 2. Data (verified from the files, not from the READMEs)

* PLB commit `fd88824f9114244f95a14b485e6d6c96c1de716d`, CC BY 4.0, target **tyk2** (PDB 4GIH, TYK2 pseudokinase JH2). Files fetched with
  `scripts/fetch_plb.py` (no LFS needed: the files are served as plain text) and sha256-checked against the manifest; the run script
  re-checks them and refuses a ligand whose SDF structure differs from the manifest's SMILES.
* **13 ligands in `ligands.sdf`** (counted from the SDF: 13 records, names equal to the manifest's), 12 analogues + the parent. All **formal charge 0**
  (RDKit formal charge of each SDF record; the manifest agrees, `plb_charge` is null for all). Elements: H, C, N, O, F, Cl.
  Heavy atoms 21 to 25, 32 to 42 atoms, 124 to 150 STO-3G basis functions. **ferric 0.1.0rc7's STO-3G covers all six elements** (checked with
  `run_plb_ddE.basis_gaps`, which the run script also calls and which refuses to start otherwise): **no ligand is excluded.** No metal, cofactor or
  non-organic element in the ligands.
* All ligands are in the protein's frame (centroids within 1.8 A of each other): one consistent binding mode.
* **Parent** `lig_ejm_42` (propanamide), chosen by the manifest's rule (the ligand with the most single-site-change neighbours: 12 of 12), not by me.
* Experimental ddG = `R T ln(Ki_i / Ki_ref)` at 298.15 K from the manifest. **Ki, not IC50** (unlike cdk2). PLB's `error` is a 30% relative error
  for every ligand (sigma(ddG) about 0.25); what it means is unverified. ddG range -1.92 to +1.53 kcal/mol.
  **Caveat, stated now:** 9 of the 12 analogues (and the parent) are from one publication (10.1016/j.ejmech.2013.03.070, "ejm"); 3 (`lig_jmc_23/27/28`,
  the halo/methyl cyclopropyl amides) are from another (10.1021/jm400266t, "jmc"), where PLB took the cis enantiomer's value from a table listing four. Cross-publication Ki
  differences are an unmodelled experimental error (cf. `docs/limits.md` L1). A pre-declared secondary analysis uses the 9 ejm analogues only (section 5.9).

## 3. Procedure (the cdk2 procedure, changes listed)

### 3.1 The pocket field (tool `scripts/plb_pocket_charges.py`, OpenMM 8.6.1)

* AMBER ff14SB (`amber14-all.xml`) charges by OpenMM's residue templates; protonation exactly as PLB ships it. **Deleted:** the ACE and NME caps (no
  template), after the tool's guard confirmed their atoms lie beyond cutoff + 1 A of the field centre: measured ACE A889 at 22.7 A, NME A1178 at 28.3 A.
  There is no TPO or other nonstandard residue in this file (HETATM inventory: ACE, NME, HOH only). **Dropped:** the **two** crystal waters (HOH A1318 at 9.4 A and
  HOH A1325 at 3.4 A from the centre, both INSIDE the 15 A cutoff), the same modelling choice as cdk2 (a water fitted to the crystal ligand, in a field-only model with no other
  water); recorded in the provenance. The two waters are closer to the ligand than cdk2's single water was, so this choice may matter more here; it is not varied.
* Field centre = centroid (4 decimals) of the parent in the PLB frame, (-7.9646, -9.0535, -17.2246) A. One field for all ligands. Cutoff 15 A, by atom: **1013 charges**.
  Net charge of the cropped sphere +1.40 e (the whole prepared protein sums to -2.00 e, PLB's own record says -3; not investigated). Histidines matched: 6 HID, 7 HIE.
* Only the one charge model (AMBER). **CHARMM36 is not run** (section 9).

### 3.2 Poses

As cdk2: pose 0 is each ligand's PLB pose; poses k >= 1 apply to EVERY ligand the same rigid transform (<= 15 degrees, <= 0.5 A per axis, about the parent's centroid, `default_rng([1, k])`,
seed 1). **8 poses per ligand (0 to 7)**, up from cdk2's 6, because pose noise dominated cdk2 and tyk2's ligands are cheaper (section 7). Not docking, no relaxation.
Close contacts of 1.0 to 1.5 A between a ligand atom and a protein point charge were found in cdk2; here each pose's minimum distance is recorded and the secondary analysis (drop poses
with any contact under 2.0 A) is run as in cdk2; if it is vacuous again it will be said so, not re-tuned.

### 3.3 Quantum chemistry

ferric 0.1.0rc7 from PyPI (`--no-sources`, python 3.11, as cdk2), RHF/STO-3G, `energy_conv` 1e-10, `density_conv` 1e-8, one thread per process, 4 worker processes, `nice -n 10`.
dE_int = E_RHF(in field) - E_RHF(vacuum) per ligand and pose (tested equal to `FieldInteraction`); ddE_i = mean over poses of [dE_int(i, k) - dE_int(parent, k)].
STO-3G is too small a basis to be quantitative for a polarisable interaction; not varied.

## 4. The screening analysis (issue #28): ONE analysis, declared now

**Treatment.** For eps in **{2, 4, 8}** the field's point charges are divided by eps (positions untouched): a uniform, distance-independent relative permittivity applied to the
bare AMBER field. eps = 1 is the unscreened field (the primary data, no separate SCF). No other treatment is run: not a distance-dependent dielectric, not COSMO, not PCM
(ferric's IEF-PCM has an open solver defect; any PCM row would be pre-fix and excluded from conclusions, so none is made), and no eps is added after seeing results.

**Poses.** The screened SCFs are run on the **first 4 poses of the kept pose list** (decided by index and SCF convergence only), all 13 ligands, so that the eps = 1 row for this analysis is
computed on exactly the same four poses (the analysis restricts eps = 1 to the poses every screened model completed). If the CPU cap stops stage 2 early, the poses completed for all ligands and all
eps are used (pose-major); if fewer than 3 whole poses complete, #28 is reported **NOT measured**.

**What is reported, per eps (all from `plb_measure.screening_analysis`, n = 12, same poses):** Spearman rho of ddE with ddG and its permutation p and bootstrap CI; OLS slope of ddE on ddG with
bootstrap CI; sd(ddE)/sd(ddG); the mean dE_int and its ratio to the unscreened one; the **first-order expectation** 1/eps and the ratio of the fitted slope to slope(1)/eps ("measured over first order");
Spearman between ddE(eps) and ddE(1); the number of ligands whose mean ddE changes sign against eps = 1 and the number of (ligand, pose) dE_int values that change sign; and the eps at which the fitted slope
crosses 1, by log-log interpolation between neighbouring eps (descriptive; None if not bracketed).

**Why the first-order row matters.** Dividing the charges by eps divides the first-order (electrostatic) part of dE_int by eps and the induced part by eps^2. If the response is first order, the slope
and the sd ratio fall by 1/eps by construction and the ranking does not change: the slope "moving toward 1" would then be arithmetic, not evidence that screening is the missing term.
The informative quantities are (i) whether the rank correlation changes and (ii) whether the slope deviates from slope(1)/eps.

**Pre-declared labels (descriptive, no test is run on them, because eps is not a free parameter fitted to experiment and four poses are few):**
* **"Slope moves toward 1 mechanically"**: the point slope at eps = 2, 4, 8 is below the point slope at eps = 1 and the measured/first-order ratio lies in [0.8, 1.25] at every eps.
  Reading allowed: "uniform screening rescales ddE; this shows nothing about whether screening is the missing physics".
* **"Slope response is not first order"**: the ratio lies outside [0.8, 1.25] at some eps; reported with the eps.
* **"Rank correlation changes"**: |rho(eps) - rho(1)| >= 0.15 at some eps (a shift of about 20 adjacent swaps at n = 12), else "rank correlation unchanged by uniform screening". Spearman between ddE(eps) and ddE(1) is reported
  beside it: a value of at least 0.95 at every eps means the ranking was preserved. "Screening improves tracking" may be said only if rho(eps) meets Q1's own criteria (p < 0.05, CI excluding 0, tau > 0) at some eps;
  three eps are tested without correction, so the report must call such a result exploratory.
* **Sign rule.** The issue's "19/19" sign rule is not defined in the repo that I can find; I report the flips of ddE signs and of dE_int signs against eps = 1 and the sign agreement with ddG at every eps,
  and do not claim to have re-checked the 19/19 statement.
* **Trivial-limit anchor** (issue acceptance criterion): eps = 1 must reproduce the unscreened interaction energy to 1e-9 kcal/mol; tested at the SCF level on a small molecule (`tests/test_plb_screening.py`) and
  by construction (`scale_field(.., 1.0)` returns identical numbers). Also tested: eps -> infinity gives vanishing dE_int.
* Outcomes are published whatever they are. A uniform eps is a crude proxy for screening of a point-charge field (a protein interior is heterogeneous; solvent screening is distance dependent); a result,
  positive or negative, closes at most the uniform-eps question.

## 5. Statistics (all by `smeltery.plb_measure`, seeded; changes from cdk2 listed)

x = experimental ddG (negative = tighter than the parent), y = ddE (negative = stronger interaction). A faithful ddE is positively correlated with ddG.

1. Spearman rho and Kendall tau-b of y against x; 95% percentile bootstrap CI over ligands (10,000 resamples, seed 20261010 + offsets). **Primary: rho, unscreened AMBER.**
2. **Permutation null.** 12! = 479 million relabellings cannot be enumerated, so the null is **Monte Carlo: 200,000 seeded random permutations** (the code's existing branch for n > 9). p-values are
   permutation-MC p-values (standard error about 0.001 near p = 0.05), counting the identity; **this replaces the exact enumeration of cdk2** and is labelled MC everywhere.
3. OLS slope, intercept, RMSE after the fit (n - 2), raw MAE (meaningless where the scales differ), Pearson r, sd ratio.
4. Sign agreement (binomial two-sided p against 0.5).
5. Resolution: all 78 pairs, `funnel.resolved(z = 2)` with floor 0 and 4.07. (No charge-sensitivity floor: no second charge model is run.)
6. Trivial baselines: heavy-atom count and Crippen logP of the manifest SMILES, same statistics; Spearman of ddE with each descriptor.
7. Pose noise: paired and unpaired SEM per ligand, reliability of the ddE ranking, pose-only and joint bootstrap CIs.
8. **Detectable effect at n = 12, stated before interpreting any correlation:** the two-sided 5% critical value of |rho| is **0.587** (Kendall |tau-b| about 0.42, MC); the simulated power of the Spearman permutation test for a bivariate normal truth with
   Pearson r = 0.3 / 0.5 / 0.7 / 0.8 / 0.9 is **14% / 33% / 68% / 86% / 98%** (`power_simulation`, 4,000 simulations, seed 20261010), **before** measurement noise (cdk2's reliability was 0.52, which lowers these). cdk2's n = 8 numbers were
   9 / 19 / 40 / 60 / 84%. So n = 12 gains power against r = 0.7 (40% to 68%) but a true r = 0.5 is still missed about two times in three.
9. **Pre-declared secondary:** (a) drop poses with any contact under 2.0 A; (b) **the 9 analogues from the parent's publication only** (same code, `secondary_same_source_as_parent`): rho, tau, CI, MC p. It has less power
   (critical |rho| 0.700 at n = 9, exact) and is a robustness check against cross-publication Ki error, not a rescue: it cannot change the primary label.

## 6. Pre-declared outcome language

* **"smeltery ddE tracks experiment (weakly)"**: Spearman rho > 0 with two-sided permutation p < 0.05 AND the bootstrap 95% CI of rho excludes 0 AND Kendall tau-b > 0. Even then: one target, 12 analogues, STO-3G, jittered crystal poses; "consistent with tracking", not "ranks ligands". If its |rho| point estimate also exceeds both baselines the report may say "more rank information than heavy-atom count and Crippen logP at this n" (point estimates only).
* **"smeltery ddE does not track experiment at the level this study can detect"**: anything else, with sub-labels "no evidence either way" (p >= 0.05 and CI contains 0: **absence of detection is not absence of an effect**, and this study cannot see rho below about 0.59) and
  "anti-correlated" (rho < 0 with p < 0.05, a result to be explained, not tuned away).
* Pairwise ranking is **not licensed** by any outcome here; the resolved-pair counts are reported, and "unranked" is the faithful statement where a pair is not resolved.
* The slope is read under Q2's rule only if Q1 says "tracks"; otherwise printed with the warning that a slope fitted to an uncorrelated pair is noise.
* The same-source secondary (5.9) and the screening labels (section 4) cannot upgrade or downgrade the primary label.
* The cdk2 and tyk2 answers may be shown side by side; "the two series agree/disagree" is not a pooled test and will not be worded as one.

## 7. Compute budget (cap 20 CPU-hours in all)

Measured on 2026-10-11, one core, `nice -n 10`, ferric 0.1.0rc7, box load 6 to 10 on 12 cores: parent (135 basis functions, 1013 charges) vacuum 104 CPU-s, field 108 CPU-s; `lig_ejm_48` (150 functions, the largest) 145 and 150 CPU-s;
fitted exponent 2.44 in the basis-function count. Summed over the 13 ligands, one SCF per ligand costs about 1557 CPU-s (0.43 CPU-h). CPU time moves by tens of percent with load; the caps below are enforced on measured CPU time.

* **Stage 1 (Q1 to Q4):** vacuum + unscreened AMBER SCFs, 13 ligands, 8 poses: predicted 6.9 CPU-h; `--max-cpu-hours 9.5`.
* **Stage 2 (Q5):** AMBER field divided by 2, 4, 8, on the first 4 kept poses: predicted 5.2 CPU-h; stops when the total of every SCF recorded (stage 1 included) reaches **16.5 CPU-h**. Pose-major, so the poses completed are the same for all ligands and all eps.
* Predicted total 12.1 CPU-h, plus about 0.3 CPU-h already spent (pilot 0.14 CPU-h, tests). Worst case: 16.5 + 4 SCFs in flight (about 0.2) + 0.3 = 17.0 CPU-h, under the cap. **Shrinking rule:** if stage 1 is projected (after the first full pose, from the measured cost) to
  exceed 9.5 CPU-h, the pose count is reduced (the run stops after the last whole pose that fits and the poses completed are used); nothing is allowed to exceed the cap. Nothing is left running when the work ends.
* **Non-convergence rule (fixed now; cdk2 had to improvise it).** An SCF that does not converge (ferric's `converged` False, or any error) is **not retried and not given convergence aids**. Its pose index is dropped for **every ligand, every eps and the vacuum
  energies** (`assemble --drop-pose K=reason`), so that every ligand always has the same poses; the failed SCF is recorded in the results (`dropped_failures`) and the reason in `inputs.dropped_poses`. The screened poses are then the first 4 of the remaining kept indices, as
  declared in section 4. If dropped poses leave fewer than 4 poses for stage 1, the study reports them as they are.

## 8. Second target and what else is not done

Not run. (The criteria for a later target remain those of `plb_plan.md` section 8.)

## 9. What this study will not do

No docking, no relaxation, no solvent, no COSMO or PCM, no basis or cutoff scan, no second force field (**the three-model charge comparison of #29 and CHARMM36 are not run**: the compute budget goes to the screening analysis; `FieldInteraction.systematic_floor`
is not changed and no default floor is published), no claim about the other tiers, no claim about pairwise ranking, no pooling with cdk2, no edit of the numbers in `docs/limits.md` beyond adding rows that satisfy `tests/test_limits.py`.
