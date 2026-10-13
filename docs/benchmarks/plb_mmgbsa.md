# A single-snapshot MM/GBSA baseline against the PLB experimental ddG, and how it compares with smeltery's paired ddE (issue #27)

Plan, committed before any series energy was computed: [plb_mmgbsa_plan.md](plb_mmgbsa_plan.md) (with its amendment log A1 to A3). This page reports what was measured.
Raw data: `benchmarks/plb/results/cdk2_mmgbsa.json` and `tyk2_mmgbsa.json` (per-ligand energy terms of complex, receptor and ligand, minimisation diagnostics, charge and
receptor provenance); every statistic below is recomputed from them, the committed manifest and the stored ddE analyses by `scripts/analyse_plb_mmgbsa.py` into
`*_mmgbsa_analysis.json`, and `tests/test_plb_mmgbsa.py` recomputes it again (floats to relative 1e-12) and checks that the numbers quoted here are the numbers in those files.

## Answer in five lines

1. **Feasibility.** The protocol ran, with no restricted licence in the path, in the optional conda environment of `docs/environments.md` (OpenMM 8.6.1, openmmforcefields 0.16.0, openff-toolkit 0.18.0, AmberTools' `sqm`/`antechamber`). No new dependency or extra was added to smeltery. It is a *minimal* MM/GBSA: one minimised snapshot per ligand, no entropy, no ensemble, a 10 A pocket shell of the receptor.
2. **cdk2 (n = 8): MM/GBSA does not track at the level this n can detect** (rho +0.714, exact p 0.0576, bootstrap CI [-0.06, +1.00]) and **cannot be distinguished from ddE** (Delta-rho = rho_MM - rho_ddE = +0.024, paired bootstrap CI [-0.43, +0.57]). Crippen logP (rho +0.790) is again at least as good as either. The sensitivity variant (Sage instead of GAFF2) gives rho +0.738, p 0.0458, CI [-0.06, +1.00]: still "does not track" because the CI contains 0.
3. **tyk2 (n = 12): MM/GBSA tracks (weakly) by the pre-declared criteria** (rho +0.888, p 0.0003, CI [+0.61, +0.99], tau-b +0.727) **and ranks the series better than ddE** (Delta-rho +0.636, paired CI [+0.09, +1.25]; the interval excludes 0 but only just). ddE had rho +0.252 here; Crippen logP +0.011. The variant agrees (rho +0.916, Delta-rho +0.664, CI [+0.09, +1.31]).
4. **The two series disagree, so the pre-declared joint language is "no pooled statement":** on cdk2 the data cannot rank ddE against the cheap baseline in either direction; on tyk2 they say the cheap baseline is better than ddE. That is the series dependence the literature reports for MM/GBSA, but this is two series and cannot separate "MM/GBSA is series dependent" from "ddE is series dependent" (or both).
5. **What this does not show:** nothing about MM/GBSA averaged over molecular dynamics, nothing about absolute binding energies (the computed spread is 1.65 to 1.85 times the experimental one on tyk2 where rank is good, and 2.5 to 3.4 times on cdk2), and nothing about pairwise ranking (experiment resolves no pair at the 4.07 kcal/mol floor).

## What was run (exactly)

* **Series.** PLB commit `fd88824f9114244f95a14b485e6d6c96c1de716d`. cdk2 (1H1Q): parent `lig_1h1q`, the ddE study's 8 analogues; tyk2 (4GIH): parent `lig_ejm_42`, 12 analogues. `lig_17` (3-bromo, cdk2) was also run (MM needs no STO-3G table) and is reported in one pre-declared descriptive row, never in the paired comparison.
* **Geometry.** The PLB pose of each ligand, unmoved (the base of the ddE study's jittered poses, not one of them), one snapshot. The provenance of those poses (crystal or modelled analogue) was not re-verified here.
* **Receptor.** AMBER ff14SB (`amber14-all.xml`), protonation as shipped, crystal waters dropped, **only the residues with an atom within 10 A of any atom of any ligand pose of the series** (cdk2: 69 of 559 residues, 1133 atoms; tyk2: 67 of 290, 1101 atoms), cut ends neither capped nor neutralised (amendment A2). The same receptor serves every ligand of a series.
* **Ligand.** Explicit-H structure from the SDF; AM1-BCC charges from `sqm` through openff-toolkit (which minimises at the AM1 level first, amendment A1); **GAFF2 (`gaff-2.11`)** (primary), or **OpenFF Sage 2.2.1** with the same charges (the one declared variant). The System's ligand charges equal the AM1-BCC charges to 0.0 e for all 46 runs.
* **Solvent.** OBC2 (OBC type II, Amber igb 5) by OpenMM's native `GBSAOBCForce`, mbondi2 radii and OBC screening parameters from OpenMM's tables, dielectrics 1 and 78.5, no salt, OpenMM's ACE surface-area term (default 2.25936 kJ/mol/nm^2, not Amber's LCPO). On the tyk2 receptor alone this force agreed with the `implicit/obc2.xml` CustomGBForce to 1.3e-6 relative; a test checks agreement on a real 6-residue fixture to 1e-4.
* **Minimisation.** Complex, protein heavy atoms restrained to their input positions (k = 10 kcal/mol/A^2), ligand and hydrogens free, CPU platform, RMS force tolerance 5 kJ/mol/nm (amendment A3), budget 5000 iterations. **All 46 runs met it** (largest final RMS force 4.98 kJ/mol/nm, in 1000 iterations); the ligand moved by at most 0.90 A RMS (cdk2) and 0.52 A (tyk2).
* **Score.** On the minimised snapshot, Reference platform (double precision), no restraint: dG = E(complex) - E(receptor) - E(ligand), each = bonded + vdW + Coulomb + GB/SA. The bonded part of dG was zero to better than 1e-6 kcal/mol in all runs (a check, refused otherwise). ddG_MM = dG(analogue) - dG(parent). **Ligand strain, receptor reorganisation, entropy and explicit water are not included.**
* **Cost.** 0.60 CPU-h in the 46 production tasks (cdk2 0.23, tyk2 0.37), 4 workers at `nice -n 10`, one OpenMM thread each. About 0.25 CPU-h more was spent on the first full-protein pilot that was killed (amendment A2), timing tests and the parent pilot: about 0.85 CPU-h in all, against the 6 CPU-h cap.

## Detectable effect

Unchanged from the ddE studies: the smallest significant two-sided |rho| is 0.738 at n = 8 and 0.587 at n = 12; power about 40% (n = 8) and 68% (n = 12) at a true Pearson r of 0.7. A true correlation of 0.5 would be missed four times in five at n = 8.

## Tables (generated by `scripts/analyse_plb_mmgbsa.py`)

### cdk2, n = 8 analogues, parent lig_1h1q (bootstrap 10000 resamples, seed 20261010)

| score | rho | rho 95% CI | p (exact perm., 2-sided) | tau-b | label (plan section 6) |
|---|---:|---|---:|---:|---|
| MM/GBSA (GAFF2, primary) | +0.714 | [-0.06, +1.00] | 0.0576 | +0.571 | does not track at the level detectable |
| MM/GBSA (Sage, variant) | +0.738 | [-0.06, +1.00] | 0.0458 | +0.643 | does not track at the level detectable |
| smeltery ddE (AMBER ff14SB, from the ddE study) | +0.690 | [-0.20, +1.00] | 0.0694 | +0.571 | does not track at the level detectable |
| Crippen logP | +0.790 | [+0.29, +1.00] | 0.0248 | +0.618 | tracks (weakly) |

| variant | Delta-rho vs ddE | 95% CI (paired) | reading | Delta-rho vs logP | 95% CI | rho(MM, ddE) | rho(MM, logP) |
|---|---:|---|---|---:|---|---:|---:|
| GAFF2 | +0.024 | [-0.43, +0.57] | cannot be distinguished at this n | -0.076 | [-0.88, +0.59] | +0.714 | +0.443 |
| Sage | +0.048 | [-0.27, +0.41] | cannot be distinguished at this n | -0.052 | [-0.73, +0.53] | +0.905 | +0.719 |

With `lig_17` (n = 9, descriptive only, no comparison with ddE exists): GAFF2 rho +0.700 [+0.16, +0.95], p 0.0433; Sage rho +0.717 [+0.17, +1.00], p 0.0369. The Br radius is OpenMM's 1.5 A default (no bromine in its mbondi2 table), and the experimental value of `lig_17` (+1.15) is the largest in the series, so this row moves the interval more than a typical ligand would; it is not read as a rescue of the n = 8 result.

### tyk2, n = 12 analogues, parent lig_ejm_42 (same settings)

| score | rho | rho 95% CI | p (perm., 2-sided, 200,000 shuffles) | tau-b | label |
|---|---:|---|---:|---:|---|
| MM/GBSA (GAFF2, primary) | +0.888 | [+0.61, +0.99] | 0.0003 | +0.727 | tracks (weakly) |
| MM/GBSA (Sage, variant) | +0.916 | [+0.64, +1.00] | 0.0001 | +0.788 | tracks (weakly) |
| smeltery ddE (AMBER ff14SB, from the ddE study) | +0.252 | [-0.42, +0.79] | 0.4304 | +0.182 | does not track at the level detectable |
| Crippen logP | +0.011 | [-0.62, +0.64] | 0.9789 | +0.062 | does not track at the level detectable |

| variant | Delta-rho vs ddE | 95% CI (paired) | reading | Delta-rho vs logP | 95% CI | rho(MM, ddE) | rho(MM, logP) |
|---|---:|---|---|---:|---|---:|---:|
| GAFF2 | +0.636 | [+0.09, +1.25] | MM/GBSA ranks this series better (95% CI excludes 0) | +0.878 | [+0.18, +1.44] | +0.517 | -0.140 |
| Sage | +0.664 | [+0.09, +1.31] | MM/GBSA ranks this series better (95% CI excludes 0) | +0.906 | [+0.19, +1.52] | +0.434 | -0.147 |

Because rank tracking is shown here, the plan allows reading the scale: GAFF2 slope of ddG_MM on experimental ddG +1.68 (95% CI [+1.24, +2.16]), sd ratio 1.85; Sage +1.43 ([+0.92, +1.96]), 1.65. The computed spread is 1.4 to 2 times the experimental one, which is not an absolute agreement and not a claim that the scale is calibrated. On cdk2 the slope is not read (rank tracking not shown): printed in the analysis file only.

### Per ligand, kcal/mol (experimental ddG, ddE pose mean from the ddE study, ddG_MM)

| series | ligand | exp ddG | ddE | ddG_MM GAFF2 | ddG_MM Sage |
|---|---|---:|---:|---:|---:|
| cdk2 | lig_1h1r | +0.51 | +1.02 | -1.64 | -1.53 |
| cdk2 | lig_1oi9 | -1.57 | -7.87 | -7.83 | -10.26 |
| cdk2 | lig_1oiu | -0.91 | -9.23 | -3.54 | -5.01 |
| cdk2 | lig_1oiy | -1.61 | -4.33 | -3.55 | -3.16 |
| cdk2 | lig_20 | -0.52 | -8.00 | -4.08 | -4.59 |
| cdk2 | lig_21 | +0.37 | -1.70 | -1.11 | -1.68 |
| cdk2 | lig_22 | +0.33 | -2.11 | -1.65 | -1.67 |
| cdk2 | lig_26 | -0.24 | -5.71 | -3.62 | -4.18 |
| cdk2 | lig_17 (no ddE) | +1.15 | n/a | -2.49 | -2.08 |
| tyk2 | lig_ejm_31 | +0.24 | +0.93 | +1.28 | +1.27 |
| tyk2 | lig_ejm_43 | +1.53 | -0.36 | +2.36 | +2.34 |
| tyk2 | lig_ejm_45 | +0.23 | -3.53 | -1.37 | -1.14 |
| tyk2 | lig_ejm_46 | -1.53 | -0.25 | -2.13 | -1.61 |
| tyk2 | lig_ejm_47 | +0.09 | -1.52 | -0.73 | -1.51 |
| tyk2 | lig_ejm_48 | +0.78 | -0.58 | -0.27 | -0.32 |
| tyk2 | lig_ejm_50 | +0.81 | -4.32 | +1.02 | +1.43 |
| tyk2 | lig_ejm_54 | -0.75 | -1.92 | -2.29 | -2.20 |
| tyk2 | lig_ejm_55 | +0.58 | +2.95 | +0.61 | +0.20 |
| tyk2 | lig_jmc_23 | -1.92 | -3.06 | -3.26 | -2.54 |
| tyk2 | lig_jmc_27 | -1.50 | -7.07 | -4.37 | -3.68 |
| tyk2 | lig_jmc_28 | -1.20 | -0.81 | -2.89 | -2.40 |

## Answers to the pre-declared questions (plan sections 5 and 6)

**Q1, does MM/GBSA track?** cdk2: no, at the level n = 8 can detect (the CI contains 0; p 0.0576 is just above 0.05, and the Sage variant's p 0.0458 does not rescue it because the CI still contains 0 and the variant was not the primary). tyk2: yes, weakly by the declared criteria (rho > 0, p < 0.05, CI excluding 0, tau-b > 0), for both ligand force fields.

**Q2, against ddE.** cdk2: cannot be distinguished (Delta-rho +0.024, CI [-0.43, +0.57]); the point estimate's sign is not interpreted. tyk2: MM/GBSA ranks the series better, the CI of Delta-rho excluding 0 at its lower end by 0.09.

**Q3, against Crippen logP (informational).** cdk2: Delta-rho -0.076 [-0.88, +0.59], indistinguishable; logP is nominally the best of the three there. tyk2: MM/GBSA better (+0.878 [+0.18, +1.44]); logP has no rank information on this series.

**Joint reading (only if both series agree on a label, otherwise the declared fallback).** The labels differ between the series (cdk2 "cannot be distinguished", tyk2 "MM/GBSA better"), so the declared sentence applies: the two series disagree, as the literature's series dependence of MM/GBSA would predict; no pooled statement.

## Descriptive, not endpoints

* **Components** (rho of each term's difference to the parent against experiment; five quantities were looked at on 8 or 12 ligands, so none is a finding). cdk2 GAFF2: vdW -0.667, Coulomb +0.976, GB/SA -0.976, vdW + Coulomb (vacuum MM) +1.000, Coulomb + GB/SA +0.857 (Sage: -0.524, +0.976, -0.976, +0.929, +0.810). tyk2 GAFF2: +0.238, +0.406, -0.378, +0.587, +0.483 (Sage: +0.126, +0.413, -0.399, +0.594, +0.476). The Coulomb and GB terms are strongly anti-correlated with each other and cancel; the total rank is not any one term. Nothing is made of the vacuum-MM +1.000.
* **Not converged:** none. The sensitivity row that drops flagged ligands was therefore not needed.

## Post hoc, NOT pre-declared (read with that in mind)

tyk2 mixes two publications (9 analogues from the parent's own paper, 3 `jmc` cyclopropyl amides from another, all three among the most strongly binding), so a two-cluster structure could inflate a rank correlation. Added after the primary result was read: restricted to the 9 analogues from the parent's publication, GAFF2 MM/GBSA rho +0.850 [+0.36, +1.00] (p 0.0061), Sage +0.917 [+0.48, +1.00] (p 0.0013), while ddE on the same 9 is -0.017. So the tyk2 result does not depend on the 3 `jmc` ligands. This is an undeclared subset analysis and is not used for any label.

## What the data do not support

* **That MM/GBSA is better than ddE in general.** One series says so, with a paired CI whose lower bound is +0.09; the other cannot say. Two series, two comparisons, no multiplicity correction, and a lower bound that close to zero: treat it as a likely but fragile difference on tyk2.
* **That ddE is worse than MM/GBSA on cdk2, or equal to it.** At n = 8 the paired interval spans about 1 rho unit. The same point estimates (+0.714 against +0.690) are not evidence of equality.
* **That this MM/GBSA is representative of the published ones.** One minimised snapshot, 10 A pocket shell, OpenMM's native OBC2 and ACE surface term, restrained heavy atoms, no entropy, AM1-BCC from an AM1-minimised conformer. Published numbers (for example PLK1: FEP rho 0.854 against MM/GBSA 0.767) use other sampling and other sets and are not compared with these.
* **Absolute numbers.** The computed spread is 2.5 (GAFF2) to 3.4 (Sage) times the experimental one on cdk2 (sd ratio, in the analysis file, not interpreted there) and 1.65 to 1.85 times on tyk2; the cdk2 ligands with the largest MM scores (lig_1oi9: -7.8 or -10.3 against experiment -1.6) are among the ligands that dominated ddE.
* **Pairwise ranking.** As in the ddE studies, experiment resolves no pair at the 4.07 kcal/mol floor.
* **The cause of the tyk2 vs cdk2 difference.** Not tested. Candidates (a ligand-force-field or pocket-polarity effect, pose quality, the experimental set mixing publications) were not separated.

## Deviations from the plan (all listed in its amendment log, A1 to A3, written before any series result was read)

* **A1.** AM1-BCC charges come from the AM1-optimised conformer started from the PLB pose, not from the PLB geometry itself.
* **A2.** After a first full-protein pilot ran 12 CPU-minutes without finishing and was killed (its energies were never read; its output was deleted), the GB force became OpenMM's native `GBSAOBCForce` (same model, about 7 times faster) and the receptor became a 10 A shell around every ligand pose of the series; the explicit TPO160 deletion and its guard went away (TPO160 lies outside the shell on cdk2).
* **A3.** The RMS-force tolerance went from 1 to 5 kJ/mol/nm after the parent pilot (45 CPU-s) stopped at 1.26 kJ/mol/nm at its iteration budget; only minimisation diagnostics of that one parent run were read, not its energies.
* **Presentation, not protocol:** the ddE and logP rows in `*_mmgbsa_analysis.json` are recomputed with the bootstrap seeds the ddE studies used (seed + 200 and seed + 101), so their intervals match the published ones; the MM/GBSA rows use seeds seed + 10 (primary) and seed + 40 (n = 9 row). The post hoc subset block (seed + 70) is the only analysis added after a result was read.
* The runner was reformatted by ruff and two unused variables removed after the production run (no logic change; the `script_sha256` in the run metadata is that of the script as run, and the assemble step gained a `receptor` key that the first assembly lacked, re-assembled from the same task log).
* The AmberTools version is not recorded in the results file (`versions` has OpenMM, openmmforcefields and openff-toolkit only); the environment had ambertools 26.0 per its conda metadata.

## Mutation checks made while writing the tests (each fails at least one test in `tests/test_plb_mmgbsa.py`)

dG = C - R + L; ddG_MM with the sign reversed (parent minus analogue); vdW taken as the whole nonbonded energy; Coulomb as nonbonded plus vdW; bonded energy left in kJ; GB solvent dielectric 1 instead of 78.5; OBC screening parameter replaced by a constant; the pocket-shell test loosened to twice the radius; Delta-rho with the operands swapped; the label rule accepting a CI lower bound of -0.5; the experiment-side relative score reversed; the analysis reading the MM scores in reversed ligand order. The first run of the bonded-energy mutation survived (no test had a non-zero bonded term); `test_energy_terms_convert_the_bonded_energy_to_kcal` was added and kills it.
