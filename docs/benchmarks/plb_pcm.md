# smeltery's paired ddE with implicit solvation (IEF-PCM) in both SCF legs, tyk2 and cdk2 (issue #28, PCM lane)

Plan, committed before any production SCF: [plb_pcm_plan.md](plb_pcm_plan.md) (with its amendment log). Runner and analysis: `scripts/run_plb_ddE.py` (`--solvent`, `alone:MODEL`),
`scripts/merge_plb_runs.py`, `scripts/analyse_plb.py`, `smeltery.plb_measure.solvent_analysis`. Results: `benchmarks/plb/results/tyk2_sto3g_pcm.json` and `cdk2_sto3g_pcm.json` (per-pose SCF energies and provenance),
analyses `*_pcm_analysis.json`. The gas-phase numbers it is compared with are the merged runs ([plb_tyk2.md](plb_tyk2.md), [plb_cdk2.md](plb_cdk2.md)); their energies enter the new files bit for bit and were not recomputed.

## Answer in four lines

1. **Water-PCM leaves the rank agreement of ddE with experiment unchanged at the level this study can detect.** tyk2, 12 analogues, 4 poses: rho +0.294 (permutation p 0.3537) with PCM against +0.280 without, on the same four poses; Delta-rho +0.014 (paired bootstrap 95% CI [+0.00, +0.11]); the two ddE rankings correlate +0.993 and no ddE changes sign.
2. **PCM ddE does not track experiment at the level detectable** (p 0.35, rho CI [-0.38, +0.83], this study cannot see a true correlation below about 0.6). Solvent 4.0 (2 poses) and cdk2 (n = 8, 2 poses) say the same: Delta-rho +0.000 in both, ddE rank correlation with the gas-phase ddE +1.000.
3. **PCM scales the interaction up, it does not screen it:** the mean dE_int moves from -18.81 to -21.84 kcal/mol (x1.16) on tyk2 and from -38.12 to -41.20 (x1.08) on cdk2, the spread of ddE against the experimental spread is about the same (2.77 against 2.42), and the fitted slope stays inside noise (+1.06, CI [-0.47, +2.88]). The pocket charges lie outside ferric's ligand-only cavity, so this tests "the ligand relaxing in a reaction field", not a solvent-screened pocket.
4. **Decision for the maintainer, not taken here:** no solvent option is added to `FieldInteraction`, no default is published, `systematic_floor` is untouched. The data give no reason to add one for ranking; they do not exclude a benefit in a model where the pocket is inside the dielectric.

## What ran

| stage | system | solvent | poses | SCFs | CPU-h | failures |
|---|---|---|---|---:|---:|---:|
| A (primary) | tyk2, 13 ligands | water (78.4) | 0 to 3 | 104 | 8.82 | 0 |
| B (secondary) | tyk2, 13 ligands | 4.0 | 0 to 1 | 52 | 5.44 | 0 |
| C (conditional) | cdk2, 9 ligands (lig_17 excluded as before) | water (78.4) | 1 and 2 | 36 | 5.65 | 0 |

dE_int(PCM) = E_PCM(ligand + AMBER ff14SB field) - E_PCM(ligand alone), both legs with `solvent=S`, the same pose geometry (digest-checked against the merged runs), the same 1013 (tyk2) or cdk2 charges (sha256-checked), RHF/STO-3G, `pcm_lebedev_order` 110, no other keyword.
ddE is the pose-mean of the paired difference against the parent, as before. Every SCF converged (max 22 iterations on tyk2); none was retried. The stored gas-phase energies were not reused as legs of the PCM dE_int.
Stage C's pre-declared rule (cumulative CPU after A and B at most 15.5 CPU-h) was met at 14.76 and cdk2 was run on the two poses the plan named; the cdk2 files' pose 0 is dropped as in the merged cdk2 result.

## Results: tyk2 (n = 12 analogues)

Primary endpoint (Spearman, AMBER field, water-PCM, poses 0 to 3), with the gas-phase and uniform-eps values on the SAME four poses (the eps rows are quoted from `tyk2_sto3g_analysis.json`):

| treatment (poses 0 to 3) | rho | p (perm., 2-sided) | slope | sd ratio | mean dE_int |
|---|---:|---:|---:|---:|---:|
| gas phase (eps = 1) | +0.280 | 0.3790 | +0.90 | 2.42 | -18.81 |
| field charges / 2 | +0.294 | 0.3545 | +0.42 | 1.16 | -8.40 |
| field charges / 4 | +0.364 | 0.2462 | +0.20 | 0.58 | -3.95 |
| field charges / 8 | +0.392 | 0.2100 | +0.10 | 0.29 | -1.91 |
| **water-PCM** | **+0.294** | **0.3537** | **+1.06** | **2.77** | **-21.84** |

(The 8-pose gas-phase primary endpoint of the merged run is rho +0.252, p 0.4304, and is not the comparison here: PCM has 4 poses.) Baselines on the same series: heavy-atom count rho -0.329, Crippen logP rho +0.011 (p 0.9789).

### Implicit solvation against the gas-phase model, same poses (issue #28)

| model | solvent | poses | rho | p | rho 95% CI | tau-b | slope | slope 95% CI | sd ratio | gas rho | Delta-rho | Delta-rho 95% CI (paired) | rho with gas ddE | mean dE_int | gas mean dE_int | ratio | ddE sign flips | dE_int sign flips | rank change | tracks | spread |
|---|---|---:|---:|---:|---|---:|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|
| amber14_pcm_water | water | 4 | +0.294 | 0.3537 | [-0.38, +0.83] | +0.212 | +1.06 | [-0.47, +2.88] | 2.77 | +0.280 | +0.014 | [+0.00, +0.11] | +0.993 | -21.84 | -18.81 | 1.161 | 0/12 | 0/52 | unchanged at the level this study can detect | does not track at the level detectable | about the same spread |
| amber14_pcm_eps4 | 4.0 | 2 | +0.154 | 0.6333 | [-0.48, +0.72] | +0.121 | +0.66 | [-0.57, +2.17] | 2.30 | +0.154 | +0.000 | [+0.00, +0.00] | +1.000 | -19.82 | -17.97 | 1.103 | 0/12 | 0/26 | unchanged at the level this study can detect | does not track at the level detectable | about the same spread |

The same-publication secondary (the 9 analogues from the parent's paper) for water-PCM gives rho -0.017 (the gas-phase 8-pose value is also -0.017); it cannot change the label. The pose-noise block is in the analysis file: the mean paired SEM of the PCM ddE is 0.74 kcal/mol at 4 poses (0.38 for the gas phase at 8).

## Results: cdk2 (n = 8 analogues, conditional Stage C)

| model | solvent | poses | rho | p | rho 95% CI | Delta-rho | Delta-rho 95% CI (paired) | rho with gas ddE | mean dE_int | gas mean dE_int | ratio | ddE sign flips | dE_int sign flips |
|---|---|---:|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|
| amber14_pcm_water | water | 2 | +0.643 | 0.0962 | [-0.16, +1.00] | +0.000 | [+0.00, +0.00] | +1.000 | -41.20 | -38.12 | 1.081 | 0/8 | 0/18 |

The merged 5-pose gas-phase rho is +0.690 (p 0.0694); on the two poses used here the gas phase gives +0.643, identical to PCM. Labels: rank change unchanged, does not track at the level detectable (n = 8 cannot see rho below about 0.74).

## Reading, with the pre-declared language

* **P1:** "rank correlation unchanged by water-PCM at the level this study can detect" (|Delta-rho| 0.014, under the 0.15 threshold). The MCL1 paper's finding (removing implicit solvation worsened ranking) is neither reproduced nor refuted: a different system, full DFT, a single structure, and here the ranking of ddE simply does not move (rank correlation with the gas-phase ddE +0.993).
* **P2:** "does not track at the level detectable", sub-label "no evidence either way". The slope is not interpreted (the plan reads it only when ddE tracks).
* **Spread:** "about the same spread" (PCM sd ratio over gas sd ratio 1.15). Nothing here says the 4.5x absolute overshoot of ferric's docstring is or is not reduced: there is no absolute experimental reference for dE_int. What can be said is the sign of the change: PCM made the mean interaction energy 16% (tyk2), 8% (cdk2) MORE negative, not less.
* **Stage B and C** are descriptive and cannot change P1/P2. Solvent 4.0 behaves like water on the two poses it has (Delta-rho +0.000, ratio 1.10 against 1.16 for water on the same poses); cdk2 behaves like tyk2.

## What the data do not support

* That PCM cannot help: with n = 12 a change in rho smaller than a few tenths cannot be established, and the ranking is nearly the same ranking (rho with gas ddE +0.993), so the answer is that this implementation does not reorder the ligands, not that solvation is irrelevant to binding.
* That the pocket is solvent-screened: the field's point charges are outside the cavity (the plan's section 0 probe: a far +1 charge's interaction with a toy molecule GREW by about 35% with PCM). No result here concerns water between ligand and pocket.
* Anything about absolute energies, the 4.5x overshoot, other basis sets, other PCM settings (the lebedev order, cavity radii), relaxed geometries, or the other tiers.
* Anything pooled across tyk2 and cdk2, or about pairwise ranking.
* Statistics on 2 poses (Stage B, C) beyond "the numbers are the same as the gas phase's".

## Deviations from the plan, and interruptions

* Pose counts are as planned (A: 4, B: 2, C: 2). All three stages were run: Stage C met its pre-declared rule (the plan expected it would probably not).
* The measured cost ratio PCM/gas was about 2.5, not the pilot's 3.0 (CPU seconds per SCF vary with the load of the shared machine), so all stages cost less than predicted: A 8.82 (predicted 10.5), B 5.44 (5.2), C 5.65 (7.9).
* The in-flight cap guard uses `>=` (stop at equality) rather than the plan's `<=` wording; it never triggered.
* Stages ran in separate cache directories and were merged by `scripts/merge_plb_runs.py` (amendment A1); the runner's git commit differs between stages A and B (a later commit added the analysis printing, not the runner), and both are recorded in the results file.
* `assemble` needed one fix after Stage C finished: a base file written before the screening study (cdk2) has no `field_eps`; it is read as 1.0. This changes no energy.
* The agent session driving the work was cut off twice by rate limits. The detached SCF processes were unaffected; no SCF was lost, repeated or retried (A3 of the amendment log).
* The planned mutation checks were run on a copy of the repository while production ran, and one survivor (a solvent model joining the screening family) led to a stronger test.
* The pre-declared secondary "drop poses with any contact under 2.0 A" is vacuous on tyk2 again (every pose has a contact; the file shows an empty kept list) and is not re-tuned.

## Mutation checks (tests written for this lane)

Made by hand on a copy of the repository, each made at least one test fail and was reverted: the assembled reference leg taken from the gas-phase vacuum SCF; `solvent` dropped from the field leg; from the alone leg; the cap guard ignoring the CPU spent elsewhere; ignoring the SCFs in flight;
a restart retrying a failed SCF; the rest of a failed pose still running; a solvent model joining the screening family (first survived, the test was strengthened to give both models `dielectric_eps` as the runner does, then killed); a solvent model taken as a second charge model; an unpaired resample in the Delta-rho CI;
the 0.15 threshold and the 0.8/1.25 spread thresholds moved; the validator comparing dE against the gas energy; the common-pose slice of the gas model; the tracks label ignoring the CI; the merge accepting different seeds or a repeated model; a wrong quoted rho or uniform-eps row in this report;
a changed seed offset of the stored analysis; the validator ignoring a corrupted alone energy.

## Cost

Production SCFs: 8.82 + 5.44 + 5.65 = **19.91 CPU-h** (zero lost: no failure, no restart). Plus the pilot (0.18 CPU-h) and the test and mutation runs, which were not metered one by one: the final full suite (1054 passed, 76 skipped, plus `experiments/`) used 0.33 CPU-h, an earlier partial suite run and the mutation runs an estimated 0.4 CPU-h, so **about 20.9 CPU-h of the 24 CPU-h cap**.
4 workers at nice 10, one thread each. The cap guard counted 0.5 CPU-h for the pilot and tests when production started.

## Decisions for the maintainer

1. Whether to add a solvent option to `FieldInteraction`: the evidence says it would not reorder ligands (and costs 2.5x). Not done in this PR.
2. Whether to test a model where the pocket is inside the dielectric (cavity including the pocket, or a distance-dependent dielectric on the field); this PCM test does not speak to it. Issue #28 stays open for distance-dependent screening and COSMO.
3. `docs/limits.md`: row L14 records the measured result; row U1 now says PCM (ligand-only cavity) is measured.
