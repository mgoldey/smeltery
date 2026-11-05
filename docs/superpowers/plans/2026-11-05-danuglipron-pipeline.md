# Danuglipron Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The danuglipron halogen campaign as a Bench pipeline (old results imported, new elements as method
registrations), run end to end under Temporal with every gap filled.

**Architecture:** A method registry (`smeltery.methods`) becomes the one place a method is declared; catalog, execution
order, steps, CLI validation and input staging derive from it, and four slot protocols (pose_source, field_source,
measure, analysis) let new methods plug into `smeltery run`. New elements and a Temporal runner are added on top; the
example lives in `src/smeltery/prebuilt/danuglipron-halogen/`.

**Tech Stack:** Python 3.12, temporalio (the `temporal` extra), Temporal dev server in Docker, ferric RHF, pdb2pqr.

**Spec:** `docs/superpowers/specs/2026-11-05-danuglipron-pipeline-design.md`

## Global Constraints

- Existing configs keep their input digests and results (tests: golden path, `tests/test_prebuilt.py`, `tests/test_cli.py`).
- One declaration per method: no method may be listed in catalog.py, pipeline.py, steps.py, cli.py TOP_KEYS or INPUT_PATHS
  by hand once D1 lands.
- Every SCF outcome is recorded (value, rung, or failure); nothing is silently retried or dropped.
- The imported record is never modified; `experiments/` and `docs/archive/` are frozen.
- All earlier constraints of `docs/superpowers/plans/2026-11-04-bench-workspace.md` still hold.

## Review Focus

1. A config that uses no new method produces the same input digest before and after D1. → D1.
2. A pose that fails at the parent but converges for an analogue: dropped pairwise, both sides. → D4.
3. A Temporal worker killed mid-SCF: the workflow resumes, the finished SCFs are not recomputed. → D4.
4. The Temporal server down: `--temporal` fails fast with the docker command, never falls back silently. → D4.
5. A legacy record lacking `per_pose`: imported shows "not recorded", Bench does not 500. → D5.

---

### D1: The method registry

**Files:** Create `src/smeltery/methods/__init__.py` (registry + `MethodSpec` + slot protocols),
`src/smeltery/methods/builtin.py` (today's methods as registrations); Modify `src/smeltery/gui/catalog.py`,
`src/smeltery/gui/pipeline.py`, `src/smeltery/gui/steps.py`, `src/smeltery/cli.py`, `src/smeltery/run_inputs.py`;
Test `tests/test_methods_registry.py`.

**Interfaces:**
- `@dataclass(frozen=True) class MethodSpec: key, label, rung, role, step, slot, section: str|None, knobs: type|None,
  file_knobs: tuple[str, ...], tier: str|None, required: bool, exclusive: tuple[str, ...], wired: bool, hook: object|None`
  plus the existing catalog fields it replaces (needs, provides, gate_key, summary, implicit, why_unwired).
- `register(spec) -> MethodSpec`, `registry() -> dict[str, MethodSpec]`, `by_slot(slot) -> list[MethodSpec]`.
- Slot protocols: `PoseSource.parent_poses(cfg, parent, ctx) -> list[Pose]`; `FieldSource.field(cfg, parent, ctx) ->
  PointChargeField`; `Measure.tier(cfg) -> Tier`; `Analysis.run(plan, rec, ctx) -> dict` (merged into `results[key]`).
- `catalog()`, `EXECUTION_ORDER`, `STEPS` membership, `cli.TOP_KEYS`, section knob validation and `run_inputs.INPUT_PATHS`
  are derived from `registry()`.

- [ ] Test `test_a_new_registration_appears_everywhere`: register a dummy analysis method in a test (fixture that
  unregisters): it is in `catalog()`, its section validates in `validate_config`, it is offered by `Pipeline.addable()`,
  it lands in its step in `steps()`, its file knob is staged by `stage_inputs`, and `run_plan` calls its hook.
- [ ] Test `test_existing_configs_keep_their_digests`: for every `examples/*.toml` and `src/smeltery/prebuilt/*/pipeline.toml`
  the `--plan` digest equals the digest computed at the parent commit (record them in the test from a run at HEAD before
  the change, as data).
- [ ] Migrate; full gates of AGENTS.md for cli.py and gui/. Commit `methods: one registration declares a method`.

### D2: Bound-conformer parent poses

**Files:** Create `src/smeltery/methods/bound_pose.py` (registration + PoseSource), move the XYZ→SMILES-order logic from
`examples/danuglipron_halogen.py` (`bound_pose_in_smiles_order`, `BoundPosePairedPoses`) into it; Test
`tests/test_bound_pose.py`.

**Interfaces:** `[bound_pose] file (path, staged), n_poses = 6, jitter_deg = 15.0, jitter_ang = 0.5, seed, skip_first = 0`;
exclusive with `docking`; pose 0 = the bound conformer, poses 1.. = seeded rigid jitters; analogues paired as today.

- [ ] Tests: poses equal the example's `make_pairing(...)` output on the committed conformer (data source: the example's
  own functions, imported); atom order matches the parent SMILES with explicit H; exclusive with docking; file staged.

### D3: Charge-model sensitivity

**Files:** Create `src/smeltery/methods/charge_sensitivity.py` (registration + Analysis); Modify the measure step view
(`ui/src/components/workspace/views/MeasureView.tsx`) to show the sensitivity table; Test `tests/test_charge_sensitivity_method.py`.

**Interfaces:** `[charge_sensitivity] models = ["CHARMM"]`; builds each extra field with pdb2pqr `--ff=<model>` the way the
primary field is built, re-runs the measurement for every pose, records `results.charge_sensitivity = {model: {paired_ddE,
sign_flips, spearman}}` via `smeltery.charge_sensitivity`.

- [ ] Tests with an injected measure (no SCF): the analysis result equals `charge_sensitivity(...)` called by hand; the
  field for CHARMM differs from AMBER's on the 7LCJ fixture (data source: pdb2pqr output).

### D4: Pipeline runs under Temporal

**Files:** Create `src/smeltery/temporal_run.py` (workflow + activities + worker), Modify `src/smeltery/cli.py`
(`run --temporal`, `temporal-worker`), `src/smeltery/bench/use_cases/launch_run.py` (use the Temporal queue when
configured); Test `tests/test_temporal_run.py` (uses `temporalio.testing.WorkflowEnvironment.start_time_skipping()` or
`start_local()`; skips with a reason when temporalio is absent).

**Interfaces:** `PipelineRunWorkflow.run(RunInput(config, out, workers)) -> RunSummary`; activities `prepare`,
`score(ScoreInput(candidate, pose, field_model, rung)) -> ScoreResult(dE|None, rung, cpu_s, error)` with
`start_to_close_timeout` from `[run] scf_timeout_s` (controller call default 4 h), heartbeats, `RetryPolicy(maximum_attempts=3,
non_retryable_error_types=["ScfNotConverged", "ClashRefused"])`, `finalize`. Remedy ladder `[field_interaction] remedies`
(list of knob overrides); rung recorded per value in `calcs.jsonl`. Pairwise drop of poses that fail every rung, recorded
in `results.dropped_poses`. Server down → exit 2 with the docker command.

- [ ] Tests: workflow completes on a tiny config with an injected score activity; a killed worker resumes without
  recomputing (count activity calls); a non-converging pose climbs the ladder then is dropped pairwise; server down exits 2.

### D5: The danuglipron example and the importer

**Files:** Create `src/smeltery/prebuilt/danuglipron-halogen/{pipeline.toml, about.md, <inputs>}`,
`src/smeltery/bench/importers/__init__.py` (registry `register(name, detect, convert)`),
`src/smeltery/bench/importers/danuglipron_2026_10.py`; `imported/` run folder generated by the importer from the
committed record; Test `tests/test_importers.py`, extend `tests/test_prebuilt.py`.

- [ ] Tests: `pipeline.toml` validates and plans (no SCF); the imported record passes `verdict_of` (UNRANKED, as recorded),
  its per-candidate paired means equal the original's `paired_ddE_amber` (data source: the original file), the original is
  byte-identical after import, missing keys are listed in the imported record's `results.not_recorded`.

### D6: Run it and fill the gaps

- [ ] Controller: start the Temporal dev server (Docker), a worker, `smeltery run ... --plan` (print the estimate),
  then `--temporal`; monitor; on completion commit `run/` and write the outcome into `docs/benchmarks/danuglipron_halogen.md`
  (a new dated section; earlier sections unchanged) with every number from the record.
