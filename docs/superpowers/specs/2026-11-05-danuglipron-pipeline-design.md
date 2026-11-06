# Danuglipron as a pipeline, run durably: design addendum

Date: 2026-11-05. Builds on `2026-11-04-bench-workspace-design.md`. Status: requested by the user, written by the
controller; the calls marked "controller call" are mine.

## Why

The user asked: map the existing danuglipron halogen results into a Bench pipeline, copying the results in and adding the
pipeline elements the campaign needs; focus on easy extensibility as gaps appear; write the whole thing up as a working
example; then run it end to end, filling every gap, with Temporal tracking state.

The campaign exists today only as a script (`examples/danuglipron_halogen.py`) and one record
(`benchmarks/danuglipron_halogen/results/danuglipron-halogen-38fa833cb90c.json`, 12 analogues, 7LCJ field of 6458 AMBER
charges, RHF/STO-3G). That record pairs one kept pose per candidate (amendment A1 dropped pose 0; nine jittered SCFs did
not converge), so every paired ddE has n = 1, the cut is UNRANKED, and the second charge model (CHARMM) was skipped. The
run metered 12.1 CPU-h; the median SCF took 27 min and the slowest 2 h 6 min.

## What the campaign needs that a pipeline cannot express today

1. **Parent poses from a bound conformer** (the cryo-EM conformer plus seeded rigid jitters), not ETKDG or docking.
2. **A charge-model sensitivity analysis**: the same pocket charged by a second force field (pdb2pqr `--ff=CHARMM`), the
   measurement repeated, and the disagreement reported (`smeltery.charge_sensitivity` already computes it).
3. **Pose-level SCF execution that survives**: per-pose work with timeouts, heartbeats, retries for transient failures,
   a recorded (never silently retried) outcome for chemistry failures, resume after interruption, and a remedy ladder for
   non-convergence whose rung is recorded per value.
4. **Importing a record that predates the contract**: the old record uses `paired_ddE_amber` and campaign criteria, not
   the keys Bench reads.
5. **Extensibility**: adding one method today edits `cli.py` (TOP_KEYS, knob validation, stage wiring),
   `gui/catalog.py`, `gui/pipeline.py` (EXECUTION_ORDER), `gui/steps.py` (STEPS), `run_inputs.INPUT_PATHS`, a tier docs page
   and the UI. Each of gaps 1-3 would repeat that.

## Decided

- **A method registry is the extension point.** One registration declares a method once: key, label, rung, role, step,
  execution slot, config section and its knobs dataclass, which knobs are input files (so staging finds them), the tier
  it runs, required/exclusive, and a hook of the slot's protocol. The catalog, the pipeline's execution order, the steps
  model, the CLI's TOP_KEYS and knob validation, and input staging all derive from the registry. Slots with a hook
  protocol: `pose_source` (gives parent poses), `field_source` (gives point charges), `measure` (the dE_int tier),
  `analysis` (runs after the measurement, adds results). Existing methods are re-expressed as registrations; behaviour
  and digests of existing configs must not change (golden-path and prebuilt digest tests pin it).
- **The new elements are registrations** in their own modules: `bound_pose` (pose_source; `[bound_pose] file`, n_poses,
  jitter_deg, jitter_ang, seed, skip_first), `charge_sensitivity` (analysis; `[charge_sensitivity] models = ["CHARMM"]`).
- **Temporal runs a pipeline** (`smeltery run CONFIG --out OUT --temporal`, plus `smeltery temporal-worker`), following the
  pattern of the user's `scripts/temporal_ddE.py`: the workflow holds only control flow; activities do the IO and
  compute: `prepare` (build the plan, write poses and fields under OUT), `score(candidate, pose, field)` per SCF with a
  start-to-close timeout, heartbeats, a retry policy for transient errors and a non-retryable error type for chemistry
  refusals, and `finalize` (assemble the run record from the per-SCF log). Resume reads completed SCFs from the log.
  A non-converged SCF climbs a remedy ladder declared in the config (controller call: `[field_interaction] remedies`,
  a list of knob overrides tried in order, e.g. a higher `max_iter`; each value records the rung that produced it); if
  every rung fails the pose is dropped pairwise (parent and analogue together) and the record says so. Bench launches a
  pipeline through the existing Temporal task queue when the server is up, and its work panel shows the workflow.
- **The example**: `src/smeltery/prebuilt/danuglipron-halogen/` with `pipeline.toml` (the 12 analogues and neutral-acid
  parent as recorded, `[bound_pose]` from the committed cryo-EM conformer, the 7LCJ pocket, RHF/STO-3G, the remedy
  ladder, `[charge_sensitivity]`, the declared floor 1.26 kcal/mol with its source), inputs, `about.md`, and two runs:
  `imported/` (the 2026-10-16 record through a versioned importer, original kept byte for byte, every key the contract
  needs that the old record lacks reported as "not recorded") and `run/` (the new Temporal run).
- **The importer is versioned and declared**: `smeltery.bench.importers` registers `(name, detector, converter)`; the
  2026-10 danuglipron converter is the first. It never edits the original.
- **Controller calls on the run**: 4 kept poses per candidate (enough for an SEM; pose 0 is the bound conformer and is kept,
  amendment A1's drop is not carried forward because the remedy ladder replaces it), AMBER and CHARMM fields, 6 workers,
  `nice 10`, a CPU-hour cap of 120 with the estimate printed by `--plan` before starting. The previous run's converged
  SCFs are not reused (different poses and settings).

## Non-goals

- Changing the science of the measurement (same tier, same field construction, same pairing).
- A new UI page; the existing step views show the new results (the measure view gains the sensitivity table).
- Recommending a candidate: the cut decides, and the campaign doc's refusals stand.

## Anti-goals

- A second way to declare a method (the registry is the only one).
- Silently retrying or dropping an SCF: every rung and every drop is recorded.
- Editing the imported record or the frozen `experiments/` and `docs/archive/` records.
