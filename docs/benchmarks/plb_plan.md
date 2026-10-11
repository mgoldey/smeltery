# Plan: smeltery's paired ddE against the PLB experimental ddG (cdk2), and the pocket charge model

Status: **PLAN, written and committed before any production run.** It fixes the design, the statistics and the
words used to report the result. Anything done differently from it is listed under "Deviations" in the report
(`docs/benchmarks/plb_cdk2.md`), with the reason. Issues: #27 (measure the paired ddE against experiment) and the first
part of #29 (charge-model systematic error). #28 (screening) is a later study and is not touched here.

## Amendment A1 (made before any production result existed; the first launch was aborted after seconds)

The first launch of stage 1 failed at once on `lig_17` (3-bromoanilino): **ferric 0.1.0rc7's bundled STO-3G has no shells for
Br (Z = 35)** (`basis error: no basis shells for Z=35 (Br) in "sto-3g"`); the other elements of the series (H, C, N, O, S, Cl)
work (tiny hydrides were run to check). I had not checked the basis coverage of the series before the first draft of this plan;
that was an omission. Nothing was computed for any ligand but the parent (see the disclosure below) when this was found. The
rule, fixed here and applying to any basis: **a ligand with an element the basis does not cover is excluded from the QM series, by
name, with the reason recorded in the results (`inputs.excluded_ligands`); the run script refuses to start if a non-excluded
ligand has such an element**. Changing the basis for one ligand would make its dE_int incomparable, and a bigger basis (def2-svp)
is not affordable (section 3.3). Consequences: **`lig_17` is excluded; the series is 9 ligands (the parent and 8 analogues), n = 8,
36 pairs**. `lig_17` is the weakest binder of the series (ddG +1.15 kcal/mol), so the experimental range of the 8 analogues is
narrower than the manifest's. The detectable effect gets worse: section 5.8 and the sample sizes below are the n = 8 values. Costs
fall by about 9% (section 7). Every number below that depends on n has been recomputed for n = 8.

## 0. What is already known, and what this study may not do

* The benchmark's own power table (`benchmarks/plb/README.md`) says **no pair in cdk2's series differs by the
  4.07 kcal/mol ddE noise floor** (0 of 45 pairs of the 10 ligands, and the largest experimental difference is 2.76 kcal/mol; the QM series without `lig_17` is 9 ligands, 36 pairs, amendment A1), so pairwise ranking
  of this series is impossible a priori, even at slope 1. The right statistics are therefore trend statistics over
  ligands (correlation, slope, a permutation null), not "does smeltery order ligand A above B".
* The campaign measured (`experiments/danuglipron/RESULTS.md`, M4 to M14) that single-pose ddE is dominated by pose
  variance (per-pose sd 28.75 kcal/mol on danuglipron; selecting one pose gives sd x sqrt 2 = 40.66; averaging n = 100
  gives SEM x sqrt 2 = 4.07). One pose per ligand would be noise, so every ligand gets a pose ensemble and the
  per-ligand pose SEM is reported.
* **No knob is tuned after seeing agreement with experiment.** Basis, cutoff, poses, jitter, charge models, seeds and
  every statistic below are fixed here. The analysis code (`src/smeltery/plb_measure.py`, `scripts/analyse_plb.py`) is
  committed, with synthetic-data tests, **before** the production results are assembled or looked at; the run's log
  prints timings only, not energies. The first look at ddE against ddG is made by that committed code.
* A null or negative result is a legitimate outcome and will be reported as such.
* **Disclosure of what has been seen.** One timing run, made before this plan was written, computed the parent `lig_1h1q`
  at pose 0 only and printed its dE_int: -24.24 kcal/mol (AMBER) and -23.34 kcal/mol (CHARMM36) (the earlier campaign pilot,
  same system, gave -24.242). No other ligand has been computed, so no ddE and no comparison with experiment has been seen.
  The production run recomputes that SCF pair; agreement with these values is a determinism check.

## 1. Questions, fixed in advance

| # | question | statistic (section 5) |
|---|---|---|
| Q1 (#27) | Does smeltery's paired ddE track the experimental ddG across the cdk2 ligands? | Spearman rho of ddE against ddG, n = 8 analogues, exact two-sided permutation p, 95% bootstrap CI over ligands; Kendall tau-b the same way |
| Q2 (#27) | Does the 4.5-5x overshoot of interaction energies survive pairing? | OLS slope of ddE on ddG with bootstrap CI, and sd(ddE)/sd(ddG); read **only if Q1's answer is "tracks"** |
| Q3 (#27) | Does ddE carry more rank information than a trivial descriptor? | the same statistics for heavy-atom count and Crippen logP against ddG |
| Q4 (#27) | How many pairs of the series does ddE actually resolve (z = 2) at the achieved pose SEM, and are the resolved ones ordered correctly? | `funnel.resolved` over all 36 pairs of the 9 QM ligands, with floor 0, 4.07 and the charge-sensitivity floor |
| Q5 (#29, part 1) | How far does swapping the pocket charge model move ddE on identical geometries? | sign flips of ddE, pairs whose order flips, Spearman between models, `gates.charge_sensitivity` floor |

"Per tier" in #27: only ONE tier is measured here, the QM `FieldInteraction` (RHF/STO-3G). Docking, force-field and
xtb tiers are not run on this series (the PLB poses are not docked), so the other rungs of the ladder are **not
measured** and the report will not say anything about them.

### Hypotheses written down before running (issue #27's note)

* **H_cancel**: pairing cancels the overshoot; then slope(ddE on ddG) is near 1 (and sd(ddE)/sd(ddG) near 1).
* **H_overshoot**: the overshoot is per-candidate and survives pairing; slope near 4 to 5.
* **H_null**: ddE carries no information about ddG; slope near 0 (not 1, and not 4.5).

These three predict different slopes (1, 4.5, 0), so the design can in principle distinguish them. Whether it does
depends on the CI width, which is only known after the run; the report states the CI and which hypotheses it excludes.
Rule: slope is interpreted only if Q1 says "tracks". If the 95% CI excludes 1 and contains 4.5, say "overshoot
survives"; if it contains 1 and excludes 4.5, "cancels"; if it contains both or neither, "not resolved".
Regressing ddE (y) on ddG (x) is right because the experimental error is small against the spread (median sigma(ddG)
0.08 kcal/mol against an sd of 0.96 kcal/mol of the nine analogues' ddG (including `lig_17`; amendment A1 removes it; the figure is recomputed in the report), mean sigma^2 0.036 against a variance of 0.92, so the attenuation of the slope is about 4%), whereas the large error is in y, which does not
bias an OLS slope.

## 2. Data

* PLB (Open Force Field protein-ligand-benchmark), commit `fd88824f9114244f95a14b485e6d6c96c1de716d`, CC BY 4.0
  (attribution in `benchmarks/plb/manifest.json`). Files fetched with `scripts/fetch_plb.py` and sha256-checked
  against the manifest (and re-checked by the run script).
* Target **cdk2** (PDB 1H1Q): 10 ligands in PLB, 9 in the QM series (`lig_17` excluded, amendment A1), all neutral, explicit hydrogens, 3D, all in the protein's frame (centroids
  within about 1 A: one consistent binding mode). Reference (parent): `lig_1h1q`, chosen by the manifest's rule, not by me.
  The 8 analogues are the sample (n = 8); the parent has ddE = ddG = 0 by definition and is not a data point.
* Experimental ddG: `R T ln(IC50_i / IC50_ref)` at 298.15 K, from the manifest (IC50 is not Kd; Cheng-Prusoff not
  applied; what PLB's `error` means is unverified). Span 2.76 kcal/mol; median sigma(ddG) 0.08.
* The run script refuses a ligand set whose SDF structure differs from the manifest's SMILES or whose sha256 differs.

## 3. Procedure

### 3.1 The pocket field (tool: `scripts/plb_pocket_charges.py`, OpenMM 8.6.1, optional dependency)

* Charges are assigned by OpenMM from the force field's own residue templates (pdb2pqr30 cannot template ACE/NME/TPO).
  Protonation is exactly as PLB ships it (Schrodinger PrepWizard, PropKa/Epik, pH 7.4); **no protonation is changed**.
  Histidines are matched by OpenMM to the force field's HID/HIE (AMBER) or HSD/HSE (CHARMM) templates from the hydrogens
  present; the matched counts are recorded.
* **Deleted:** the ACE and NME caps (4 residues) and the phosphothreonine TPO160, because no template exists (AMBER) or the
  caps are patches (CHARMM36 in OpenMM). The tool **fails loudly** if any deleted residue has an atom within
  `cutoff + 1 A` of the field centre; the measured minimum distances are TPO160 21.3 A, NME A297 17.1 A, ACE A-1 24.2 A,
  ACE B174 31.0 A, NME B434 58.9 A, all outside the 15 A sphere. **Dropped:** the single crystal water (HOH A2117),
  which lies 5.9 A from the centre, i.e. INSIDE the cutoff. That is a modelling choice, not a limitation of the tool;
  it is recorded in the provenance. *Alternative not taken:* keeping the water with its TIP3P charges; it would add a
  pocket water whose geometry was fitted to the crystal ligand, in a field-only model with no other water.
* **Field centre** = centroid (all atoms, 4 decimals) of the parent `lig_1h1q` in the PLB frame, `(5.2570, 44.2359,
  51.2286)` A. **One field for all ligands**, so the ddE of different ligands see identical charges.
  **Cutoff 15 A**, by atom (the existing `load_pocket` convention, 1046 charges). *Alternatives not taken:*
  a residue-wise crop (would keep residues whole and give a near-integer net charge) and other radii. The atom-wise sphere
  carries a net charge (AMBER +4.68 e, CHARMM +3.17 e); the campaign found truncation non-monotone (RESULTS.md), so ddE
  absolute values are not trusted and the cutoff is not tuned.
* Model A (primary, #27): **AMBER ff14SB** (`amber14-all.xml`). Model B (#29): **CHARMM36** (`charmm36.xml`).
  Why B: it is the other widely used protein force field, with an independently derived charge set (CHARMM: fits to
  water-interaction energies and condensed-phase data; AMBER: RESP to HF/6-31G* electrostatic potentials), so it is not a
  rounding of A; both assign charges to the same atoms with no ad hoc edits (same deletions, same atoms; verified by the
  per-force-field atom counts in the provenance). *Alternatives not taken:* other AMBER variants (same lineage,
  differences too small to be an independent model); pdb2pqr's PARSE/CHARMM (pdb2pqr cannot template this protein); OPLS (not in
  OpenMM's stock files); Gasteiger/ML charges (not defined for proteins). Disclosed limit: A and B share the structure, the
  protonation, the crop and the point-charge approximation, so their disagreement is a LOWER bound on charge-model error.

### 3.2 Poses

* Pose 0 is each ligand's PLB pose as shipped. Poses 1 to 5 apply, to **every** ligand, the **same** rigid transform:
  a rotation of at most 15 degrees about a random axis and a translation of at most 0.5 A per axis, about one shared pivot
  (the parent's centroid); the transform of pose k is drawn from `numpy.random.default_rng([1, k])` (seed 1). 15 degrees /
  0.5 A is the campaign's jitter. **6 poses per ligand.**
* "Paired" means exactly: pose k of every ligand is that ligand's PLB pose moved by the same rigid motion, so the
  difference of ligand i and the parent at pose k cancels what the two share (the field and the displacement). This is
  not the campaign's pairing (`PairedPoses`, which copies parent atoms into the analogue); here the ligands are crystal-
  aligned already and the shared transform keeps them aligned. The unpaired SEM is computed too, to show what pairing buys.
* This is **not docking** and relaxes nothing. A rigid jitter of a crystal pose can put an atom close to a field charge.
  Every pose's minimum ligand-atom to field-charge distance is recorded. Poses are not filtered in the primary analysis; the
  secondary analysis drops any pose index at which any ligand has an atom closer than 2.0 A to a charge.

### 3.3 Quantum chemistry

* ferric 0.1.0rc7 from PyPI (the CI wheel), RHF/STO-3G, `energy_conv` 1e-10, `density_conv` 1e-8, one thread per process,
  ligands at their SDF formal charge (all 0).
* dE_int = E_RHF(in field) - E_RHF(vacuum) per ligand and pose, the quantity `FieldInteraction` computes (a test checks that
  the run script's per-task function equals the tier on a small molecule). The vacuum SCF does not depend on the charge
  model, so it is run once per (ligand, pose) and shared by both models; this is the only difference from calling
  `FieldInteraction` once per model, and it changes no number.
* ddE_i = mean over poses of [dE_int(i, k) - dE_int(parent, k)]; its SEM is the SD of those paired differences over sqrt(6).
* STO-3G is too small a basis to be quantitative for a polarisable interaction. It is chosen because it is the only basis the
  tier has a measured cost for and the only one that fits the budget; a def2-svp run is **not** done (not measured, would
  cost far more), and basis dependence is **not** assessed.

## 4. Models and the second part of #29

The same geometries are scored under model A and model B (CHARMM36): all 60 for A, the first m whole poses of every ligand for B (m is set by the CPU cap, section 7). `gates.charge_sensitivity` is called with the
eight analogues, quantity "ddE", values = each ligand's mean paired ddE under A and under B; it returns the sign-flip count,
the Spearman and `floor = max(delta) - min(delta)`. It is valid input (the same candidates, the same geometries, two
models); **n = 8 and a range statistic underestimates a population range**, so the floor is reported as a lower bound with
that caveat. I also report the number of ligand pairs whose order flips between models (of 28), a bootstrap CI for the
between-model Spearman, and the shift of the raw dE_int. The constant `FieldInteraction.systematic_floor` is **not** changed
by this study (one system, one model pair, n = 8: not enough to publish a default floor; see the report's decisions).

## 5. Statistics (all computed by `smeltery.plb_measure`, all seeded)

x = experimental ddG (kcal/mol, negative = tighter than the parent), y = ddE (kcal/mol, negative = stronger pocket
interaction). Both are differences from the same parent, so a faithful ddE is **positively** correlated with ddG.

1. Spearman rho and Kendall tau-b of y against x; 95% percentile bootstrap CI over ligands (10,000 resamples, seed
   20261010+offsets). **Primary endpoint: Spearman rho for model A.**
2. **Exact permutation null**: all 8! = 40,320 relabellings of the ddG across ligands; p for rho, tau, Pearson r, slope
   and the sign-agreement count, two-sided on |statistic| and one-sided in the positive direction (the pre-declared direction).
   The primary p is two-sided.
3. OLS slope of ddE on ddG, intercept, **RMSE after the linear fit** (n - 2 dof), raw MAE of ddE against ddG (the issue asks
   for MAE; it is not meaningful where the scales differ and is reported with that warning), Pearson r, sd ratio.
4. Sign agreement: the number of analogues with sign(ddE) = sign(ddG), exact binomial p against 0.5.
5. Resolution: for each of the 36 pairs of the 9 ligands, the pose-by-pose paired difference is a `Measurement`;
   `funnel.resolved(z = 2)` with floor 0, 4.07 and the charge-sensitivity floor. Report resolved counts and, among the
   resolved, the number in the right experimental order. Also how many pairs experiment itself resolves (|diff| > 2 sigma).
6. Trivial baselines: heavy-atom count and Crippen logP (RDKit) of the manifest SMILES, with the same statistics, so the
   reader can see whether any correlation exceeds a descriptor. Also Spearman of ddE with each descriptor (is ddE just size?).
7. Pose noise: the paired and unpaired SEM per ligand, the between-ligand variance left after removing the mean pose
   noise (a reliability of the ddE ranking), and CIs for rho when the poses (shared index across ligands) and when both poses
   and ligands are resampled.
8. Detectable effect at the achieved n (**stated before interpreting any correlation**, as #27 requires): for n = 8 the
   exact two-sided 5% critical value is |rho| >= 0.738 (|tau-b| >= 0.643); the simulated power of the Spearman permutation
   test for a bivariate normal truth with Pearson r = 0.3 / 0.5 / 0.7 / 0.8 / 0.9 is 9% / 19% / 41% / 60% / 82% (4,000
   simulations, seed 20261010), **before** measurement noise. So a true correlation of 0.5, which would be a useful but modest
   tracking, is detected about one time in five. The measurement noise in ddE lowers this further and is reported after the run.

## 6. Pre-declared outcome language

* **"smeltery ddE tracks experiment (weakly)"**: Spearman rho > 0 with exact two-sided permutation p < 0.05 AND the
  bootstrap 95% CI of rho excludes 0 AND Kendall tau-b > 0. Even then it is one target, 8 analogues, STO-3G, a jittered
  crystal pose: "consistent with tracking", not "ranks ligands". If it also exceeds both baselines in |rho| (point
  estimate) the report may say "more rank information than heavy-atom count and Crippen logP at this n"; the
  bootstrap CI of the difference is given and will almost certainly be wide.
* **"smeltery ddE does not track experiment at the level this study can detect"**: anything else. With the sub-labels
  "no evidence either way" (p >= 0.05 and CI contains 0: **absence of detection is not absence of an effect**, and
  this study cannot see rho below about 0.74) and "anti-correlated" (rho < 0 with p < 0.05, a result to be explained, not tuned away).
* Pairwise ranking is **not licensed** by any outcome here: the fraction of resolved pairs and their correctness are
  reported, and "unranked" is the faithful statement wherever a pair is not resolved.
* The slope is read under Q2's rule only if the answer to Q1 is "tracks". Otherwise it is printed with the warning that a
  slope fitted to an uncorrelated pair is noise.
* #29: the sign-flip count, pair flips, Spearman and floor are reported as measured, with n = 8, as a **lower bound**.
  Neither "agree" nor "disagree" is a pass/fail; the campaign's 4 of 12 flips and Spearman +0.664 (from `examples/danuglipron_halogen.py`'s
  summary) are quoted for context only, on a different system.

## 7. Compute budget

**Measured cost** (2026-10-10, one core, `nice -n 10`, `OPENBLAS_NUM_THREADS=1 RAYON_NUM_THREADS=1`, ferric 0.1.0rc7 from
PyPI, RHF/STO-3G, `lig_1h1q`, 45 atoms, 141 basis functions, 1046 charges, box load 18 to 26 on 12 cores): vacuum SCF 225 CPU-s,
field SCF 232 CPU-s (AMBER) and 250 CPU-s (CHARMM36); 707 CPU-s and 1059 s wall for the three together. (The campaign pilot
on a quieter moment gave 320 CPU-s for vacuum plus one field, so CPU time moves by tens of percent with load; the cap below is
enforced on measured CPU time, not on this prediction.)

**Extrapolation** to the 9-ligand QM series (amendment A1), scaling by (nbf/141)^2.71 (the exponent `smeltery.cost` fitted for
this tier; the ligands have 141 to 166 basis functions counting 9 for S and Cl; the sum of the nine scale factors is 11.24):
vacuum plus AMBER for 9 ligands x 6 poses is 11.24 x 6 x 457 s = **8.6 CPU-h**; CHARMM36 for all 6 poses would be
11.24 x 6 x 250 s = **4.7 CPU-h**. Together 13.2 CPU-h, plus about 0.3 CPU-h already spent (timing test, test suite, the aborted
first launch): too close to the 14 CPU-h cap given that CPU time moves by tens of percent with load. So:

* **Stage 1 (primary, Q1 to Q4):** vacuum and AMBER SCFs for all 9 ligands, 6 poses, `--max-cpu-hours 9.2`, 4 workers.
* **Stage 2 (#29):** CHARMM36 field SCFs, pose-major (pose 0 for all ligands, then pose 1, ...), until the total of every SCF
  recorded (stage 1 included) reaches **12.6 CPU-h**, then stop. About 4 CPU-h remain for it at the predicted stage 1 cost,
  which is about 5 of the 6 poses (0.78 CPU-h per pose of all ligands). The number of poses B completes is therefore decided by
  the CPU cap and the load, never by a result. **The declared subset is "the first m whole poses of every ligand"**; Q5 compares
  the two models on exactly those m poses (model A is restricted to the same m for that comparison). If m < 3, #29 is reported as
  NOT measured. The vacuum SCF is shared, so stage 2 costs only the field SCF.
* Worst case total: 12.6 + the SCFs in flight when the cap hits (4 x about 5 min = 0.35 CPU-h) + 0.3 already spent = 13.3 CPU-h,
  under the cap. If stage 1 itself hits its cap before 6 poses, the primary analysis uses the poses completed (pose-major, so
  equal for every ligand), and this is reported. Nothing is left running when the work ends.
* A second target (section 8) does **not** fit in this budget and will not be run.

## 8. Second target

Not run: it does not fit the budget (section 7). The criteria are written down anyway, fixed now on the manifest's own
columns, so that a later study picks the same target without looking at results: reported errors for every ligand, one ligand
net charge across the series (a field-only STO-3G model cannot compare ions with neutrals), no metal or cofactor in the
pocket file (pde2 has Zn and Mg, tnks2 has Zn), at least 9 analogues, then the smallest cost (fewest heavy atoms). From the
suitability table that is **tyk2** (Ki with errors for 13 of 13, all neutral, 12 of 12 single-site, 21 to 25 heavy atoms,
range 3.45 kcal/mol); pde2 fails on metals, thrombin/eg5/syk/tnks2 on mixed charges, mcl1 on its charged ligands, p38 on cost.
Its prep needs the same explicit deletions and guard (its TPO/cap/metal inventory has not been checked here).

## 9. What this study will not do

No docking, no relaxation, no solvent, no def2-svp, no basis or cutoff scan, no screening (#28), no change to
`FieldInteraction.systematic_floor`, no claim about the other tiers, and no claim about pairwise ranking.
