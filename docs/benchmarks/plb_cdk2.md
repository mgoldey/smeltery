# smeltery's paired ddE against the PLB cdk2 experimental ddG, and the pocket charge model

First measurement of smeltery against experiment (issue #27) and the first part of issue #29. Plan, fixed and committed before
any production run: [plb_plan.md](plb_plan.md) (with amendment A1). This page reports what was measured, nothing else.
Raw data: `benchmarks/plb/results/cdk2_sto3g.json` (per-pose SCF energies, provenance); every statistic below is recomputed from it by
`scripts/analyse_plb.py` into `benchmarks/plb/results/cdk2_sto3g_analysis.json`, and `tests/test_plb_results.py` recomputes it
again and checks that the numbers quoted here are the numbers in that file.

## Answer in four lines

1. **Does smeltery's paired ddE track the experimental ddG across cdk2's ligands?** By the plan's pre-declared criteria: **not shown.**
   Primary endpoint (AMBER, n = 8 analogues): Spearman rho = +0.690, exact two-sided permutation p = 0.0694, 95% bootstrap CI
   [-0.20, +1.00]. The point estimate is positive and just below the smallest rho this n can call significant (0.738); the CI contains 0.
   Language fixed in advance: "no evidence either way; absence of detection is not absence of an effect".
2. **Does ddE carry more rank information than a trivial descriptor?** **No.** Crippen logP against ddG gives rho = +0.790 (p 0.0248,
   CI [+0.29, +1.00]), at least as good as ddE; ddE itself correlates with logP at rho = +0.75.
3. **Does the 4.5-5x overshoot survive pairing?** The spread of ddE across the ligands is about 4 times the spread of experimental ddG
   (sd ratio 4.2; slope +3.00 with 95% CI [+0.37, +7.47]); the CI contains both 1 and 4.5, and by the plan's rule the slope is not
   interpreted because question 1 is not answered "tracks". **Not resolved.**
4. **How far does swapping the pocket charge model move ddE (#29 part 1)?** Little, on this system: 0 of 8 ddE signs flip, 1 of 28 pair
   orders flips, Spearman between the AMBER and CHARMM36 ddE = +0.976, `charge_sensitivity` floor 1.26 kcal/mol (n = 8, a lower bound).
   Pose noise (mean paired SEM about 2 kcal/mol) is larger than this floor.

## What was run (exactly)

* **Series.** PLB cdk2 (PDB 1H1Q), PLB commit `fd88824f9114244f95a14b485e6d6c96c1de716d`, parent `lig_1h1q`, 9 ligands in the QM series:
  `lig_17` (3-bromo) was **excluded** because ferric 0.1.0rc7's bundled STO-3G has no bromine shells (plan amendment A1). 8 analogues, 36 pairs.
* **Field.** OpenMM 8.6.1 charges, protonation as shipped by PLB; ACE/NME caps (4) and TPO160 deleted (the tool refuses if any lies within
  16 A of the field centre; the nearest is 17.1 A), the one crystal water (5.9 A from the centre, i.e. inside the cutoff) dropped; one field
  for every ligand: 1046 atom-wise charges within 15 A of (5.2570, 44.2359, 51.2286) A, the parent's centroid. Net charge of the cropped
  sphere: +4.68 e (AMBER ff14SB, model A) and +3.17 e (CHARMM36, model B). The PQR digests, force-field file digests, residue distances and
  histidine template counts are in `inputs.prep_provenance`.
* **Poses.** Pose k = the PLB pose of each ligand moved by one shared rigid transform (<= 15 degrees, <= 0.5 A, seed 1, `default_rng([1, k])`)
  about the parent's centroid. Planned poses 0 to 5; **pose 0 (the unmoved PLB pose) was dropped for every ligand and model** because one SCF
  (lig_20, AMBER) did not converge (deviation D2), so **every number here uses poses 1 to 5: five jittered poses per ligand, no unmoved crystal
  pose.** Not docking, no relaxation.
* **QM.** ferric 0.1.0rc7 from PyPI (`ferric.__build__` commit `cddfd1aa5777`), RHF/STO-3G, convergence 1e-10/1e-8, one thread per process, 4 worker
  processes, `nice -n 10`; python 3.11.14; smeltery commit `75f5e88` (the run scripts; the assemble step, commit `b994339`, added the declared pose drop).
  dE_int = E(in field) - E(vacuum) per pose (equal to `FieldInteraction`, tested); ddE_i = mean over the 5 poses of [dE_int(i) - dE_int(parent)] at the same pose index.
* **Cost.** 10.20 CPU-h in all SCFs (plan prediction 13.2; the box was less loaded than during the timing test), plus about 0.5 CPU-h for the timing
  test, tests and analysis: well inside the 14 CPU-h cap. Nothing is left running.
* **Commands.** `scripts/plb_pocket_charges.py` (twice, `--ff amber14` and `--ff charmm36`, `--center 5.2570 44.2359 51.2286 --cutoff 15
  --delete-residues ACE,NME,TPO --drop-waters`), `scripts/run_plb_ddE.py run` twice (in the results file under `inputs.commands`), `assemble --drop-pose 0=...`,
  `scripts/analyse_plb.py`.

## Detectable effect, stated before interpreting any correlation (as #27 requires)

n = 8 ligands. The exact two-sided 5% critical value of Spearman rho is 0.738 (Kendall tau-b 0.643). For a bivariate-normal truth with Pearson
r = 0.3 / 0.5 / 0.7 / 0.8 / 0.9 the test has 9% / 19% / 40% / 60% / 84% power (4,000 simulations, seed 20261010), before measurement noise, which is not
negligible here: the reliability of the ddE ranking (between-ligand variance left after removing the mean pose noise) is 0.52. **This study could
only have seen a strong correlation.** A true correlation of 0.5 would be missed four times in five.

## Tables (generated by `scripts/analyse_plb.py`; model A = amber14 is the primary, charmm36 the second model)

n = 8 analogues, design 6 poses, completed per model {'amber14': 5, 'charmm36': 5}; bootstrap 10000 resamples, seed 20261010

### Trend of computed ddE against experimental ddG

| quantity | rho | rho 95% CI | p (exact perm., 2-sided) | tau-b | tau 95% CI | p (tau) | slope | slope 95% CI | RMSE after fit | MAE raw | sign agree | binom. p |
|---|---:|---|---:|---:|---|---:|---:|---|---:|---:|---|---:|
| smeltery ddE (amber14) | +0.690 | [-0.20, +1.00] | 0.0694 | +0.571 | [-0.17, +1.00] | 0.0610 | +3.00 | [+0.37, +7.47] | 2.75 | 4.41 | 6/8 | 0.289 |
| smeltery ddE (charmm36) | +0.738 | [-0.08, +1.00] | 0.0458 | +0.643 | [-0.08, +1.00] | 0.0312 | +3.17 | [+0.64, +6.93] | 2.46 | 4.70 | 6/8 | 0.289 |
| baseline: heavy_atoms | -0.434 | [-0.96, +0.60] | 0.2952 | -0.371 | [-0.93, +0.50] | 0.2976 | -0.38 | [-1.67, +0.47] | 1.01 | 26.58 | 3/8 | 0.727 |
| baseline: crippen_logp | +0.790 | [+0.29, +1.00] | 0.0248 | +0.618 | [+0.18, +1.00] | 0.0423 | +0.64 | [+0.27, +1.36] | 0.51 | 4.30 | 3/8 | 0.727 |

### Pose noise and what survives it

| model | mean paired SEM | mean unpaired SEM | sd of ddE across ligands | sd of exp ddG | reliability of ddE ranking | pose-only CI of rho | joint CI of rho |
|---|---:|---:|---:|---:|---:|---|---|
| amber14 | 2.07 | 3.34 | 3.61 | 0.85 | 0.52 | [+0.40, +0.88] | [-0.24, +1.00] |
| charmm36 | 2.02 | 3.21 | 3.53 | 0.85 | 0.53 | [+0.40, +0.90] | [-0.23, +1.00] |

### Per ligand (amber14), kcal/mol

| ligand | exp ddG | ddE mean | paired SEM | unpaired SEM | sd over poses |
|---|---:|---:|---:|---:|---:|
| lig_1h1r | +0.51 | +1.02 | 0.42 | 2.37 | 0.93 |
| lig_1oi9 | -1.57 | -7.87 | 3.91 | 3.52 | 8.73 |
| lig_1oiu | -0.91 | -9.23 | 3.62 | 4.74 | 8.10 |
| lig_1oiy | -1.61 | -4.33 | 2.38 | 4.40 | 5.32 |
| lig_20 | -0.52 | -8.00 | 3.73 | 4.64 | 8.33 |
| lig_21 | +0.37 | -1.70 | 1.06 | 2.42 | 2.36 |
| lig_22 | +0.33 | -2.11 | 0.55 | 2.42 | 1.24 |
| lig_26 | -0.24 | -5.71 | 0.94 | 2.25 | 2.09 |

### Pairs of the series resolved by ddE (amber14); 36 pairs

| floor | kcal/mol | resolved (z=2) | right order | wrong order | binom. p |
|---|---:|---:|---:|---:|---:|
| 0 | 0.00 | 16 | 15 | 1 | 0.001 |
| noise_floor_4.07 | 4.07 | 9 | 9 | 0 | 0.004 |
| charge_sensitivity | 1.26 | 14 | 13 | 1 | 0.002 |

Experiment: 32 pairs differ by more than 2 sigma of their reported errors, 0 by at least the 4.07 noise floor; ddE has the right sign for 28 of 36 pairs.

### Per ligand (charmm36), kcal/mol

| ligand | exp ddG | ddE mean | paired SEM | unpaired SEM | sd over poses |
|---|---:|---:|---:|---:|---:|
| lig_1h1r | +0.51 | +0.78 | 0.41 | 2.29 | 0.93 |
| lig_1oi9 | -1.57 | -8.73 | 4.12 | 4.07 | 9.21 |
| lig_1oiu | -0.91 | -8.95 | 3.21 | 4.13 | 7.18 |
| lig_1oiy | -1.61 | -5.19 | 2.44 | 4.18 | 5.46 |
| lig_20 | -0.52 | -7.60 | 3.26 | 4.03 | 7.29 |
| lig_21 | +0.37 | -2.02 | 1.07 | 2.40 | 2.39 |
| lig_22 | +0.33 | -2.48 | 0.60 | 2.30 | 1.34 |
| lig_26 | -0.24 | -6.48 | 1.09 | 2.28 | 2.44 |

### Pairs of the series resolved by ddE (charmm36); 36 pairs

| floor | kcal/mol | resolved (z=2) | right order | wrong order | binom. p |
|---|---:|---:|---:|---:|---:|
| 0 | 0.00 | 17 | 16 | 1 | 0.000 |
| noise_floor_4.07 | 4.07 | 12 | 12 | 0 | 0.000 |
| charge_sensitivity | 1.26 | 17 | 16 | 1 | 0.000 |

Experiment: 32 pairs differ by more than 2 sigma of their reported errors, 0 by at least the 4.07 noise floor; ddE has the right sign for 29 of 36 pairs.

### Charge model: same geometries, two pocket charge sets (issue #29, part 1)

models amber14 and charmm36, n = 8: sign flips of ddE 0/8; pair order flipped 1/28; Spearman between models +0.976 [+0.73, +1.00]; Kendall +0.929; charge_sensitivity floor 1.26 kcal/mol; raw dE_int shift +0.48 +/- 1.38 (sd) kcal/mol; raw dE_int Spearman across ligand-pose +0.955

The baseline rows' "MAE raw" and "sign agree" columns compare a descriptor in its own units with kcal/mol and mean nothing; ignore them. The
pair binomial p-values in the resolution tables treat 36 pairs of 8 ligands as independent, which they are not, so they are descriptive only; the
valid tests are the permutation p-values of the first table.

## Answers to the pre-declared questions

**Q1, tracking.** AMBER (primary): rho +0.690, exact two-sided p 0.0694 (one-sided in the pre-declared positive direction 0.0347), bootstrap CI
[-0.20, +1.00]; Kendall tau-b +0.571 (p 0.0610). The criteria for "tracks (weakly)" need p < 0.05 AND a CI excluding 0 AND tau > 0: **not met.** The
label is "does not track at the level this study can detect, no evidence either way". CHARMM36 (secondary) gives rho +0.738, p 0.0458, CI [-0.08, +1.00]:
it would also fail the CI criterion, and it was not the primary, so the marginal p is not a rescue; the two models rank the ligands almost identically (rho +0.976 between them),
and the two rho values differ by one swapped pair. Honest reading: the data are consistent with a positive correlation of moderate to strong size, and equally with a
weak or zero one. Sign agreement is 6 of 8 (binomial p 0.289). Resampling only the poses (ligands fixed) gives rho in [+0.40, +0.88]; resampling both, [-0.24, +1.00].

**Q2, slope.** Printed, not interpreted (rule in the plan): slope +3.00 [+0.37, +7.47], RMSE after the fit 2.75 kcal/mol, raw MAE 4.41 kcal/mol. The sd of ddE across ligands is 3.61
kcal/mol against 0.85 of experiment; removing the mean pose-noise variance leaves 2.59, a ratio near 3. These descriptive scale ratios say the computed spread is several times
the experimental one; whether that overshoot "survives pairing" in the sense of a slope cannot be said, because the slope's meaning requires a relationship that is not established.

**Q3, descriptors.** Crippen logP rho +0.790 [+0.29, +1.00] (p 0.0248) and heavy-atom count -0.434 (p 0.295). ddE does not beat logP; in this series polar substituents (OH, CH2OH, SO2NH2, CONH2)
bind more tightly in experiment and have the most negative ddE, and logP separates them as well. The three largest ddE magnitudes (lig_1oi9, lig_1oiu, lig_20, about -8 to -9 kcal/mol
against experimental -0.5 to -1.6) are polar-group ligands: ddE may be reading polarity, not the specifics of the pocket. This was not tested further. The planned bootstrap CI of a
ddE-minus-baseline difference was only conditional on ddE winning, so it was not computed.

**Q4, pairs.** Of the 36 pairs, 16 are resolved at z = 2 with floor 0 (15 in the experimental order, 1 not); with the 4.07 floor, 9 are resolved (all 9 in order); with the charge-sensitivity floor, 14 (13 in
order). ddE has the experimental sign for 28 of 36 pairs. Experiment itself separates 32 of the 36 pairs by more than 2 sigma of its reported errors but **none** by the 4.07 floor. The resolved pairs are those
containing the large-magnitude ligands whose ddE is 4 to 9 times the experimental difference, so "resolved and correctly ordered" does not mean ddE is quantitatively right, and pairwise ranking remains not licensed.
Pairing helps less than in the benzoic-acid example: mean paired SEM 2.07 against unpaired 3.34 kcal/mol (1.6x, not 10 to 17x), and for lig_1oi9 the paired SEM (3.91) is larger than the unpaired (3.52).
Per-ligand sd over poses is up to 8.7 kcal/mol.

**Q5, charge model (#29 part 1).** AMBER ff14SB against CHARMM36 on identical geometries (n = 8, 5 poses each): ddE sign flips 0/8; ligand pairs whose order flips 1/28; Spearman between models +0.976
(bootstrap CI [+0.73, +1.00]); `charge_sensitivity` floor 1.26 kcal/mol; the raw dE_int shifts by +0.48 kcal/mol on average (sd 1.38). The cropped spheres differ in net charge by 1.5 e, yet paired ddE barely moves.
For context only, the campaign's earlier pair on a different system gave 4 of 12 flips and Spearman +0.664. **What this licenses:** on cdk2 with these two force fields the charge model is a smaller limit than pose noise. **What it does not:**
a default floor for the tier (the 1.26 is a range over 8 values, so a lower bound; one system, one model pair that share structure, protonation, crop and the point-charge approximation), and nothing is said about
Lowdin/Hirshfeld/external ligand-side charge models, the three-model comparison #29 asks for. `FieldInteraction.systematic_floor` is unchanged.

## What this does not show

* That smeltery's ddE ranks ligands, or fails to: n = 8, power 40% even for r = 0.7. A positive point estimate with p = 0.07 is not evidence against a real correlation, and not evidence for one.
* That the 4.5-5x overshoot is or is not cancelled by pairing (Q2).
* Anything about other targets, other series, other tiers (docking, force field, xtb were not run), def2-svp or any basis other than STO-3G, other cutoffs, or relaxed or docked poses.
* That the pocket charge model does not matter: two similar force fields on one crop is a lower bound.
* That the jittered ensemble is a binding ensemble: it is five rigid perturbations of one crystal pose, none of them the crystal pose itself.
* Anything pairwise (see Q4).

## Limitations

Single target, 8 analogues (one lost to the basis), STO-3G (no bromine; too small for polarisation), field-only embedding with no screening or solvent, the experimental ddG derived from IC50s (not Kd; PLB's error semantics are unverified; the PLB series
spans 2.76 kcal/mol), a rigid jitter ensemble, no relaxation, an atom-wise 15 A crop that leaves the field net-charged (+4.7 or +3.2 e), a single crystal water dropped from inside the cutoff, and contacts of 1.0 to 1.5 A between a ligand atom and
a protein point charge at every pose (shared by every ligand through the common core), where STO-3G with point charges is likely over-polarised; paired ddE may cancel some of that, which was not tested.
The pre-declared secondary analysis (drop poses with any contact under 2.0 A) is vacuous: every retained pose has such a contact, and it was not re-tuned. Pose-sampling noise (5 poses) is the dominant statistical uncertainty: resampling only the poses already moves rho between +0.40 and +0.88.

## Deviations from the plan, and everything not done

* **A1** (written into the plan before any result existed): `lig_17` excluded, n = 8 (no Br in STO-3G).
* **D1**: the first launch of stage 1 was aborted after seconds (the bromine failure); the second hung (forked workers after a rayon pool existed; four idle processes, zero CPU, killed after 20 min); fixed by a spawned pool. No result from either.
* **D2**: one SCF (lig_20, pose 0, AMBER) did not converge in 470 CPU-s. The plan did not say what to do. I decided, before assembling or looking at any ddE, to drop that pose index for every ligand and model (a pose is kept for all ligands or none), rather than retry with different convergence aids. The five remaining poses are 1 to 5; the record holds the failed SCF (`results.dropped_failures`).
* **D3**: the plan expected CHARMM36 to cover only part of the poses (CPU cap); the box was quieter, so stage 2 completed all poses 1 to 5. The CPU cap was not binding.
* **D4**: the analysis table header originally said 6 poses; corrected to report the completed count. The charge-model comparison uses the 5 poses both models completed.
* **Not done**: a second target (did not fit and was not needed to answer; tyk2 is the pre-declared candidate); #28; the three-model charge comparison and any committed floor constant or default-floor test of #29; the ddE-minus-logP bootstrap; a non-STO-3G basis; the per-tier ladder of #27 beyond the one QM tier.
* **Seen before the plan**: one timing run printed the parent's pose-0 dE_int (-24.24 AMBER, -23.34 CHARMM36 kcal/mol). It is not part of the results; pose 0 was later dropped for all ligands. (The production run's recomputation of that pose was not kept in the results, so no determinism comparison is claimed.)

## Decisions for the maintainer

1. Treat #27 as **measured but unresolved at n = 8**; the next step that can change that is more ligands (a target with more charge-neutral, single-site, basis-covered analogues and reported errors), not more poses; tyk2 (12 analogues) is the pre-declared candidate, or bromine basis coverage.
2. Do not publish a `FieldInteraction` default floor from this: the 1.26 kcal/mol is one lower-bound pair; #29 needs the ligand-side models and a second system.
3. Whether to add a convergence fallback (level shift) to the run script for a failed SCF; D2 chose to drop the pose instead.
4. Whether crystal-pose close contacts of 1.0 to 1.5 A should be handled (a minimum-distance gate or a screened field) before more such runs.
