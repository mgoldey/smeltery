# The core run record (`attestation.run/1`, v1-DRAFT)

smeltery writes a second record beside `OUT/run-<digest12>.json`: the cross-producer core record that attestation
reads, defined by the `attestation-schema` distribution (spec: attestation's
`docs/superpowers/specs/2026-11-19-core-run-record-design.md`, pinned at commit `314e4ff`, the revision that answered
smeltery's findings below; first written against `93bb5ab`). smeltery depends on the
stdlib-only schema package, never on `attestation`. The schema is a **draft**: records written now may stop reading if
it changes before the freeze. This page is smeltery's half of the Phase A fit check.

## Install, write, convert

- `smeltery[attestation]` (Python >= 3.11, smeltery's own floor; CI's 3.11 `test` job installs it, so the core
  record tests run there). attestation-schema is not on an index yet: `uv sync --extra attestation` resolves it from the pinned git commit in `[tool.uv.sources]`; with pip, install
  that pin first (`scripts/wheel_smoke.py` `PREINSTALL` has the exact requirement).
- Every finished `smeltery run` (plain, `--combine`, `--temporal`) writes it last, into OUT. Without the extra the run
  prints one `note:` line and is otherwise unchanged; a schema refusal is a `warning:`, never the run's exit code (the
  legacy record is written and stays the source of truth while the schema is a draft). A rerun with `--force`
  replaces it. Partial (`--only-candidate`) records get none: a partial is not a finished run.
- An existing run: `smeltery record core OUT` (or `.../run-<digest12>.json`), `--force` to replace.
- Library: `smeltery.core_record.write_core(path, ...)`; the mapping is the pure `core_fields(...)`.

## Layout: OUT is the run directory

```
OUT/run-<digest12>.json, poses/, calcs.jsonl, config.toml, inputs/, structure/ ...   unchanged (gui-contract.md)
OUT/steps/NN-<stage>/stage.json   per funnel stage, execution order: its funnel row + its stage-log rows (optional in the spec)
OUT/attestation/digests.json      sha256 of every file in OUT outside attestation/
OUT/attestation/run.json          the core record, written last, only through attestation_schema.write
```

OUT itself is the run directory, not a sibling: the manifest then binds every file the run left (poses, SCF log,
staged inputs, the legacy record) to the core record through one digest, and nothing the GUI contract names moves.
Only new names are added to OUT, and no reader of OUT globs them (`run-*.json` does not match `attestation/run.json`).

## Mapping

| Core field | From | Notes |
|---|---|---|
| `run_id` | `input_digest[:12]` | Same as the legacy file name. Identifies the inputs, not the execution (finding 8). |
| `family` | `campaign` | |
| `arm` | `null` | smeltery has no arm. `benzoic-rhf` and `benzoic-pbe` share campaign `mwe-benzoic-cli` and are really two arms; nothing names them, so nothing is invented. |
| `status` | `"complete"` | A legacy record exists only for a finished run. Failed runs write no core record yet (follow-up). |
| `started_at`, `finished_at` | live run: the `smeltery run` start and the moment the core record is written, both through `attestation_schema.format_timestamp` (`core_record.utc_now`); conversion, `--combine`, `--temporal`: `null` | The legacy `started_utc` is neither (stamped after the measurement tier; under `--temporal`, the earliest SCF's start). It is kept as `ext.smeltery.started_utc` under its own name. |
| `producer` | `{"smeltery", <installed version>}`; a conversion is `smeltery record-core` | |
| `code` | the legacy `smeltery` identity: `commit` -> `git_sha`, `dirty`, `git_remote` -> `repo`, `version` (else the legacy `smeltery_version`), `provenance` from the sentence's ladder level, the sentence as `evidence` | The same ladder as a dependency: an editable install is `inferred`. The install's `file://` source names a machine, not a repository, and is dropped. `version` is never the converter's. |
| `config` | the legacy `inputs` (what `input_digest` hashes, with the tiers) | Opaque to attestation, so the candidates' SMILES live here verbatim. |
| `data` | role `input`: the staged config, its staged inputs, the receptor, the decisions file, a tier's pocket input, one entry per content digest; role `output`: the legacy record file; role `manifest`: `attestation/digests.json` | All digests are the ones the legacy record already holds; nothing is re-hashed except the legacy file and the manifest. |
| `metrics` | `stage.candidates_in` / `stage.survivors` with `labels: {"stage": ...}` per funnel stage; `cut.survivors`; `paired_ddE.mean` / `.sem` / `.n` with `labels: {"analogue": ...}` | What a value is about is a label, never part of the name. Only the paired ddE mean has a direction (`"min"`): the cut sorts ascending mean, best group first, so smeltery genuinely knows it. Counts, sem and n have none. `unit`: mean and sem carry the record's `results.units["dE_int"]` (`kcal/mol`), the quantity they pair; counts and n are `null`; a record that names no unit gives `null`, never an assumed one. An undefined value is `"NaN"` (computed, undefined): a one-pose sem, and an unranked cut's survivor count (`candidates_out: null` beside `unranked: true`, which goes to `ext.smeltery.cut_unranked`). A count a stage does not keep (`structure`) is absent: not reported. |
| `history` | `[]` | smeltery logs no curves; every value it records is final. |
| `seeds` | `{tier: seed}` from each tier's integer `seed`, `{tier: [seed, ...]}` from a tier's seed list (docking: `{"docking": [7]}`) | `null` (unknown) when no tier records one, when a seed list is not a non-empty list of integers, or when two tiers of one name disagree: never a partial object. |
| `dependencies` | ferric (the legacy identity; `unknown`, naming the builds, when `results.builds` lists more than one ferric: finding 10), Python (`host.python`), then rdkit, numpy, and every optional package the run imported, each through `record.package_identity`; binaries only a tier recorded (xtb, pdb2pqr) | One mapping for every package (below). A conversion uses only what the record says: unrecorded core packages are `unknown`, never filled from today's environment. |
| `ext.smeltery` | `tiers`, `input_digest`, `host`, `started_utc`, `legacy_schema_version`, `cut_unranked` | Everything chemistry-shaped (tier settings: method, basis, functional, docking settings) is here, and the cut's `unranked` flag (a flag is not a metric). |

### Dependency identity: one ladder, no special case

`record.package_identity(dist, module)` is the ladder `ferric_identity()` always used, generalised: a package's own
build stamp (`<module>.__build__`, ferric is the one that has one) is VERIFIED; else `direct_url.json` (git install:
VERIFIED with the commit; local directory: INFERRED from its HEAD; a wheel file: INFERRED with pip's hash); else an
index install is VERIFIED by its immutable version. New: a package installed by something other than pip or uv
(conda, a distro) is INFERRED, because a version string from another channel is not an immutable index release.
`core_record.dependency(name, identity)` maps every result the same way: `VERIFIED...` -> `verified`, `INFERRED...` ->
`inferred`, anything else -> `unknown`; `build_sha` = the commit when it is 40/64 hex; `evidence` = the provenance
sentence verbatim; `source` = where it was installed from, from the identity's own `source`: `index` (a package index;
which one is not recorded), `git+<url>` (a git install, credentials stripped), `file` (a local directory or wheel file:
the path names a machine and is not written), the installer for a conda/distro install, and `null` for a stamped build
(`ferric.__build__` says how the build was identified, not where it was installed from) and for everything known only
from a version string. A version a tier recorded at run time (`rdkit.__version__`, `xtb --version`) is `inferred`: the
tier read a string, not where the package came from. Python is `verified` from `platform.python_version()`.

## Schema findings and what became of them

smeltery's first fit check (against `93bb5ab`) filed thirteen findings, each handled by leaving a value out rather than
bending another field. The schema owner answered them in the spec's "Producer findings" (smeltery section) and
`attestation_schema` at `314e4ff`; smeltery re-pinned there and removed every leave-it-out that the schema now has a
slot for. Status per finding:

| # | Finding | Status | What smeltery writes now |
|---|---|---|---|
| 1 | JCS has no int/float distinction, so `input_digest` cannot be re-derived from the core record | Resolved by spec text (the proposed `config_digest` was rejected) | Unchanged: `input_digest` in `ext.smeltery`, checkable against the legacy record bound in `data`. The spec now says a producer digest is checkable from the record only if taken over JCS bytes. |
| 2 | `code` has no provenance | Resolved by schema change (`code.provenance`, `code.evidence`) | `code.provenance` from the identity's ladder level (an editable install is `inferred`), the sentence as `evidence`. |
| 3 | `code` has no version | Resolved by schema change (`code.version`) | The identity's version, else the legacy `smeltery_version`; never the converter's. |
| 4 | Metrics have no unit | Resolved by schema change (`metrics[].unit`) | ddE mean and sem: `results.units["dE_int"]` (`kcal/mol`); counts and n: `null`; never assumed. |
| 5 | Metrics have no slot for the entity a value is about | Resolved by schema change (`metrics[].labels`, part of the uniqueness key) | `paired_ddE.mean|sem|n` with `{"analogue": ...}`; the funnel counts likewise moved their stage out of the name: `stage.candidates_in|survivors` with `{"stage": ...}`. |
| 6 | No undefined or boolean metric value | Resolved by spec text (absent = not reported, `"NaN"` = computed and undefined, flags go in `ext`), using the non-finite values MLflow's findings added | A one-pose sem and an unranked cut's survivor count are `"NaN"`; the cut's `unranked` flag is `ext.smeltery.cut_unranked`; a count a stage does not keep stays absent. |
| 7 | `seeds` cannot hold a list | Resolved by schema change (`{name: int \| [int, ...]}`) | Docking's `{"docking": [7]}` beside `{"paired_poses": 20261005}`; `seeds` is no longer `null` for docking runs. |
| 8 | `run_id` is not unique for smeltery | Resolved by spec text (same `(family, run_id)` = re-executions, both kept, told apart by `record_digest`) | Unchanged: `<digest12>`. |
| 9 | `Dependency` has no `source` | Resolved by schema change (`dependencies[].source`, credentials refused) | `index`, `git+<url>`, `file`, the installer, or `null` (mapping above). |
| 10 | One identity per dependency name; a `--temporal` run can mix ferric builds | Still deferred (per-scope identities), with a rule in the spec meanwhile: a mixed dependency is `unknown`, `evidence` naming the mix | Adopted the rule: a run whose `results.builds` lists more than one ferric is `unknown` with the builds and their row counts in `evidence`, instead of the finalizing process's ferric. The committed `danuglipron-halogen` run is one (two ferric builds: 1 and 111 rows). |
| 11 | The layout assumes per-step outputs | Resolved by spec text (`steps/` and `logs/` optional; outputs anywhere under the run directory, covered by the manifest) | Unchanged layout: `steps/NN-<stage>/stage.json` kept, no `logs/`, outputs where the GUI contract puts them. |
| 12 | `requires-python >= 3.12` without a 3.12 need | Resolved by schema change (floor 3.11, tested in the schema's CI) | The extra has no marker; CI's 3.11 `test` job installs it, the 3.11 wheel smoke converts two prebuilt runs instead of checking a refusal. |
| 13 | Not on an index | Still deferred (the spec's Open decision 2) | Unchanged: `[tool.uv.sources]` git pin; `scripts/wheel_smoke.py` `PREINSTALL` (and `preinstall`, which CI's test job installs from) has the same pin. |

## Open findings against `314e4ff`

Found while adopting the revision; each is left as it is in smeltery (not worked around) for the schema owner.

1. **Mixed builds of the experiment code itself.** The spec's mixed-build rule covers `dependencies`; `code` has the same
   problem and no rule. `danuglipron-halogen`'s SCF rows were written by two smeltery commits (`1303507`, 1 row;
   `f3d6edf`, 111 rows), and `code` states the finalizing process's `1303507` (`inferred`). smeltery leaves `code` as
   that identity rather than invent a rule.
2. **The one-spelling timestamp does not sort as text.** With no trailing fraction zeros, `...:37.5Z` < `...:37Z`
   lexically, though it is later; fixed-width RFC 3339 sorts. smeltery's own live-run test compared the strings and had
   to parse them instead. The spec should say timestamps are compared parsed, never as text (or keep the fraction
   fixed-width).
3. **`from_dict` and the model disagree on a non-finite value's type.** The model holds `float('nan')` and `dumps`
   spells it; `from_dict` takes JSON values and refuses that float, and the spelling helper is private. A producer that
   builds the JSON (smeltery's pure `core_fields`) spells `"NaN"` itself (`core_record.wire_value`).
4. **`source` has no vocabulary.** Suggested values are `pypi`, `git+https://...`, `conda-forge`, `file`. smeltery
   knows "a package index" but not which one (`index`), knows the installer (`conda`) but not the channel, and writes
   `file` without the machine path. Readers filtering on `source` will meet each producer's spelling.

Not schema findings, but found on the way and left as they are: the legacy `started_utc` is not a start (above); the
legacy record has no run-level failed state, so failed runs get no core record; a build-stamped dependency's
`source` is `null` because `record.package_identity` stops at the stamp and does not also read where the package was
installed from.
