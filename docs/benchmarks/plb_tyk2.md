# smeltery's paired ddE against the PLB tyk2 experimental ddG, and a uniform-screening study of the pocket field

Second measurement of smeltery against experiment (issue #27, more ligands than cdk2) and the first measurement for issue #28 (screening). Plan, fixed and committed before any production run
(commit `ef9cae2`): [plb_tyk2_plan.md](plb_tyk2_plan.md); it repeats the cdk2 protocol ([plb_plan.md](plb_plan.md), [plb_cdk2.md](plb_cdk2.md)) with the same analysis code.
This page reports what was measured. Raw data: `benchmarks/plb/results/tyk2_sto3g.json` (per-pose SCF energies, provenance); every statistic below is recomputed from it by `scripts/analyse_plb.py` into
`benchmarks/plb/results/tyk2_sto3g_analysis.json`, and `tests/test_plb_tyk2_results.py` recomputes it again and checks that the numbers quoted here are the numbers in that file.
The cdk2 and tyk2 results are not pooled.

## Answer in four lines

1. **Does smeltery's paired ddE track the experimental ddG across tyk2's 12 analogues?** By the plan's pre-declared criteria: **not shown.** Primary endpoint (AMBER, unscreened, n = 12, 8 poses): Spearman rho = +0.252, permutation p = 0.4304 (Monte Carlo, two-sided),
   95% bootstrap CI [-0.42, +0.79]; Kendall tau-b +0.182. Language fixed in advance: "no evidence either way; absence of detection is not absence of an effect". This series does **not reproduce** cdk2's positive point estimate (+0.69, p 0.069, n = 8); its CI contains cdk2's value and also zero.
   The test could only call |rho| >= 0.587 significant and has about 68% power at a true r = 0.7 (14%, 33%, 68%, 86%, 98% at r = 0.3 to 0.9, before measurement noise, which is small here: ddE reliability 0.97), so a strong correlation would probably have been seen and a moderate one (r = 0.5, 33%) would probably not.
2. **Does ddE carry more rank information than a trivial descriptor?** **Cannot be claimed.** Heavy-atom count gives rho -0.329 (p 0.2931) and Crippen logP +0.011 (p 0.9789): neither tracks either. ddE's point estimate is numerically above both, but the plan allows that sentence only if ddE tracks, and it does not.
3. **Does the overshoot survive pairing?** **Not resolved by the plan's rule** (slope is read only if question 1 is answered "tracks"). Printed: slope +0.83, 95% CI [-0.47, +2.29] (contains 1 and not 4.5); sd(ddE)/sd(ddG) = 2.34 (2.62 against 1.12 kcal/mol; cdk2: 4.2), but ddE is only weakly related to ddG, so the spread is largely not experimental signal.
4. **Does a uniform screening of the field move things (#28)?** Dividing the charges by eps = 2, 4, 8 divides the fitted slope almost exactly by eps (0.94, 0.91 and 0.90 of the first-order 1/eps expectation) and the sd ratio from 2.42 to 1.16, 0.58, 0.29: **a mechanical rescale.**
   The slope was already below 1 at eps = 1 (+0.90 on the 4 common poses), so screening moved it **away from 1, toward 0**; the pre-declared crossing of 1 does not exist. Spearman with experiment moved from +0.280 to +0.294, +0.364, +0.392 (largest shift 0.11, under the plan's 0.15 label; none significant, p 0.21 to 0.35):
   **rank correlation unchanged by uniform screening.** Signs of ddE flipped for 1, 4 and 4 of 12 ligands (all near zero); dE_int signs flipped for 0 of 52 values. Uniform eps does not bring the point-charge field closer to experiment on this series; it says nothing about distance-dependent screening, COSMO or PCM, which were not run.

## What was run (exactly)

* **Series.** PLB tyk2 (PDB 4GIH), PLB commit `fd88824f9114244f95a14b485e6d6c96c1de716d`, parent `lig_ejm_42` (the manifest's rule: most single-site neighbours, 12 of 12), **13 ligands, none excluded** (the plan's checks: all formal charge 0, elements H C N O F Cl all covered by ferric 0.1.0rc7's STO-3G, no metals), 12 analogues, 78 pairs. Counted from the SDF, not from the READMEs.
  Ki-based ddG; 9 analogues from the parent's publication, 3 (`lig_jmc_23/27/28`) from another.
* **Field.** OpenMM 8.6.1 AMBER ff14SB charges, protonation as shipped by PLB; ACE/NME caps deleted (22.7 and 28.3 A from the centre; the guard requires more than 16 A), **two** crystal waters dropped (HOH A1318 at 9.4 A, HOH A1325 at 3.4 A, both inside the cutoff); one field for every ligand: **1013** atom-wise charges within 15 A of
  (-7.9646, -9.0535, -17.2246) A, the parent's centroid; net charge of the cropped sphere +1.40 e. The PQR digest, force-field digests, histidine counts (6 HID, 7 HIE) are in `inputs.prep_provenance`.
* **Poses.** Pose k = the PLB pose of each ligand moved by one shared rigid transform (<= 15 degrees, <= 0.5 A, seed 1); **8 poses (0 to 7), all converged, none dropped, so pose 0 (the unmoved PLB pose) is included.** Not docking, no relaxation.
* **QM.** ferric 0.1.0rc7 from PyPI (`ferric.__build__` commit `cddfd1aa5777`), RHF/STO-3G, convergence 1e-10/1e-8, one thread per process, 4 workers, `nice -n 10`; python 3.11.14; smeltery commit `ef9cae2`.
  dE_int = E(in field) - E(vacuum) per pose; ddE_i = mean over poses of [dE_int(i) - dE_int(parent)] at the same pose index.
* **Screening.** Models `amber14_eps2`, `_eps4`, `_eps8` = the same field with every charge divided by 2, 4, 8 (positions untouched), SCFs on poses 0 to 3 of all 13 ligands (the first 4 kept poses, as declared). The eps = 1 row of the screening analysis uses the same four poses.
* **Cost.** 11.16 CPU-h in all SCFs (stage 1: 208 SCFs, 6.98 CPU-h; stage 2: 156 SCFs, 4.18 CPU-h; plan prediction 12.1), plus a 0.14 CPU-h timing pilot and under one further CPU-h (tests, mutation checks, analysis; not metered) against the 20 CPU-h cap. No SCF failed. Nothing is left running.
* **Commands.** `scripts/plb_pocket_charges.py --ff amber14 --center -7.9646 -9.0535 -17.2246 --cutoff 15 --delete-residues ACE,NME --drop-waters`; `scripts/run_plb_ddE.py run` twice (in `inputs.commands`), `assemble`, `scripts/analyse_plb.py`.

## Detectable effect, stated before interpreting any correlation (as #27 requires)

n = 12 ligands. The two-sided 5% critical value of Spearman rho is 0.587 (Kendall tau-b about 0.42; Monte Carlo, because 12! cannot be enumerated). For a bivariate-normal truth with Pearson r = 0.3 / 0.5 / 0.7 / 0.8 / 0.9 the test has 14% / 33% / 68% / 86% / 98% power
(4,000 simulations, seed 20261010), before measurement noise; the reliability of the ddE ranking (between-ligand variance left after removing the mean pose noise) is 0.97, so measurement noise lowers this little. cdk2's n = 8 numbers were 9 / 19 / 40 / 60 / 84%.
**This study could see a strong correlation (r about 0.7 or more) about two times in three, and a moderate one (r about 0.5) one time in three.**

## Tables (generated by `scripts/analyse_plb.py`)

n = 12 analogues, design 8 poses, completed per model {'amber14': 8, 'amber14_eps2': 4, 'amber14_eps4': 4, 'amber14_eps8': 4}; bootstrap 10000 resamples, seed 20261010

### Trend of computed ddE against experimental ddG

| quantity | rho | rho 95% CI | p (permutation, 2-sided; exact for n <= 9, else 200,000 seeded shuffles) | tau-b | tau 95% CI | p (tau) | slope | slope 95% CI | RMSE after fit | MAE raw | sign agree | binom. p |
|---|---:|---|---:|---:|---|---:|---:|---|---:|---:|---|---:|
| smeltery ddE (amber14) | +0.252 | [-0.42, +0.79] | 0.4304 | +0.182 | [-0.31, +0.64] | 0.4593 | +0.83 | [-0.47, +2.29] | 2.57 | 2.20 | 7/12 | 0.774 |
| smeltery ddE (amber14_eps2) | +0.294 | [-0.36, +0.83] | 0.3543 | +0.212 | [-0.27, +0.68] | 0.3811 | +0.42 | [-0.26, +1.15] | 1.27 | 1.11 | 8/12 | 0.388 |
| smeltery ddE (amber14_eps4) | +0.364 | [-0.29, +0.84] | 0.2466 | +0.273 | [-0.20, +0.70] | 0.2499 | +0.20 | [-0.15, +0.58] | 0.63 | 0.80 | 7/12 | 0.774 |
| smeltery ddE (amber14_eps8) | +0.392 | [-0.27, +0.87] | 0.2121 | +0.303 | [-0.18, +0.73] | 0.1989 | +0.10 | [-0.08, +0.29] | 0.32 | 0.85 | 7/12 | 0.774 |
| baseline: heavy_atoms | -0.329 | [-0.78, +0.33] | 0.2931 | -0.205 | [-0.62, +0.30] | 0.4346 | -0.29 | [-0.82, +0.21] | 1.15 | 23.47 | 7/12 | 0.774 |
| baseline: crippen_logp | +0.011 | [-0.62, +0.64] | 0.9789 | +0.062 | [-0.44, +0.57] | 0.8415 | -0.04 | [-0.39, +0.19] | 0.57 | 4.21 | 7/12 | 0.774 |

### Pose noise and what survives it

(The amber14 row uses 8 poses; the screened rows use the 4 common poses.)

| model | mean paired SEM | mean unpaired SEM | sd of ddE across ligands | sd of exp ddG | reliability of ddE ranking | pose-only CI of rho | joint CI of rho |
|---|---:|---:|---:|---:|---:|---|---|
| amber14 | 0.38 | 1.46 | 2.62 | 1.12 | 0.97 | [+0.21, +0.42] | [-0.35, +0.82] |
| amber14_eps2 | 0.35 | 1.25 | 1.30 | 1.12 | 0.90 | [+0.06, +0.45] | [-0.38, +0.84] |
| amber14_eps4 | 0.18 | 0.63 | 0.64 | 1.12 | 0.90 | [+0.10, +0.48] | [-0.35, +0.85] |
| amber14_eps8 | 0.09 | 0.31 | 0.32 | 1.12 | 0.89 | [+0.06, +0.48] | [-0.35, +0.85] |

### Per ligand (amber14), kcal/mol

| ligand | exp ddG | ddE mean | paired SEM | unpaired SEM | sd over poses |
|---|---:|---:|---:|---:|---:|
| lig_ejm_31 | +0.24 | +0.93 | 0.17 | 1.32 | 0.48 |
| lig_ejm_43 | +1.53 | -0.36 | 0.08 | 1.23 | 0.22 |
| lig_ejm_45 | +0.23 | -3.53 | 0.46 | 1.54 | 1.31 |
| lig_ejm_46 | -1.53 | -0.25 | 0.32 | 1.41 | 0.91 |
| lig_ejm_47 | +0.09 | -1.52 | 0.37 | 1.44 | 1.05 |
| lig_ejm_48 | +0.78 | -0.58 | 0.15 | 1.27 | 0.43 |
| lig_ejm_50 | +0.81 | -4.32 | 0.50 | 1.57 | 1.42 |
| lig_ejm_54 | -0.75 | -1.92 | 0.32 | 1.42 | 0.92 |
| lig_ejm_55 | +0.58 | +2.95 | 0.34 | 1.36 | 0.96 |
| lig_jmc_23 | -1.92 | -3.06 | 0.65 | 1.67 | 1.83 |
| lig_jmc_27 | -1.50 | -7.07 | 0.99 | 1.94 | 2.79 |
| lig_jmc_28 | -1.20 | -0.81 | 0.23 | 1.36 | 0.65 |

### Pairs of the series resolved by ddE (amber14); 78 pairs

| floor | kcal/mol | resolved (z=2) | right order | wrong order | binom. p |
|---|---:|---:|---:|---:|---:|
| 0 | 0.00 | 69 | 41 | 28 | 0.148 |
| noise_floor_4.07 | 4.07 | 17 | 13 | 4 | 0.049 |

Experiment: 55 pairs differ by more than 2 sigma of their reported errors, 0 by at least the 4.07 noise floor; ddE has the right sign for 46 of 78 pairs.

Secondary (poses with any atom < 2 A from a field charge dropped): {'dropped_pose_indices': [0, 1, 2, 3, 4, 5, 6, 7], 'n_poses_kept': 0}

Secondary (only the 9 analogues measured in the parent's publication): rho -0.017 [-0.68, +0.72], p 0.9816; tau-b +0.000; slope +0.03 [-1.40, +1.64]

### Screening: the unscreened field's charges divided by eps (issue #28), 4 common poses, base model amber14

| eps | rho | p (perm., 2-sided) | rho 95% CI | slope | slope 95% CI | sd ratio | mean dE_int | dE_int / unscreened | 1/eps | slope / first-order | rho with unscreened ddE | ddE sign flips | dE_int sign flips |
|---:|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | +0.280 | 0.3790 | [-0.41, +0.81] | +0.90 | [-0.49, +2.39] | 2.42 | -18.81 | 1.000 | 1.000 | 1.00 | +1.000 | 0/12 | 0/52 |
| 2 | +0.294 | 0.3545 | [-0.36, +0.84] | +0.42 | [-0.26, +1.17] | 1.16 | -8.40 | 0.446 | 0.500 | 0.94 | +0.979 | 1/12 | 0/52 |
| 4 | +0.364 | 0.2462 | [-0.27, +0.85] | +0.20 | [-0.14, +0.56] | 0.58 | -3.95 | 0.210 | 0.250 | 0.91 | +0.902 | 4/12 | 0/52 |
| 8 | +0.392 | 0.2100 | [-0.29, +0.87] | +0.10 | [-0.08, +0.29] | 0.29 | -1.91 | 0.102 | 0.125 | 0.90 | +0.881 | 4/12 | 0/52 |

Fitted slope crosses 1 at eps = not within the range (log-log interpolation, descriptive)

The baseline rows' "MAE raw" and "sign agree" columns compare a descriptor in its own units with kcal/mol and mean nothing; ignore them. The pair binomial p-values treat 78 pairs of 12 ligands as independent, which they are not, so they are descriptive only; the
valid tests are the permutation p-values of the first table. Per-ligand tables for the screened models, the pair tables for them and every other number are in `benchmarks/plb/results/tyk2_sto3g_analysis.json`.

## Answers to the pre-declared questions

**Q1, tracking.** AMBER (primary): rho +0.252, two-sided Monte Carlo permutation p 0.4304 (one-sided in the pre-declared positive direction 0.2158), bootstrap CI [-0.42, +0.79]; Kendall tau-b +0.182 (p 0.4593). The criteria for "tracks (weakly)" need p < 0.05 AND a CI excluding 0 AND tau > 0:
**not met**, and not close. Label: "does not track at the level this study can detect, no evidence either way". Sign agreement 7 of 12 (binomial p 0.774). Resampling only the poses (ligands fixed) gives rho in [+0.21, +0.42]; resampling both, [-0.35, +0.82]: unlike cdk2, pose noise is no longer the main uncertainty (mean paired SEM 0.38 kcal/mol at 8 poses,
unpaired 1.46, pairing buys 3.8x; cdk2: 2.07 and 1.6x); **the ranking of ddE across ligands is reproducible (reliability 0.97) but it is not the experimental ranking.** Secondary, the 9 analogues from the parent's publication only: rho -0.017, p 0.9816, CI [-0.68, +0.72]
(less power; cannot change the primary label). The pre-declared secondary of dropping poses with a ligand atom within 2.0 A of a field charge is vacuous again: every pose has such a contact (closest 1.04 A) and no pose would remain; it was not re-tuned.

**Q2, slope.** Printed, not interpreted: slope +0.83, CI [-0.47, +2.29], RMSE after the fit 2.57 kcal/mol, raw MAE 2.20 kcal/mol; sd(ddE)/sd(ddG) = 2.34. A slope fitted to a relationship that is not established is noise. Of the three hypotheses, the CI excludes H_overshoot's 4.5 and contains both H_cancel's 1 and H_null's 0;
the rule forbids reading this as "cancels", and I do not.

**Q3, descriptors.** Crippen logP rho +0.011 (p 0.9789), heavy-atom count -0.329 (p 0.2931); ddE with logP -0.046, ddE with heavy-atom count -0.410. In cdk2 logP carried the rank signal (+0.79); here there is none to carry, and ddE is not simply size or lipophilicity. No claim that ddE beats the baselines is licensed because ddE does not track.

**Q4, pairs.** Of the 78 pairs, 69 are resolved at z = 2 with floor 0, **41 in the experimental order and 28 not**; with the 4.07 floor 17 are resolved (13 in order, 4 not). ddE has the experimental sign for 46 of 78 pairs (59%). Experiment itself separates 55 of the 78 pairs by more than 2 sigma of its reported errors but **none**
by the 4.07 floor. "Resolved" here means ddE differs from zero given its pose SEM, not that it is right: 28 of 69 resolved pairs are mis-ordered. Pairwise ranking is not licensed.

**Q5, screening (#28).** The pre-declared analysis, on 4 common poses and 12 ligands, is the screening table above. (i) The slope is divided by eps within 10% of first order at every eps (ratio 0.94, 0.91, 0.90, inside the plan's [0.8, 1.25]): the label **"mechanical rescale"** applies.
The plan's other label, "slope moves toward 1", does not apply for a plain reason: the slope was +0.90 at eps = 1 (4 poses; +0.83 on 8), so it falls away from 1; the interpolated crossing of 1 is None. The spread ratio sd(ddE)/sd(ddG) does fall from 2.42 through 1.16 (eps = 2) to 0.58, passing 1 between eps 2 and 4, but that is the arithmetic of dividing
charges, not evidence that screening is the missing term. (ii) **Rank correlation:** +0.280, +0.294, +0.364, +0.392 at eps 1, 2, 4, 8, none significant (p 0.3790, 0.3545, 0.2462, 0.2100; CIs all contain 0); largest shift 0.11, below the plan's 0.15, so "unchanged by uniform screening";
Spearman between ddE(eps) and ddE(1) is +0.979, +0.902, +0.881: the ranking is largely, not entirely, preserved. The creeping rise is within noise of four poses and twelve ligands and is not claimed. (iii) **Sign checks:** ddE changes sign for 1, 4 and 4 of 12 ligands (those with |ddE| near zero), dE_int for 0 of 52 values.
(iv) The issue's "19/19 sign rule" is not defined in this repository; it was not re-checked. (v) Anchors: eps = 1 equals the unscreened energy to 1e-9 kcal/mol and eps = 1e9 gives |dE_int| < 1e-4 kcal/mol (tested on a small molecule, `tests/test_plb_screening.py`). Not run: PCM (pre-fix defect, excluded by the plan), COSMO, distance-dependent dielectrics.
**Reading:** a uniform eps rescales the interaction energy and leaves the ranking about where it was; on this series it does not rescue the correlation with experiment and it cannot show that screening is or is not the missing physics.

## What this does not show

* That smeltery's ddE ranks ligands, or fails to: n = 12, 68% power at r = 0.7 and 33% at r = 0.5. The point estimate (+0.25) is positive and its CI spans zero to strong.
* That cdk2 and tyk2 disagree or agree as a pooled result: they were analysed separately, and are not pooled.
* That the overshoot is or is not cancelled by pairing (Q2), or that ddE beats a trivial descriptor (Q3).
* That the missing term is, or is not, ligand-pocket screening: only a uniform divisor of the bare field, on 4 poses, was tried.
* Anything about other targets, other tiers (docking, force field, xtb), STO-3G alternatives, other cutoffs, other charge models (CHARMM36 and the three-model comparison of #29 were not run), relaxed or docked poses.
* That the jittered ensemble is a binding ensemble: it is eight rigid perturbations of one crystal pose.
* Anything pairwise (Q4).

## Limitations

Single target, 12 analogues of one chemotype (an N-acyl or N-carbamoyl aminopyridine amide, one site changed at a time), STO-3G, field-only embedding with no solvent, Ki-based ddG from two publications (3 of 12 analogues from the second; the single-publication secondary is underpowered), a PLB error of a flat 30% (sigma about 0.25 kcal/mol), a rigid jitter ensemble, no relaxation,
an atom-wise 15 A crop that is net charged (+1.40 e), two crystal waters dropped from inside the cutoff (one 3.4 A from the centre; keeping them was not tested), and contacts as close as 1.04 A between a ligand atom and a protein point charge at every pose, where STO-3G with point charges is likely over-polarised.
Whole-protein net charge written -2 e against PLB's -3 (not investigated). The uniform-eps treatment is crude (a protein interior and bulk solvent are not one dielectric). The mean raw dE_int is -18.8 kcal/mol on the 4 common poses (unscreened); no absolute experimental reference exists for it, so the 4.5-5x absolute overshoot of #28's context is not measured here.

## Deviations from the plan, and everything not done

* No deviation of procedure. Stage 1 and stage 2 ran as declared (8 poses, caps 9.5 and 16.5 CPU-h not reached, no SCF failed, no pose dropped, so the non-convergence rule was not exercised).
* **D1** (presentation only): the table header in `scripts/analyse_plb.py` said "exact perm." for the p column; for n = 12 the p-values are Monte Carlo (200,000 shuffles, as the plan states in section 5). The header was corrected after the tables were first rendered; no number changed (the analysis JSON is byte-identical before and after).
* **D2**: the plan's "about 0.3 CPU-h already spent" was a rough figure; the pilot was 0.14 CPU-h; tests and mutation checks were not metered (well under 1 CPU-h).
* **Seen before the plan** (disclosed there): the manifest's experimental values and structures; the cdk2 results; the CPU seconds of two pilot SCFs (no energies). The eps set was chosen knowing cdk2's spread ratio of 4.2.
* **Not done**: CHARMM36 and the three-model charge comparison (#29); PCM and COSMO (#28); a second force field or basis; the ddE-minus-baseline bootstrap (conditional on ddE winning); a docked or relaxed pose set; any default floor for `FieldInteraction.systematic_floor`.

## Decisions for the maintainer

1. **#27 stays open as "measured twice, unresolved":** cdk2 rho +0.69 (p 0.069, n = 8) and tyk2 rho +0.25 (p 0.430, n = 12), no significant tracking in either, a trivial descriptor matching ddE in one. With pose noise now small (paired SEM 0.38 kcal/mol, ranking reliability 0.97) the limit is the model (field-only STO-3G on rigid poses), not the sampling and probably not the number of
   congeneric ligands: more poses will not help, and more ligands of this kind buy power only against a strong correlation that two series already suggest is not generally present (two series, so a suggestion).
2. **#28:** the uniform-eps lane is measured: negative for rank correlation, mechanical for the slope; do not close the issue (PCM, COSMO and distance-dependent screening are unmeasured). `docs/limits.md` row U1 still reads UNMEASURED; I added measured rows beside it and did not edit it.
3. Whether a minimum-distance gate or a screened field should handle the 1.0 A crystal-pose contacts before further runs (unresolved since cdk2).
4. Whether to run the three-model charge comparison (#29) on tyk2's field (a CHARMM36 field SCF for 8 poses costs about 4 CPU-h with the existing script); nothing here publishes a default floor.
