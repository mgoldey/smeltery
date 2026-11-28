# Changelog

All notable changes to smeltery are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are derived from git tags
(hatch-vcs), and the project does not yet follow a stable-API promise. Entries are grouped from the merged
pull requests; numbers refer to issues or pull requests on the smeltery tracker.

## [Unreleased]

### Added
- `smeltery floor measure RESULTS... [--models A B] [--json OUT] [--provisional]` (`smeltery.floor_measure`,
  docs/superpowers/specs/2026-11-28-measure-field-floor-design.md): the charge-model sensitivity floor of the paired
  ddE from stored per-pose energies of any number of PLB-style systems, pooled (per-system floors, max, median, min,
  spread, df-weighted shift SD). It reproduces L6 (cdk2 1.257) and L11 (tyk2 0.667 kcal/mol) from the committed
  results. `--provisional` returns a `ProvisionalFloor` (`measured` always False, source begins `PROVISIONAL, NOT
  MEASURED`, lists its systems) only with at least 3 systems within 1.5x; with the two that exist it refuses (exit 3).
  No third system was measured, `FieldInteraction.systematic_floor` is still None, and docs/limits.md gains U3, not an
  L-row.
- Generator rejections in the run record (docs/superpowers/specs/2026-11-28-generator-rejections-in-the-record.md). A
  config's `[generation] file` (written by `smeltery.generate.record.write_generation`) adds a leading `generate` row to
  `results.funnel` -- raw outputs in, candidates out, a count per closed rejection reason, at most 50 rejected SMILES --
  plus `results.generation_in` and `ext.smeltery.generation` in the core record (the frozen schema is untouched). The
  dataset export gains `smeltery.generation` file metadata for such runs, `GET /api/runs/{id}/attrition` serves the
  stages and the rejections, and the Bench Results page shows "Where candidates were lost". Records with no generation
  step are unchanged.
- A pinned compatible set and a ferric compatibility matrix (roadmap 46, docs/compatibility.md,
  docs/superpowers/specs/2026-11-20-compat-matrix-design.md). `compat/stack.toml` pins ferric, attestation-schema and
  mcp for smeltery's environment (equal to uv.lock's, enforced by tests/test_compat_stack.py) and attestation by full
  commit in its OWN environment -- one environment cannot hold both today (attestation-schema `<0.2` vs
  `>=0.2.0`; both sides already use mcp 2.3.0); `compat/constraints.txt` is its generated constraints file, for
  `uv pip install -c`. A new `stack` CI job installs exactly that pair and runs the cross-server transcript against
  it. `scripts/compat_matrix.py` tests one smeltery against one ferric per fresh venv (every other dependency pinned to
  that smeltery's uv.lock, ferric as an override), running the engine-calling tests and the example, and renders
  docs/compatibility.md from the cells; the new `compat` workflow runs it weekly, on demand and on every tag for each
  ferric release the floor admits plus ferric main's nightly wheel, and uploads the regenerated page as an artifact
  (it never commits).
- Cross-server agent transcript (roadmap 27, docs/cross-server.md): `tests/cross_server/test_agent_transcript.py`
  drives smeltery's MCP server (on the fake engine) and attestation's `attest-mcp` over stdio as an agent does. It
  submits two screens of one campaign, polls them to completion, then asks attestation for what is in flight, the
  scan, the family's ranking, each run's provenance (a dirty ferric build stamp becomes `dependencies dirty: ferric`)
  and a claim check. It writes a JSON-lines transcript. A new CI job, `cross-server`, installs attestation from git
  into its own `uv tool` environment, never smeltery's, requires the test to run, and uploads the transcript.
- `smeltery export hf SRC -o OUT` (`smeltery.hub_export`, docs/dataset-export.md): a Hugging Face–loadable folder from a
  dataset directory or a Parquet export -- the source byte for byte under `OUT/dataset/`, each split's partitions as
  Parquet, a generated dataset card (`README.md`: license when stated, `configs`, `dataset_info.features`, columns with
  units, provenance) and `croissant.json` (Croissant 1.0: a FileObject per file with the export's own sha256, a
  RecordSet whose fields are the Parquet columns, W3C PROV naming each source run, its core-record sha256 and the
  ferric builds its rows state). Needs pyarrow only; the license is never invented (`--license`, else the record's,
  else "not stated"). `--push OWNER/NAME` (new `hub` extra, huggingface_hub) is a dry run unless `--confirm`, re-checks
  every file against croissant.json and refuses a folder with no stated license; `export hf push FOLDER OWNER/NAME`
  uploads one written earlier.
- A campaign job may carry an `arm` and the submitter's own `ext` namespaces (`smeltery.jobs.campaign_spec(...,
  arm=, ext=)`, `check_spec`, docs/jobs.md "arm and ext"). The local backend hands them to the run (`smeltery run
  --arm NAME --ext-json JSON`), so they are in the core record the FIRST time it is written, in every terminal state:
  a finished record is never rewritten, since its bytes are what `settle`, a dataset's `source_runs[].record_sha256`
  and a ledger scan hash. An arm must be a label (`^[A-Za-z0-9_.:-]+$`) and an ext namespace may not be `smeltery`
  (the run's own). smeltery still has no arm of its own, so every run outside a job is unchanged. A run that IS an
  arm also states the scalars its family's arms are compared on -- `best_paired_ddE` and `mean_paired_ddE`
  (`core_record.arm_metrics`), label-free, direction `min`, derived from its own results. `SMELTERY_BACKEND=temporal`
  carries neither and refuses such a spec at submit rather than dropping it, as the contract requires.
- `smeltery al run` (`smeltery.al`, docs/active-learning.md): an active-learning loop. An external `Proposer`
  (`propose(history, n) -> [SMILES]`, optional `fit`) proposes candidates, smeltery scores them through the funnel as
  an ordinary campaign job (ceiling, stage reuse and `cancel` all apply — no second execution path), and the scores
  feed back. `--budget` caps the loop's total predicted CPU-hours; a built-in dependency-free `PoolProposer`
  (`--pool FILE --seed K`) ships for demos and tests. Each iteration is recorded as one attestation **family
  `al:<loop>`, arm `iter-NNN`**, so `attest runs compare al:<loop>` ranks the iterations by a shared `best_paired_ddE`
  metric (the proposer's learning curve). A canonical `al-manifest.json` pins every iteration's run dir and
  core-record sha256, and a stopped loop resumes from it. Canonical-SMILES dedup across iterations. Needs the
  `attestation` extra to group the records.
- `smeltery export dataset RUN... -o DIR` (`smeltery.dataset_record`, docs/dataset-export.md): a dataset DIRECTORY
  with an `attestation.dataset/1` record (draft schema) -- the table, its `--split`s, then `attestation/digests.json`
  (whose sha256 is the content digest), then `attestation/dataset.json` naming each source run's core record by the
  sha256 of its `run.json`. `--dataset-id`, `--version`, `--name`, `--license` (SPDX; null by default, never smeltery's
  own code license). Every row's `ferric_git_sha` is checked against its run's core-record ferric `build_sha`. A
  dataset directory is immutable: `smeltery export split KIND DIR -o NEWDIR` writes a new version with the split
  added, and `ExportDataset`/`export split verify` accept a dataset directory (checked against its record first; a
  split by name; `dataset_sha256=` pins the digest; `lineage_entries()` gives a training run's `data[]`). Needs
  attestation-schema >= 0.2.0, not yet released; with 0.1.0 it refuses naming that version and its tests skip.
- Target definition files are a documented format (`smeltery.target_file`, docs/targets.md): `schema_version = 1` plus
  only `[pocket]`/`[[pocket.site]]`, `[structure]`, `[docking]`, `[gates]`, each checked by `smeltery run`'s own
  validator; an unknown section or key, a missing or other version, or a missing pocket file is refused with the
  reason. `smeltery target check FILE` runs the check; example: `examples/targets/two_site_pocket.toml`. MCP's
  `target` now requires `schema_version`.
- MCP `screen_candidates` / `estimate_cost` take `relax_unmapped` (default: the target class's `true`). The description
  says what turning it off risks (the pose geometry check may refuse substituted analogues); an override is listed in
  the plan's `overrides` and written into the screen's `config.toml` (the value, plus a comment naming the override).
  The class default now also reaches docked screens (`[poses] relax_unmapped`), which previously got the tier's own
  default (`false`).
- Resumability and cancellation (`smeltery.reuse`, docs/jobs.md "Resumability"): every stage of a run gets a content
  digest over its upstream output, its settings and the code identity (smeltery's source bytes, ferric's build), and
  a resubmitted identical campaign reuses the measurement and any analysis an earlier run completed, from a
  per-workspace content-addressed store (`ROOT/.smeltery/stages`, used by local campaign jobs and by
  `smeltery run --stage-cache DIR` / `SMELTERY_STAGE_CACHE`). Never reused across ferric builds, nor from a dirty or
  unverified one; a corrupted entry is detected by digest and recomputed; concurrent writers cannot corrupt the store.
  A reused stage is recorded in `results.reuse` / `ext.smeltery.reuse` and `steps/NN-<stage>/stage.json` (which run,
  which digest); the output is otherwise byte-identical. A run logs completed stages to `OUT/stages.partial.jsonl`,
  so a cancelled or failed job's record lists them (`ext["smeltery.job"].stages_completed`) and a resubmission reuses
  them; `smeltery status` and MCP's `run_status` / `screen_candidates` report reused stages.
- `smeltery mcp`: an MCP server (official `mcp` SDK, the new optional `mcp` extra) with four intent-shaped tools for
  an agent with no quantum chemistry -- `screen_candidates` (plans and submits a screen through the campaign
  `JobBackend`, returning a handle at once; `dry_run=True` returns the plan and its cost without submitting),
  `run_status` (state plus per-stage progress where the run leaves enough on disk to tell), `explain_drop` (which
  stage dropped a molecule, on what number, against what threshold and systematic-error floor, read from the run's
  own record) and `estimate_cost` (CPU-hours, candidates and memory from `smeltery.cost`, null with a reason when it
  cannot price the settings). Defaults come per target class from a data table
  (`src/smeltery/data/target_classes.toml`), each justified from this repository's own tier pages and cost
  measurements, so no caller picks a basis set. Guardrails: `SMELTERY_MAX_CPU_HOURS` and `SMELTERY_MAX_CANDIDATES`
  (via `smeltery.settings`) refuse a submission above the ceiling -- an unpriceable screen counts as over it -- and
  the refusal says to pass `confirm=True`. Expected refusals reach the caller verbatim; anything else is logged and
  answered generically. See `docs/mcp.md`.
- Campaign jobs: `smeltery submit CONFIG --out RUN_DIR` returns at once, and `smeltery status / cancel / result
  RUN_DIR` follow the job (`smeltery.jobs`, the `attestation` extra). Two implementations of attestation-schema's
  `JobBackend` behind `SMELTERY_BACKEND`: `local` (default; a capped task on Bench's SQLite queue under its own
  supervisor, sharing `SMELTERY_MAX_RUNS`, no server) and `temporal` (the existing `PipelineRunWorkflow`, started
  without waiting). The backend owns `RUN_DIR/attestation/state.json` (`attestation.state/1`: JCS, atomic, under a
  `flock`, history checked against the state machine), and every terminal job has a core record: a complete run's own,
  or a minimal `failed`/`cancelled` record. `smeltery worker` (local) settles jobs whose process ended.
  `tests/test_job_backends.py` runs one conformance suite against both backends (the temporal leg needs a server).
  See `docs/jobs.md`.
- `smeltery serve` (Bench, the repo's one server, with a backend choice) and `smeltery worker` (the worker for that
  backend: a local supervisor loop, or one Temporal process polling both `smeltery-tasks` and `smeltery-pipeline`),
  configured by `SMELTERY_BACKEND`, `SMELTERY_TEMPORAL_ADDRESS`, `SMELTERY_TEMPORAL_NAMESPACE`, `SMELTERY_TASK_QUEUE`,
  `SMELTERY_PIPELINE_QUEUE`, `SMELTERY_HOST`, `SMELTERY_PORT`, `SMELTERY_ROOT`, `SMELTERY_WORKERS`,
  `SMELTERY_THREADS_PER_WORKER`, `SMELTERY_TASK_CONCURRENCY` and `SMELTERY_MAX_RUNS` (flag, then variable, then default;
  one pure resolver, `smeltery.settings`; a bad value exits 2 naming the variable). `task-worker`, `temporal-worker` and
  `run --temporal --host/--task-queue` keep working and now read the same variables; `run` and `temporal-worker` gain
  `--namespace`. `compose.yaml` gains an opt-in `temporal` profile (Temporal dev server plus a `smeltery worker`
  service); the Dockerfile takes an `SMELTERY_EXTRAS` build argument. See `docs/temporal-runs.md`.
- `smeltery export parquet|arrow RUN... -o FILE`: finished runs as one columnar dataset, one row per (ligand, target,
  tier), with scores and units and floors from the tiers' own declarations, the paired ddE, the funnel decision, and
  provenance columns (run id, `record_sha256`, smeltery and ferric versions, ferric build commit, dirty flag and
  provenance level). Units and the schema version are in the file's key-value metadata. pyarrow is the optional
  `dataset` extra, with a wheel smoke check; see `docs/dataset-export.md`.
- `smeltery export split scaffold|target|verify`: train/valid/test splits of an exported table as versioned JSON
  artifacts (kind, algorithm version, seed, parameters, the table file's SHA-256, row identities per partition).
  Byte-identical for the same table and seed; loading against a changed table raises. Scaffold splits use RDKit Murcko
  scaffolds and refuse without RDKit. `smeltery.dataset_torch.ExportDataset` reads the table for a PyTorch
  `DataLoader` (feature and target columns, tier/decision filters, split partition, transform hook); torch is the new
  optional `torch` extra, with a wheel smoke check.
- The core run record (`attestation.run/1`, v1-DRAFT): with the new optional `attestation` extra (attestation-schema,
  attestation-schema 0.1.0 from PyPI; Python >= 3.11) every finished run also writes `OUT/attestation/run.json`
  through the schema's own writer, with a manifest of OUT and one `steps/NN-<stage>/` per funnel stage, beside the
  unchanged `run-<digest12>.json`. `smeltery record core OUT` converts an existing run. Dependencies, ferric included,
  go through one identity ladder (`record.package_identity`); a conda/distro install is now INFERRED, not VERIFIED.
  Mapping and the schema findings: `docs/core-record.md`.
- The core run record follows attestation-schema `314e4ff` (pin moved from `93bb5ab`): `code` carries `version`,
  `provenance` and `evidence`; metrics carry `unit` and `labels` (`paired_ddE.mean|sem|n` labelled by analogue,
  `stage.*` by stage, ddE in `kcal/mol`); an undefined value (a one-pose sem, an unranked cut's count) is `"NaN"`, not
  omitted; docking's seed list is recorded; dependencies carry `source`; `history` is `[]`; timestamps go through
  `format_timestamp`; a run that mixed ferric builds records ferric as `unknown`. The `attestation` extra now installs
  on Python 3.11 too. The golden core record changed accordingly. Findings disposition: `docs/core-record.md`.
- The core run record follows attestation-schema `f809522` (pin moved from `314e4ff`): `history` is `null` (the schema
  moved curves to a digest-bound `attestation/history.jsonl` sidecar; smeltery has none); a run whose rows came from
  more than one smeltery commit records `code` as `unknown` with `git_sha: null` and `evidence` naming each commit and
  its row count (`danuglipron-halogen`); `dependencies[].source` uses the schema's closed vocabulary (Python and distro
  installs are `system`, conda-family installs `conda`, a wheel from a URL `null`); rdkit and numpy unidentified by a
  converted record carry `evidence` instead of being empty entries; `core_record.wire_value` is gone in favour of
  `attestation_schema.spell_value`; a failed/cancelled job's record writes `history` and `dependencies` as `null`
  unconditionally. The golden core record changed accordingly. Findings and open items: `docs/core-record.md`.
- The core run record follows the **frozen** `attestation.run/1` (attestation `9fb7eed`, attestation-schema 0.1.0; the
  extra requires `attestation-schema>=0.1.0,<0.2`: attestation-schema 0.1.0 from PyPI, no git pin and no preinstall step in the wheel smoke or CI). `config` is the campaign
  config the run read (the staged `config.toml`, by its recorded digest), the same with and without a job; the derived
  `inputs` moved to `ext.smeltery.inputs`; a conversion that cannot observe the config writes `{}` and says why in
  `ext.smeltery.config_unobserved`. `smeltery run --run-id` names the run. `dependencies[].source` gains `url:<url>`
  (credentials and query stripped, `#sha256=` kept) and `hg+`/`svn+`/`bzr+` (from PEP 610's `vcs_info.vcs`). The golden
  core record changed accordingly. Findings resolved at the freeze and v2 candidates: `docs/core-record.md`.

### Changed
- `smeltery export` tables are `smeltery.schema_version` 2: new columns `family` and `core_record_sha256` (sha256 of
  the run's core `attestation/run.json`, null without one), and `run_id` is the core record's run_id (a job's
  `--run-id`), no longer always `input_digest[:12]`, which remains derivable from `input_digest`. A core record
  that lists the legacy record with another sha256 is stale and refuses the export. CI's test job installs the
  `dataset` extra, so the Parquet/Arrow tests no longer skip there.
- Campaign jobs (`smeltery.jobs`) use `attestation_schema.state` for `state.json`, its lock, `settle` and the terminal
  record; smeltery's own copy is deleted. `read_state` returns a `StateFile` (`--json` prints its `to_dict()`), a
  malformed file raises `StateFormatError`, `IllegalTransition` is the schema's `IllegalTransitionError`, and ending a
  job is `settle`'s alone. The job's run_id now names its complete record too (the runner passes `--run-id`; Temporal
  passes `RunInput.run_id`), and settle fails a job whose record is not its own. The Temporal backend passes the
  workflow's start and close times to settle. `ext["smeltery.job"]` is `{backend, job_id, stages_completed}`: the
  message lives in `state.json` only. Without `flock` (Windows) state writes are refused. Docs: `docs/jobs.md`.

## [0.1.0] - 2026-11-09

The first release with the migrated tiers (#31). Everything below landed since 0.0.1.

### Added
- Bench: a web app (React + FastAPI) for the whole funnel, with a workspace, a starting point, file picking, reports
  and comment threads (#119, #120, #121).
- Durable runs under Temporal (`smeltery run --temporal`) and the danuglipron halogen scan run as a pipeline, with a
  method registry (#120, #122).
- Marimo GUI: results, grid, pose and pipeline views, a static HTML report, and run outputs (pose SDFs, per-pose
  values, receptor) with a decisions file (`smeltery.gui`, `smeltery.decisions`).
- Tiers: `rescoring` (ScoringProvider), `forcefield` (MMFF and OpenMM/OpenFF Sage), `qmmm` (real covalent cut),
  `surface_esp`, `designability`, `docking`, and a PBE/D3 interaction tier with water-free binding fields (#117).
- Providers: structure (PDB, AlphaFold DB, Boltz-2), property, scoring and potential (ANI-2x, AIMNet2) interfaces,
  a provider registry with entry points and a conformance suite, and a licence table (#16, #21, #22, #23, #24, #25).
- `smeltery run`: config-driven campaigns that start from a target (`[structure]`), dock the parent, report
  per-stage counts and apply a strain gate (#17).
- Gates: PoseBusters before any quantum tier, an always-on pose geometry gate, and a refusal to difference
  rank-only scores (#15, #21).
- Scaffold-growth candidate generator (DiffSBDD).
- Docs: one page per tier with a registry test, measured limits with a test that every limit has a number and a
  source, a golden-path guide, a worked example, a campaign page for danuglipron, and a glossary.
- Benchmarks: PLB experimental ddG set with provenance, paired ddE versus experiment on cdk2 and tyk2, pocket
  charge-model comparisons, an MM/GBSA baseline, a PCM lane, and ChEMBL assay-to-assay variability (#26, #27, #28, #29).
- Golden-path smoke test with a per-gate mutation sweep (#33).
- Bench run views: a per-pose table with CSV export and a values figure toggle, dropped poses on the Overview, and
  the SCF log in the pose explorer (RUN-001, RUN-002, RUN-003, RUN-004).
- Decisions in Bench: bulk keep/drop, and export to CSV and SDF (RUN-006, RUN-007).
- Compare runs side by side, including per-candidate ddE when they share a parent, with explicit "not run" rows
  (RUN-010, CMP-002).
- Run records carry a `schema_version` and are read tolerantly, record the git remote and branch, and self-hash so
  `smeltery verify-run` can detect tampering; `smeltery inspect` and `smeltery diff-run` read them (API-010,
  PROV-001, PROV-002, ENT-015, CLI-008).
- Report context: the systematic floor stated alongside every ddE, the measured limits that applied to the run, and
  a snapshot of comments and decisions (SCI-005, SCI-010, RUN-011).
- Pose export: enriched SDF properties (ddE, tie group, survivor, gates, decisions) from Bench and
  `smeltery export-sdf`; a PyMOL session export (PROV-004, STR-009, PROV-005, STR-010).
- Candidate import and export: CSV and SDF in, candidates out (GEN-008, GEN-009).
- Pipeline lifecycle in Bench: rename, clone, bundle download and bundle import, with zip-slip and zip-bomb guards
  (WS-011, WS-013, WS-014, ENT-006).
- The file picker shows each file's size, mtime and sha256, and pipeline issues are grouped by step (WS-004, WS-010).
- A documented, contract-tested method extension path, with per-method floor status, a tier doc per method, reset to
  built-in, and pipeline links (MET-001, MET-002, MET-003, WS-012).
- Notebook: file attachments and markdown entries (STU-003, STU-004).
- Operational endpoints `/livez` and `/readyz`, with the readiness sqlite probe owned by the task-queue adapter
  (OPS-004).
- `smeltery backup` and `smeltery restore`, with a hash manifest and consistent sqlite snapshots (OPS-008).
- Worker health: task heartbeat age with a stalled marker, per-task memory limits with peak RSS tracking (descendants
  included), and OOM tasks that are killed and never retried (HPC-004, HPC-002).
- Bounded task retries with backoff (HPC-003).
- CLI run flags: `plan.json` written on failure, `--fail-on-unranked` (exit 4), and a refusal to overwrite a
  completed run (CLI-001, CLI-007, CLI-009).
- Structure checks on the PDB in the active-site view, and status markers in the forest view that do not rely on
  colour alone (STR-001, A11Y-002).
- Empty states that say what to do next, human-readable error messages, and campaign progress on the home page
  (A11Y-006, A11Y-007, STU-006).
- A troubleshooting guide generated from the code's own error messages (Q-009).
- A bootstrap test for the SEM, and a glossary (SCI-004, Q-008).

### Changed
- The version is derived from the git tag (#48).
- `FieldInteraction` and `SurfaceEsp` pass the SMILES net charge to ferric.
- The danuglipron experiment and its tools moved in from ferric and are frozen; smeltery reads no ferric checkout
  (#13).
- `PairedPoses` reports which limit an MCS timeout hit.
- Structure fetch provenance is recorded outside `input_digest`, so re-fetching the same structure does not change a
  run's identity (SCI-007).
- Artifact digests are validated when a record is read (ENT-006, PROV-009).
- Cancelling a task kills the whole process group, not just the parent (HPC-005).
- The frontend typecheck covers every tracked file under `ui/`, including the `gen:api` script, and a test fails if a
  new file escapes both tsconfig projects (Q-005).

### Fixed
- The Temporal ddE workflow's heartbeat and resume (67 of 87 rows were self-inflicted failures) (#122).
- Intermittent "database is locked" from the Bench sqlite adapters: WAL, a 30 s busy timeout, and connections that
  are closed.
- The PoseBusters gate segfault on CPython 3.12.11 and later (#68).
- A failing `pdb2pqr30` shim is now treated as "not installed" (#50).
- Exact-float comparisons in the tyk2 result tests.

## [0.0.1] - 2026-10-05

### Added
- First release: a tiered screening funnel that ranks only what it can resolve, built on paired differences
  against a parent (ddE) with an SEM and a systematic floor.
- PyPI packaging, installable from the index and identified when installed, released on a version tag through
  Trusted Publishing.

[Unreleased]: https://github.com/mgoldey/smeltery/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mgoldey/smeltery/compare/v0.0.1...v0.1.0
[0.0.1]: https://github.com/mgoldey/smeltery/releases/tag/v0.0.1
