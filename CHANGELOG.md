# Changelog

All notable changes to smeltery are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are derived from git tags
(hatch-vcs), and the project does not yet follow a stable-API promise. Entries are grouped from the merged
pull requests; numbers refer to issues or pull requests on the smeltery tracker.

## [Unreleased]

Nothing yet.

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
