# Plan: does implicit solvation (IEF-PCM) in the RHF SCFs change the rank agreement of smeltery's paired ddE with experiment? (issue #28, PCM lane)

Status: **PLAN, written and committed before any production SCF.** It extends the tyk2 measurement ([plb_tyk2_plan.md](plb_tyk2_plan.md), report
[plb_tyk2.md](plb_tyk2.md)) by changing ONE thing: the SCFs are run with ferric's IEF-PCM. Same ligands, same poses, same AMBER ff14SB field, same RHF/STO-3G,
same statistics code. Anything done differently from this plan is listed under "Deviations" in the report (`docs/benchmarks/plb_pcm.md`), with the reason.

## 0. Why this, what is already known, what has been seen

* The uniform-eps lane (tyk2, PR #88, `docs/limits.md` L10) divided the field's charges by eps = 2, 4, 8. It was a mechanical rescale and did not move rank
  correlation (largest shift 0.11). It says nothing about PCM, which is the other half of #28 and is row U1 ("UNMEASURED").
* ferric 0.1.0rc7's `run_rhf` docstring says that without `solvent=` "a ligand and its pocket each leave water for free" and attributes a 4.5x overshoot on a
  different system to that. A single-structure full-DFT scoring paper (arXiv 2004.08725, MCL1, 22 ligands) found that removing implicit solvation worsened
  ranking (R2 and Spearman). That is a reason to test, not a prediction for this series: different system, full DFT, a different observable.
* Current numbers (the merged runs; context, never pooled): tyk2 (n = 12) rho +0.252, permutation p 0.43 (8 poses, gas phase); on poses 0 to 3 only, rho +0.280. cdk2 (n = 8) rho +0.69, p 0.069. Both "not shown".
  The critical |rho| at n = 12 is 0.587 (`docs/limits.md` L8), so this study cannot see a true correlation below about 0.6, and a **change** in rho smaller than a few tenths cannot be established either.

### What ferric's PCM is, established by a scratch probe (not by the production code)

`ferric.run_rhf(mol, basis, solvent=S, point_charges=...)`: **the two keywords combine without error.** `solvent="water"` and `solvent=78.4` give the identical
energy (they are the same dielectric); an unknown name raises `ValueError` listing the known names (water, dmso, methanol, ethanol, acetone, dichloromethane, dcm, thf, chloroform, toluene, hexane);
a numeric solvent must be finite and > 1 (1.0 raises). `pcm_lebedev_order` defaults to 110 and is not changed. A PCM SCF costs about 3x the gas-phase one (section 7).
Probed on a toy methanol (6 atoms, STO-3G) with three made-up charges, which are **not** the production system:

* PCM(water) lowers the isolated toy's energy by 2.9 kcal/mol, and the toy's interaction with the charges changes by a few percent. The energies with a field and a cavity are not additive.
* A neutral toy with ONE far +1 charge (12 A away) has a LARGER interaction energy magnitude with PCM than without it (by about 35%), not a smaller one.

**What follows, stated before any result.** The PCM cavity is built from the ligand's atoms only; the pocket's point charges are outside the cavity, in what the continuum treats as solvent,
and enter the SCF as part of the solute's external potential. So this experiment is "the ligand relaxing in a reaction field, with the unscreened pocket potential acting on it". It is **not** a model in which the pocket charges are
screened by water between them and the ligand (in the toy, the interaction is not reduced), and it is not a model of a buried pocket. Whatever the outcome, the report may not say "solvation of the pocket was tested". It says "ferric IEF-PCM, as it combines with an
external point-charge field, was tested". I take ferric's `energy` to include the solute-solvent polarisation free energy; I did not verify that beyond the docstring.

### Disclosure of what has been seen

(a) the manifest's tyk2 and cdk2 experimental values and structures; (b) the merged gas-phase and uniform-eps results; (c) a timing pilot on the **parent at pose 0** (PCM-water, alone and in the field): CPU seconds, SCF iteration counts and convergence only
(320 s / 21 iterations alone, 313 s / 21 in the field; gas phase from the earlier pilot 104 s and 108 s); no energy of any ligand was printed or stored; (d) the toy-methanol probe above (it printed energies of a 6-atom toy, not of any series member).
No tyk2 or cdk2 PCM dE_int or ddE has been seen.

## 1. The two legs, exactly

For ligand i at pose k (the pose geometries are rigid and are the merged runs' geometries; nothing is relaxed in either leg):

* **ALONE(S)**: `run_rhf(mol_ik, STO-3G, solvent=S)`, no point charges.
* **FIELD(S)**: `run_rhf(mol_ik, STO-3G, solvent=S, point_charges=AMBER field)`, the same 1013 charges (cutoff 15 A about the parent's centroid, protein charges only, waters dropped as before).
* **dE_int(PCM, S; i, k) = [E_FIELD(S) - E_ALONE(S)] * 627.5095 kcal/mol/Eh**, with the same conv thresholds as before (energy 1e-10, density 1e-8), one thread, default `pcm_lebedev_order` 110, no other keyword.
* ddE_i = mean over poses of [dE_int(i, k) - dE_int(parent, k)], exactly as before (`funnel.paired_delta`).

Both legs carry the solvent, so **every SCF is new**: the stored vacuum and AMBER-field energies are not reused as legs of the PCM dE_int (the stored gas energies enter the new results file only as the comparison model `amber14`, bit for bit, through
`assemble --base`, which refuses the fold unless seed, jitter, basis, cutoff, pivot and the per-ligand pose-geometry digests are identical). What is reused: the pose geometries (digest-checked), the AMBER PQR (sha256-checked against the stored provenance) and the ligand set.
The anchor test (`tests/test_plb_pcm.py`) checks on a small molecule that the new runner's PCM legs equal a direct `run_rhf(..., solvent=, point_charges=)` call, that a PCM model's dE_int is NOT the gas one, and that the cited ferric behaviours above hold.

## 2. Questions, poses and series order, fixed in advance

| # | question | statistic |
|---|---|---|
| P1 (primary) | Does water-PCM change the rank agreement of ddE with experiment? | Spearman rho of ddE(PCM, 78.4, AMBER) with ddG, tyk2 n = 12 analogues, **and** the difference Delta-rho = rho(PCM) - rho(gas) on the SAME poses |
| P2 | Does PCM track experiment at all? | the tyk2 plan's Q1 criteria applied to the PCM ddE |
| P3 | Is PCM different from the uniform-eps lane? | rho(PCM) against rho of eps = 2, 4, 8 on the same poses (read from the committed `tyk2_sto3g_analysis.json`), and logP/heavy-atom baselines (same code) |

**Stage A (primary).** tyk2, solvent "water" (78.4), poses **0, 1, 2, 3** of the merged design (8 poses, seed 1, jitter 15 deg / 0.5 A), 13 ligands, ALONE and FIELD legs: 104 SCFs. The four poses are those on which gas-phase and the three uniform-eps models already exist,
so every comparison below is on identical geometries. Reduced from 8 to 4 because a PCM SCF costs 3x (section 7); this halves the pose averaging (paired SEM 0.38 at 8 poses would be about 0.54 at 4 by 1/sqrt(n) scaling; not measured at 4 for PCM).

**Stage B (declared secondary).** tyk2, solvent **4.0** (protein-like interior; a number, not a name), poses **0 and 1**, 52 SCFs. Compared with gas and with water on the same two poses. It is descriptive and cannot change P1's label. Run after Stage A regardless of A's outcome,
provided the cumulative spend after A is at most 17.0 CPU-h (it is not rescued or tuned: eps = 4.0 is the only value, and no other value is added after seeing results).

**Stage C (conditional).** cdk2 (n = 8; 9 ligands), solvent water, poses **1 and 2** (the merged cdk2 run dropped pose 0 because the gas SCF of lig_20 failed; the kept-pose list starts at 1), 36 SCFs. **Decision rule, in advance: run only if the cumulative CPU after Stages A and B is at most 15.5 CPU-h**
(predicted cost 7.9 CPU-h, so it fits only if A and B cost less than predicted). If it does not fit it is **not run, and the report says "cdk2 not measured"**; it is not shortened to one pose, and not run in place of B. Its field PQR is the cdk2 AMBER PQR (the no-water version used by the merged run), checked against the sha256 in the stored provenance.
cdk2 has n = 8 (critical |rho| 0.738), so it is descriptive.

No other series, solvent, basis, cutoff or charge model is run.

## 3. Primary endpoint and analyses (code: `smeltery.plb_measure`, seeded, the tyk2 plan's section 5 unchanged)

**Primary endpoint: Spearman rho of ddE(PCM water, AMBER) with ddG, tyk2, n = 12, poses 0 to 3.** Reported with the permutation p (Monte Carlo, 200,000 shuffles, two-sided), the 95% percentile bootstrap CI over ligands (10,000), Kendall tau-b the same way.

Also, per solvent model, on the poses it shares with the gas model: OLS slope of ddE on ddG with bootstrap CI; sd(ddE)/sd(ddG); mean dE_int and its ratio to the gas value; Spearman between ddE(PCM) and ddE(gas); ligands whose ddE changes sign; (ligand, pose) dE_int values that change sign;
**Delta-rho = rho(PCM) - rho(gas)** with a **paired bootstrap** 95% CI over ligands (10,000 resamples, the same ligand resample for both correlations); the Crippen logP and heavy-atom baselines; the pose-noise block (paired SEM, ranking reliability); the same-publication secondary (9 ligands). The comparison with the uniform-eps models uses the committed numbers for poses 0 to 3 (`tyk2_sto3g_analysis.json`, screening block), quoted, not recomputed.

## 4. Pre-declared outcome language (descriptive; the maintainers decide what follows)

* **P1 label.**
  * "**Implicit solvation improves the rank correlation**": Delta-rho >= +0.15 AND the paired-bootstrap CI of Delta-rho excludes 0.
  * "**worsens**": Delta-rho <= -0.15 AND that CI excludes 0.
  * "**a shift of at least 0.15 that this study cannot distinguish from zero**": |Delta-rho| >= 0.15 but the CI contains 0.
  * otherwise "**rank correlation unchanged by water-PCM at the level this study can detect**" (|Delta-rho| < 0.15; 0.15 is the threshold the uniform-eps lane used, about 20 adjacent swaps at n = 12).
  Spearman between ddE(PCM) and ddE(gas) is reported beside it: a value of at least 0.95 means the ranking was preserved.
* **P2 label.** "**PCM ddE tracks experiment (weakly)**" only if rho > 0 with two-sided permutation p < 0.05 AND the bootstrap CI of rho excludes 0 AND tau-b > 0; else "does not track at the level detectable" (with the sub-label "no evidence either way" when p >= 0.05 and the CI contains 0; absence of detection is not absence of an effect). The slope is interpreted only if this label is "tracks"; otherwise it is printed with the warning that a slope fitted to an uncorrelated pair is noise.
* **Spread.** sd(ddE)/sd(ddG) with PCM over the same with gas: "PCM narrows the spread" below 0.8, "widens" above 1.25, else "about the same". Descriptive: a narrower spread is not evidence of fidelity, and there is **no absolute experimental reference for dE_int**, so the 4.5x absolute overshoot quoted in ferric's docstring is **not testable here**.
* **Literature.** A "worsens" or "unchanged" result does not refute the MCL1 paper (single structure, full DFT, 22 ligands) and an "improves" result does not reproduce it; the report says which of the three it is and no more.
* The Stage B and C results, the same-publication secondary and the uniform-eps comparison cannot upgrade or downgrade the P1 or P2 label. With three solvent settings and two series nothing is corrected for multiplicity; any "improves" in B or C would be called exploratory.
* Whatever the outcome, **no default is published, `FieldInteraction.systematic_floor` is untouched, and no solvent option is added to the tier in this PR.** If the result is encouraging that is a decision for the maintainer, listed as such in the report.
* The reading limit of section 0 (point charges outside the cavity) applies to every outcome.

## 5. SCF non-convergence rule (fixed now)

An SCF that does not converge (ferric's `converged` False, or any exception) is **not retried and given no convergence aid** (no level shift, no damping change, no different guess, no larger iteration limit, no different `pcm_lebedev_order`). It is recorded in the cache with its error text and the CPU it spent.
Its pose is dropped for **every ligand and every leg of that stage** (a pose is kept for all ligands or for none, so the pairing is never broken); tasks of that pose not yet submitted are not submitted; later poses continue (the example `danuglipron_halogen.py` stops all submission, but there the poses are costlier and the cap tighter; here a pose is an independent unit that is dropped as a whole). The dropped pose is **not replaced** by another. In-flight SCFs are always collected and cached before the run exits, and a run with any failure exits nonzero
and prints the failures. The report lists each failure (ligand, pose, error). **If fewer than 3 whole poses survive in Stage A, P1 is reported "not measured" and no rank statistic is quoted as an answer.**

## 6. Scheduling, caps and the cache

Detached (`setsid nohup`), PID and log recorded, a watcher polls; **at most 4 worker processes at `nice -n 10`, one thread each** (`RAYON_NUM_THREADS=OPENBLAS_NUM_THREADS=1`). Every finished SCF is appended to a JSONL cache immediately (a kill loses only SCFs in flight; a restart skips finished ones).
The CPU cap is enforced on measured CPU time with an in-flight guard: a new SCF is submitted only if `spent + prior + 500 s * (live + 1) <= cap`, where `prior` is the CPU already spent elsewhere (the pilot and every earlier stage), so the total cannot pass the cap by more than the estimate's error.
Pose-major order (all ligands of pose 0, then pose 1, ...) so a cap stop leaves whole poses.

## 7. Compute budget: hard cap 24 CPU-hours in all, pilots and lost work included

Measured on 2026-10-12, one core, `nice -n 10`, shared box (load 18 to 58 on 12 cores): parent, 135 basis functions, 1013 charges: PCM-water ALONE 320 CPU-s, FIELD 313 CPU-s, against 104 and 108 gas: **a factor of about 3.0** (one ligand; it is assumed for the others).
Stored gas cost per pose, 13 ligands, both legs: 3140 CPU-s (tyk2), 4755 CPU-s (cdk2, 9 ligands). Predicted with PCM: tyk2 9420 CPU-s (2.62 CPU-h) per pose, cdk2 14,270 CPU-s (3.96 CPU-h) per pose.

| item | predicted CPU-h | cumulative guard (runner `--prior-cpu-hours` + `--max-cpu-hours`) |
|---|---:|---:|
| pilot (spent) | 0.18 | 0.18 |
| Stage A: tyk2 water, 4 poses | 10.5 | 11.5 |
| Stage B: tyk2 eps 4.0, 2 poses | 5.2 | 17.2 |
| Stage C: cdk2 water, 2 poses (only if cumulative <= 15.5 after B) | 7.9 | 23.5 |
| tests and analysis (small-molecule SCFs, seconds) | < 0.1 | inside the 0.5 margin below 24 |

Predicted A + B = 15.9; C therefore probably does not run, and that is the declared consequence. Wall-clock is another matter: the box is shared, so a run may take many hours of wall time at four workers; the cap is on CPU, not wall. After the first whole pose of Stage A the cost is re-projected from the measured CPU; if A is projected to exceed its guard, the run stops after the last whole pose that fits and the poses completed are used (cap rule of the tyk2 plan), and the later stages' predictions are updated, not their rules.
Nothing is left running when the work ends.

## 8. What this study will not do

No relaxation or docking, no other basis, no cutoff scan, no other charge model, no distance-dependent dielectric, no COSMO, no reparameterisation of the cavity (ferric's defaults only), no pooling of tyk2 and cdk2, no change to `FieldInteraction` or its floor, no solvent option for the tier, no
edit of existing numbers in `docs/limits.md` beyond adding a row (with a number and an existing source, `tests/test_limits.py`) and revising row U1's text to say what is now measured. No series-specific name goes into `src/`.

## Amendment log (written while the stages ran; each entry before the result it concerns was looked at)

* **A1, assembly (before any stage was assembled).** Each stage ran in its own cache directory (A: tyk2 water, B: tyk2 eps 4.0, C: cdk2 water) because a run rewrites its `meta.json` with only its own models. The tyk2 results file is assembled from the concatenation of A's and B's task caches and a merged `meta.json`
  (union of the two models' provenance), with the stored gas model folded in by `assemble --base ... --same-field-as-base`; cdk2 the same with its own base. The merge only concatenates; no energy is edited. The analysis is `scripts/analyse_plb.py` unchanged.
* **A2, measured cost (Stage A and B finished, no failure, nothing lost).** Stage A: 104 SCFs, 8.82 CPU-h (predicted 10.5; the CPU time of one SCF was 296 to 444 s against the pilot's 313 s, and the ratio to the stored gas cost is about 2.5, not 3.0). Stage B: 52 SCFs, 5.44 CPU-h (predicted 5.2). Cumulative with the 0.5 CPU-h reserved for the pilot, tests and mutation checks: 14.76 CPU-h.
  Stage C's decision rule (cumulative <= 15.5 after A and B) is therefore **met** and Stage C runs, with cumulative guard 23.5 CPU-h and the measured cost 3.3 CPU-h per cdk2 pose (about 6.7 CPU-h for the two poses). Nothing else changes.
* **A3, interruptions.** The agent session driving the work was cut off twice by a rate limit. The detached SCF processes were not affected (Stage A completed all 104 SCFs and Stage B all 52 with zero errors; the logs have no guard stop, failure or restart), and no SCF was lost or repeated.
