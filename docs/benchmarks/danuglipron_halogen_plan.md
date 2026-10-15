# Plan: the danuglipron halogen scan, run end to end through the funnel (issue #6)

Status: **PLAN, written and committed before any production run.** The example (`examples/danuglipron_halogen.py`) and its
fast tests exist; what has never happened is the run itself. This is a **new measurement** (ferric RHF/STO-3G in the 7LCJ pocket
field), not a reproduction of the campaign, whose per-analogue values came from a hand-charged classical probe and are not
recoverable (issue #6 amendment). If the numbers do not look like the campaign's, that is the result; nothing is tuned.
Anything done differently from this plan is listed under "Deviations" in the report (`docs/benchmarks/danuglipron_halogen.md`).

## 0. What has been seen before this plan

* The issue text and amendment, the campaign's summary ranges (rho 0.78 to 0.9997, CH3 SEM 1.0 to 3.5 kcal/mol, self-anchor +0.31,
  4/12 sign flips, Spearman +0.664), and `experiments/danuglipron/RESULTS.md`.
* **Two timing pilots, CPU seconds only; the energies were not printed or stored** (the pilot script discards them):
  parent pose 0 (71 atoms): **1207 CPU-s** (vacuum + field SCF); `bnz-C36-CH3` pose 0 (74 atoms): **1873 CPU-s**. Both with the
  full 6458-charge field, `nice -n 10`, one thread, box load 7 to 11 on 12 cores. Total pilot cost 3080 CPU-s = **0.86 CPU-h**,
  charged against the cap.
* **One failure found while preparing the pilot, before any energy existed:** with the tier's default `mcs_timeout_s = 10`,
  `PairedPoses` raises `NoCommonCoreError` ("MCS search timed out") for **9 of the 12 analogues** (every `pyr-*` and `bnz-*`
  one; it hit the 10 s limit on each, so it is not load noise). The exact MCS finishes in 2 s (bzim sites) to 11 s and 73 s on
  the two slow cases tried (`pyr-C28-F`, `bnz-C36-CH3`). The example now passes `mcs_timeout_s = 900` (`MCS_TIMEOUT_S`). The
  search is exact when it completes, so this changes only whether it completes; it is a tier setting and appears in the run
  record. The 12-analogue set is the example's a-priori one (sites fixed before any energy was computed), and it is **not**
  known to be the campaign's set.
* The geometries (cryo-EM conformer, relabelled to the SMILES atom order) and the field (7LCJ pocket, pdb2pqr 3.7.1 AMBER,
  **no distance cutoff: 6458 charges**) were inspected, not scored.

## 1. What is run

| item | value |
|---|---|
| Tier | `smeltery.tiers.FieldInteraction` (RHF/STO-3G, `energy_conv` 1e-10, `density_conv` 1e-8): dE_int = E(in field) - E(vacuum) per pose |
| Engine | **ferric from PyPI** (`uv run --no-sources`), expected 0.1.0rc7; `/home/matt/qc/ferric` is not touched |
| Field | `load_pocket(7LCJ_pocket.pdb, ff="AMBER")`, 6458 point charges, no cutoff |
| Parent | danuglipron as the neutral acid (the SMILES carries no formal charge, so the tier scores charge 0; the campaign scored the carboxylate; disclosed in the record) |
| Analogues | the example's 12: F, Cl, CH3 at four aromatic CH sites (`bzim-C9`, `pyr-C28`, `bnz-C34`, `bnz-C36`) |
| Poses | **3** per molecule. Pose 0 = the cryo-EM bound conformer; poses 1 and 2 = seeded rigid jitters (<= 15 deg, <= 0.5 A), `PairedPoses` default seed **20261005**. Analogue atoms mapped to the parent are exact copies of the parent pose. Not docked, not relaxed |
| Self-pair | `parent-self`, same SMILES and same coordinates, scored as a separate candidate (a real SCF per pose) |
| Second charge model | **NOT run** (CHARMM would add 13 SCFs per pose; see section 3) |
| Workers | 4 processes, `nice -n 10`, `RAYON_NUM_THREADS=1`, `OPENBLAS_NUM_THREADS=1` |

## 2. Cost, extrapolation and the pose count

Mean cost per SCF pair (vacuum + field) from the two pilots: 1207 s (parent, 71 atoms), 1873 s (CH3 analogue, 74 atoms, a
single measurement under higher load). Taking the F and Cl analogues (72 atoms) at about 1500 s, CH3 at 1873 s:

* one pose index of the 14 molecules (parent, 12 analogues, self-pair): 2 x 1207 + 4 x 1873 + 8 x 1500 = **about 21,900 CPU-s = 6.1 CPU-h**.
* 6 poses (the example's default): **36.5 CPU-h, over the cap** (the issue's ~26 CPU-h figure is not reproduced by these pilots; I do not know what it assumed).
* 5 poses: 30.4 CPU-h, over the cap. 4 poses: 24.3 CPU-h + 0.86 CPU-h pilot = 25.2 CPU-h against the 26 CPU-h hard cap, a margin under 4%
  while CPU time on this shared box moves by tens of percent and up to 4 in-flight SCFs (about 2 CPU-h) can finish past a stop: **rejected, it would
  more likely than not breach the cap or waste a partial fourth pose.**
* **3 poses: 18.3 CPU-h + 0.86 CPU-h pilot = 19.2 CPU-h**, a margin of 6.8 CPU-h (26%).

Therefore:

* **Pose count: 3** (pose 0 = the bound conformer, poses 1 and 2 jittered), declared as the reduction from the example's default of 6. It is not
  extended to a fourth pose even if the budget turns out to allow it: the plan is not changed after seeing cost.
* The run is invoked with `--poses 3 --cap-cpu-hours 23` (26 minus the pilot, minus about 2 CPU-h for in-flight SCFs): each SCF records its CPU
  seconds, work is pose-major with at most 4 SCFs in flight, and the pass stops when the CPU spent reaches the cap (`CpuCapReached`). If it does
  stop, the report uses the whole poses completed for all 14 molecules (`--poses 2` reruns free from the cache), and says the run is short of
  its declared pose count.
* Nothing is left running when the work ends; the cache and the log are committed with the results.

**What 3 poses mean statistically.** A paired SEM from n = 3 has 2 degrees of freedom: it is itself uncertain by roughly +-50% or more, and a
per-analogue rho between the parent's and the analogue's per-pose dE_int over 3 points is almost uninformative (|rho| near 1 is easy to get by chance). Both are reported as measured;
no confidence statement is made about them.

## 3. The charge-sensitivity floor (declared, not invented)

`FieldInteraction.systematic_floor("dE_int")` is **UNMEASURED** (`docs/tiers/field_interaction.md`), and `funnel.cut(tier=...)` refuses
this tier for that reason. The cut here therefore takes an explicit, declared floor:

* **Value: 1.26 kcal/mol**, the larger of the two measured pocket-side charge-model sensitivities in `docs/limits.md`: L6 (PLB cdk2,
  AMBER ff14SB vs CHARMM36, 1.26) and L11 (PLB tyk2, 0.67). Both are **lower bounds measured on other targets** with other fields; neither
  is a floor for danuglipron in 7LCJ, and the example's output and record say so. Using the larger of the two is a choice made
  here, before any result, to make "unranked" harder to avoid, not easier.
* The CHARMM36 pass on this system is **not run**: it would add about 13 SCFs per pose which the cap does not
  allow at the pose count the cap allows (it would cost about 5 CPU-h per pose). Consequently **the "4/12 sign flips" finding is NOT tested** and no sign-flip count or inter-model Spearman is
  reported. The example supports the pass (`--skip-model-b` off); it was not paid for.
* The campaign's 4/12 was measured with a classical probe and hand charges, on a different quantity. It would not have been a valid
  target for this model anyway.

## 4. Analyses and how each acceptance criterion is judged (all from the example's own output and its RunRecord)

All quantities are kcal/mol, negative = stronger interaction. ddE_i = mean over poses of [dE_int(analogue, k) - dE_int(parent, k)].

1. **Self-anchor and non-zero field.** PASS iff the parent-vs-parent-self paired difference is **exactly 0.0 on every pose** (`==`, no tolerance)
   AND every parent pose has |dE_int| > 1.0 kcal/mol (the threshold the example asserts; a field that does nothing would give 0). FAIL otherwise, reported as found.
   The campaign's +0.31 came from re-embedding the analogue; smeltery copies mapped atoms, so 0.0 is expected by construction and is
   **not** an independent validation of the physics, only of the pairing code and ferric's determinism.
2. **Tabulated paired ddE with SEM; compared with the campaign only as ranges.** The table gives, per analogue, paired ddE, paired SEM. The per-analogue
   campaign values are not recoverable (`CAMPAIGN_RECORDED` stays empty), so per-analogue agreement within SEM is reported **UNVERIFIED, not
   passed and not failed**. The ranges are reported as counts inside and outside, and the outside names, with no pass/fail wording and no tolerance:
   * **rho**: the Pearson correlation, across poses, between the analogue's and the parent's dE_int; range [0.78, 0.9997], 12 values.
   * **CH3 SEM**: the paired SEM of the four CH3 analogues; range [1.0, 3.5], 4 values.
   The campaign ranges came from a different model (classical probe), so "inside" is not agreement and "outside" is not a failure of this run.
3. **Pairing improves on unpaired.** PASS iff **paired SEM < unpaired SEM for all 12** analogues (`funnel.unpaired_delta`, independent
   ensembles). The ratio unpaired/paired is printed for each. Any analogue with paired SEM >= unpaired SEM is named and the criterion is
   FAILED; no pose or analogue is dropped to repair it. With n = 3 the SEMs are very noisy; the criterion is judged as stated, with the
   caveat attached.
4. **Cut output.** `funnel.cut(paired, keep=3, z=2.0, floor=1.26)` is run. The cut is reported as **"unranked"** for every adjacent pair (in
   ddE order) whose gap is <= the floor, and the example asserts the cut puts no such pair in different groups. The exact
   sentences by outcome:
   * all 11 adjacent gaps <= floor: "the cut cannot rank any analogue at the declared (lower-bound) floor; the order of the table is not a ranking";
   * some gaps above the floor: "ranked only across the gaps larger than the floor, and only conditionally on a lower-bound floor; the within-group order is unranked", naming the groups;
   * if `unranked_at_boundary` is true for keep = 3: "nothing is dropped: the keep = 3 boundary falls inside a group".
   The part of the criterion that says "matching the measured 4/12 sign-flip finding" is **not tested** (section 3) and the report says so.
5. **Run record.** PASS iff the RunRecord's `ferric.commit` is named with provenance beginning VERIFIED or INFERRED (asserted by the example).
   The version, commit and provenance are quoted verbatim from the file. The expectation is a PyPI install (`0.1.0rc7`); whatever the
   record says is what is reported.

## 5. Non-convergence and failures (fixed now)

An SCF that fails (non-convergence or any error) is **not retried and not given convergence aids**. The pass stops (nothing further is
submitted), the failing molecule and pose are reported, and the analysis uses only the whole poses that completed for all 14 molecules
before the failing pose index (rerun with a smaller `--poses`, free from the cache). If that leaves fewer than 2 poses, the run is reported
as not completed and criteria 2 to 4 as not measured.

## 6. What this run will not do, and what no outcome licenses

* No tuning: not the sites, the substituents, the jitter, the seed, the field, the floor, the keep, or z after seeing any number.
* No claim of agreement with, or reproduction of, the campaign. No claim about experiment: there is no experimental ddG in this study.
* No binding free energy: dE_int is a single-geometry electronic interaction energy at RHF/STO-3G, without dispersion, solvation,
  entropy, relaxation, or ligand ionization (neutral acid, not the carboxylate).
* No ranking of analogues finer than the floor allows, and no statement about the CHARMM sensitivity of this system.
* No new row in `docs/limits.md` without a number and an existing source (`tests/test_limits.py`); a row, if added, is for the measured pose
  noise of the paired ddE on this system (its mean paired SEM and the unpaired/paired ratio), sourced to the committed results file.

## Amendment A1 (written after the first run failed, before any new SCF)

**What happened.** The first production run (3 poses, cap 23 CPU-h for the call) stopped with `pyr-C28-F pose 0: SCF did not converge`
(`docs/benchmarks/danuglipron_halogen.md`). Section 5 of this plan was followed: no retry, no aids, and with the failing index 0 no whole
pose existed, so the first run is reported as not completed. Seven pose-0 SCFs had finished (parent, `bzim-C9-F/Cl/CH3`, `pyr-C28-CH3/Cl`,
`bnz-C34-CH3`). No ddE or SEM exists from them or from any other source: **this amendment is decided before any ddE has been computed.**

**Changes (the analyses, outcome language and judging of section 4 are otherwise unchanged).**

1. **Pose 0 is dropped for every molecule**, because one pose-0 SCF did not converge (the tyk2 and cdk2 studies' convention: a pose index is dropped for
   all molecules, never for one). The 7 cached pose-0 SCFs stay in the cache file and are **excluded from every statistic**. The run uses the
   two seeded jitters, **pose indices 1 and 2**, for all 14 molecules (the same geometries as in the first run's plan: seed 20261005, <= 15 deg and <= 0.5 A rigid
   jitters of the cryo-EM conformer; the bound conformer itself is no longer scored). Implemented as `--skip-first-poses 1 --poses 2`.
2. **Pose count 2, not 3** (cost, section 3 below). **Total cap raised from 26 to 30 CPU-h**, a user-side change made because the issue's ~26 CPU-h estimate
   proved low (the plan's own cost model, built on two pilots, underestimated the mean SCF; see 3).
3. **Cost accounting.** Spent so far, upper bound: 0.86 (pilots) + 3.65 (cached SCFs) + up to 10.0 (SCFs in flight when the first run stopped, unmetered because the pool discarded
   them: 4 workers x 3.41 h wall minus the cached CPU) = **up to 14.6 CPU-h**. Remaining under the 30 CPU-h cap: **15.4 CPU-h = 55,440 CPU-s**. Cost model: the 7 cached SCFs averaged
   1879 s (range 1182 to 3485; the pyridine-site `pyr-C28-Cl` was 3485), so 28 SCFs (14 molecules x 2 poses) cost about **52,600 CPU-s = 14.6 CPU-h**, a margin of
   about 0.8 CPU-h (5%). That is thin and is stated as such.
4. **Cap enforcement (new in the example).** `--cap-cpu-hours 15.4 --est-scf-cpu-s 2500`: a new SCF is submitted only if the CPU spent by the call plus 2500 s for each SCF in flight and for the new
   one stays within the cap; if work remains when that stops submission the run stops cleanly with `CpuCapReached`, every finished SCF cached. The 2500 s estimate is deliberately above the mean so
   the tail of the queue slows rather than overruns. Because the molecules are scheduled pose-major (parent, 12 analogues, self-pair; pose 1 then pose 2), a cap stop can leave the last
   molecules of pose 2 (the self-pair last) unrun; the report would then say which criteria are unmeasured, not infer them.
5. **Scheduler fix (the lost work).** A worker that fails no longer discards the other in-flight results: the worker returns the error and its CPU, the failure is cached as failed (and is
   **never retried** by later calls), nothing further is submitted, the in-flight SCFs are collected and cached, then `ScfFailed` is raised. The rule of section 5 is unchanged: a failed SCF is not retried and not helped; if a
   jittered pose fails for any molecule, that is reported plainly, the pose index is not silently dropped, and the plan's "fewer than 2 whole poses" clause applies.

**What 2 poses support (stated now).**

* A paired SEM from n = 2 has **1 degree of freedom** (it is |d1 - d2| / 2, with a 95% t multiplier of 12.7). It is a measure of the spread of two numbers, not an estimate of a standard error one may attach
  a confidence interval to. Criterion 3 (paired SEM < unpaired SEM for every analogue, ratio printed) is judged as written, with this attached; a pass on 2 poses means the arithmetic holds on these two poses, not that pairing was shown to help.
* **rho across poses is +1 or -1 by construction with 2 poses** (any two distinct points are perfectly correlated), so the comparison with the campaign's rho range [0.78, 0.9997] is printed
  but is **vacuous** (a value of 1.0 is "outside" only because it exceeds 0.9997) and is reported as uninformative, not as a finding.
* The CH3 paired SEM comparison with [1.0, 3.5] is printed as inside/outside and carries the 1-dof caveat. The cut (section 4, criterion 4) is judged as written from the means and the declared
  1.26 kcal/mol floor; the two-pose means inherit the same weakness, so any ordering it reports is conditional.
* Nothing from this run supports a ranking, an agreement with the campaign, or a statement about experiment.

**Seen since the plan:** the first run's CPU times (above), that `pyr-C28-F` pose 0 does not converge, and that the pyridine-site SCFs are the slow ones. No energy or ddE.

## Amendment A2 (written after the second run failed and before any new SCF)

**What happened and what was decided.** Run 2 left 3 jittered-pose SCFs unconverged (`pyr-C28-F`, `pyr-C28-Cl`, `bzim-C9-F`) and 4 converged (parent, `bzim-C9-Cl`, `bzim-C9-CH3`,
`pyr-C28-CH3`). The project owner read ferric issue #410 as competing convergence thresholds against the numerical noise floor and directed: **raise the energy
threshold and move forward.** That is a deliberate change of treatment, made on that instruction and not after seeing any ddE (none exists).

**Changes (everything else in the plan and A1 stands).**

1. **`energy_conv` is raised from 1e-10 to 1e-6 Hartree** for every SCF run from now on (`--energy-conv 1e-6`; `density_conv` stays 1e-8; no other convergence aid). 1e-6 Ha is
   6.3e-4 kcal/mol, about 2000 times below the declared 1.26 kcal/mol floor, so it cannot move a dE_int that the study treats as resolved. It is one value, chosen now, not scanned.
2. **The cache key now includes the threshold**, so the three failures cached at 1e-10 are not failures at 1e-6 and get **one** attempt at the new threshold. A failure at 1e-6 is final: no
   retry, no further loosening, the study reports it and stops (the plan's section 5 rule, unchanged).
3. **The 4 jittered-pose SCFs that converged at 1e-10 are reused as they are**: a result converged to 1e-10 satisfies a 1e-6 criterion. The mixture of thresholds in the record is therefore
   {1e-10 for those 4, 1e-6 for the rest}, listed per SCF in the run record's cache (`reused_from_energy_conv`). The 7 pose-0 SCFs stay excluded (A1).
4. **Model B (CHARMM) is not run** (`--skip-model-b`), as before; criterion 4 is reported SKIPPED.
5. **Cost.** 24 SCFs remain (14 molecules x 2 jittered poses, minus the 4 reused), at about 1900 CPU-s each: about 12.7 CPU-h. The call cap is 13.0 CPU-h with 2500 CPU-s per in-flight SCF.
   Study total spent so far is about 19.9 CPU-h; this call brings it to at most about 32.9 CPU-h, **over the 30 CPU-h total of A1.** That is a disclosed deviation: the instruction was to move forward, and
   stopping at the old total would again produce no result.
6. **Looser convergence can change which SCFs converge** and by up to about 1e-6 Ha (6e-4 kcal/mol) the energies it does return. It cannot be excluded that a looser threshold accepts a different SCF
   solution than a tighter one would; no check of that is made beyond the pre-existing sanity assertions (self-anchor exactly 0, non-inert field).

**What this still does not support.** Same as A1: 2 poses, 1-dof SEMs, rho of +-1 by construction, no ranking finer than the floor, no statement about experiment.

**Seen since A1:** the failure pattern (3 of 7 jittered-pose SCFs unconverged at 1e-10, all F or Cl) and the cached CPU times. No energy of a failed SCF exists; the 4 reused dE values are in the cache
but no ddE has been computed or looked at.

## Amendment A3 (written after the broken-pose finding and before any SCF of a rebuilt pose)

**What happened.** A 16-atom fragment of `bnz-C34-F` showed that the runs-1-to-3 poses have the halogen stranded 3.3 to 8.2 Å from its carbon in 5 of the 6 failed cases (`docs/benchmarks/danuglipron_halogen_accel.md`, "Cause found"); `PairedPoses` was run with its default `relax_unmapped=False`. The SCF failures, A2's threshold change, ferric#410 and the AURORA/TRAH study all rest on those poses. A3 replaces the pose construction and discards nothing already written, but no energy from runs 1-3 is carried into the A3 result.

**Changes (everything else in the plan, A1 and A2 stands unless stated).**

1. **Poses are rebuilt with `relax_unmapped=True`:** MMFF94 relaxes only the unmapped (substituent) atoms with the mapped atoms held fixed, so every mapped coordinate is still an exact copy of the parent pose. Same seeds, same `--poses 2 --skip-first-poses 1` (A1). This moves the substituent, so it changes the ddE: a deliberate change of treatment, justified by the old geometry being unphysical.
2. **A geometry gate runs before any SCF.** `smeltery.gates.pose_geometry_problems` (composition, connectivity by the Open Babel bond rule, per-bond limit; it is also now enforced by `require_passing_poses`) is applied to every pose of every molecule. Any failure stops the run before an SCF starts; the failing poses are reported and nothing is scored, tuned or dropped.
3. **The same gate is applied to the OLD (unrelaxed) poses of all 14 molecules and both poses,** once, to say how many of the 28 were stranded and how many of those had converged. This is a count, not a result.
4. **`energy_conv` returns to the tier default 1e-10** (A2's reason was the broken poses); `density_conv` stays 1e-8. Parent and self-pair poses are not relaxed, so the parent's cached SCFs at 1e-10 are reused; all 24 analogue SCFs (12 analogues x 2 poses) are new, since their coordinates changed. A failed SCF is final (no retry, no loosening), as in the original plan.
5. **Model B is not run** (`--skip-model-b`): criterion 4 stays SKIPPED.
6. **Budget:** cap 8 CPU-h for this run, 2,500 CPU-s assumed per in-flight SCF. The earlier runs' 29.0 CPU-h against the 30 CPU-h cap and the later 13.2 CPU-h are spent; this is new spend, and the project owner approved the rebuilt-pose plan on 2026-10-15 without naming a number, so the 8 CPU-h is mine and is stated here.

**Outcome language.** Unchanged from the plan and A1/A2: with 2 poses a paired SEM has 1 degree of freedom, rho is +-1 by construction, nothing supports a ranking finer than the floor or any statement about the campaign or experiment. If every SCF converges, the scan's criteria are reported as the plan defines them, on the rebuilt poses, and not compared with the broken-pose runs. If any SCF fails on a pose that passed the geometry gate, that is reported as a new finding and the run stops.

**Seen before A3:** the bond lengths of the six stored failed poses, the fragment SCFs (stored and repaired geometry), and that AURORA, TRAH and three ladder rungs converged none of the broken poses. No energy of a rebuilt pose exists.
