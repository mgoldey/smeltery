# Per-pose SCF resilience: a bounded ladder, then an explicit partial result

Date: 2026-11-28. Status: built.

## Problem

The danuglipron halogen scan never produced a paired ddE: 7 SCFs failed on jittered poses, all F or Cl analogues
(`docs/benchmarks/danuglipron_halogen.md`), and a failed SCF stopped the run. Raising `energy_conv` rescued 2 of 3 old
failures and exposed 4 new ones, so thresholds are not the lever (and they change the measured quantity). The plain
`FieldInteraction` tier raised on the first pose that failed; only `--temporal` had a ladder and a pairwise drop, with no
minimum on how many pairs a ranking needs, and the plain path had neither the drop nor the report.

## Audit: what already existed

| Piece | State |
|---|---|
| Remedy ladder (`FieldInteraction.remedies`, `REMEDY_KEYS`) | in the tier, so both paths; no cap on length; no initial-guess knob |
| Pairwise drop, `results.dropped_poses`, `results.pose_index` | `--temporal` only (`temporal_run.cut_sync`) |
| Checkpoint of every SCF (`calcs.partial.jsonl`) | plain run; failed rungs are logged too, so a rerun replays them |
| `funnel.cut` | no notion of how many pairs back a measurement; `n < 2` simply never resolves |

## Knobs ferric exposes (checked against `ferric.run_rhf` 0.1.0rc10, not assumed)

`max_iter`, `diis` (pulay, adiis, ediis), `diis_size`, `level_shift`, `soscf`, `guess` (`minao`, `hcore`; `sad` is an alias
of `minao`), `mom_after_iter`, `trah_trigger`, `stability_descent`, `aurora`. There is **no damping option**, so none is
offered. `energy_conv`, `density_conv` and `smearing_sigma` stay refused (they change the quantity). Added to
`REMEDY_KEYS`: `guess` only (an initial guess changes where the SCF starts, not which solution it reports; a rung that
lands on a higher state is already refused by `refuse_false_convergence`).

## Policy

1. **Bounded, deterministic ladder.** Rung 0 is the tier's settings; rung k adds `remedies[k-1]`. At most
   `MAX_REMEDY_RUNGS = 5`, each leg tried once per rung, in order, with no randomness and no loop back. Failed rungs are
   checkpointed, so a rerun replays them instead of paying for them again (a failure here cost 5,000 to 7,500 CPU-s).
   `DEFAULT_SCF_LADDER` (3 rungs: more iterations; ADIIS plus level shift 0.5; ADIIS, `hcore` guess, shift 1.0, SOSCF) is a
   starting point, opt in; not measured to converge the halogen cases (that needs the real engine).
2. **A pose that fails every rung is dropped for its pair, never silently.** `on_scf_failure = "drop"` (default
   `"raise"`: the existing behaviour, digests and records unchanged). A failed vacuum leg skips the field leg (its
   value would not be reported). The failure is an entry in `FieldInteraction.failures` and NaN in `per_pose`.
3. **Partial result, computed over the intersection.** `planning.drop_failed_poses` uses the `--temporal` rule: a parent
   failure drops that pose for every analogue; an analogue failure drops it for that analogue only. Poses are compacted so
   pose i still pairs with pose i. `paired_delta` additionally refuses a non-finite value, so a NaN cannot reach a mean.
4. **Reported.** `results.dropped_poses` (candidate, pose, leg, outcome, reason, rungs tried, `dropped_with`),
   `results.pose_index`, `results.pairing` = `requested_poses`, `n_pairs` per analogue, `failed_pairs` (analogue, pose,
   each failing leg with its reason), `min_pairs`; `paired_ddE.n` per analogue; a `dropped:` line per failure on stdout.
5. **The cut refuses rather than guesses.** `cut(min_pairs=)`: a measurement with fewer pairs is listed in
   `CutResult.unrankable` (`results.cut_unrankable`), the rest is still grouped, and the stage claims **no survivors**
   (`unranked`): a candidate that could not be measured cannot be called worse. `[cut] min_pairs` (integer >= 2); default 3
   once any pose was dropped (declared, not measured: n = 2 gives a one-degree-of-freedom SEM). With no drop the cut is
   exactly as before. SEM is not inflated beyond that: it is the SEM of the surviving pairs, so fewer pairs already
   widens it, and the z-bar and floor apply as usual; a small-n t-correction was considered and left out (it would need
   a measured justification for the per-pair spread).
6. **Existing behaviour kept.** A failure still keeps the finished results: the run stops only when a candidate has
   no pose left (exit 3), and the checkpoint holds everything computed. `examples/danuglipron_halogen.py`
   (its own cache and "never retried" rule, with its known flaky test) is untouched.

## Limits

* `[charge_sensitivity]` re-measures every pose and cannot pair over different pose sets: a plain run with drops
  refuses it (`--temporal` summarizes it from the log). A partial run (`--only-candidate`) that fails an SCF is refused.
* `--temporal` gets `min_pairs` but not `results.pairing` (its `dropped_poses` are as before).
* The ladder's effect on the real F/Cl failures is not measured here; every test uses a fake engine.
