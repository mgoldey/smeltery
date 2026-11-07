# Changelog

All notable changes to smeltery are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versions are derived from git tags
(hatch-vcs), and the project does not yet follow a stable-API promise. Entries are grouped from the merged
pull requests; numbers refer to issues or pull requests on the smeltery tracker.

## [Unreleased]

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

### Changed
- The version is derived from the git tag (#48).
- `FieldInteraction` and `SurfaceEsp` pass the SMILES net charge to ferric.
- The danuglipron experiment and its tools moved in from ferric and are frozen; smeltery reads no ferric checkout
  (#13).
- `PairedPoses` reports which limit an MCS timeout hit.

### Fixed
- The Temporal ddE workflow's heartbeat and resume (67 of 87 rows were self-inflicted failures) (#122).
- The PoseBusters gate segfault on CPython 3.12.11 and later (#68).
- A failing `pdb2pqr30` shim is now treated as "not installed" (#50).
- Exact-float comparisons in the tyk2 result tests.

## [0.0.1] - 2026-10-05

### Added
- First release: a tiered screening funnel that ranks only what it can resolve, built on paired differences
  against a parent (ddE) with an SEM and a systematic floor.
- PyPI packaging, installable from the index and identified when installed, released on a version tag through
  Trusted Publishing.

[Unreleased]: https://github.com/mgoldey/smeltery/compare/v0.0.1...HEAD
[0.0.1]: https://github.com/mgoldey/smeltery/releases/tag/v0.0.1
