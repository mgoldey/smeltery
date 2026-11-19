# The core run record (`attestation.run/1`, v1-DRAFT)

smeltery writes a second record beside `OUT/run-<digest12>.json`: the cross-producer core record that attestation
reads, defined by the `attestation-schema` distribution (spec: attestation's
`docs/superpowers/specs/2026-11-19-core-run-record-design.md`, pinned at commit `93bb5ab`). smeltery depends on the
stdlib-only schema package, never on `attestation`. The schema is a **draft**: records written now may stop reading if
it changes before the freeze. This page is smeltery's half of the Phase A fit check.

## Install, write, convert

- `smeltery[attestation]` (needs Python >= 3.12; on 3.11 the extra is empty). attestation-schema is not on an index
  yet: `uv sync --extra attestation` resolves it from the pinned git commit in `[tool.uv.sources]`; with pip, install
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
OUT/steps/NN-<stage>/stage.json   per funnel stage, execution order: its funnel row + its stage-log rows
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
| `started_at`, `finished_at` | live run: the `smeltery run` start and the moment the core record is written; conversion, `--combine`, `--temporal`: `null` | The legacy `started_utc` is neither (stamped after the measurement tier; under `--temporal`, the earliest SCF's start). It is kept as `ext.smeltery.started_utc` under its own name. |
| `producer` | `{"smeltery", <installed version>}`; a conversion is `smeltery record-core` | |
| `code` | the legacy `smeltery` identity: `commit` -> `git_sha`, `dirty`, `git_remote` -> `repo` | The install's `file://` source names a machine, not a repository, and is dropped. See findings 2, 3. |
| `config` | the legacy `inputs` (what `input_digest` hashes, with the tiers) | Opaque to attestation, so the candidates' SMILES live here verbatim. |
| `data` | role `input`: the staged config, its staged inputs, the receptor, the decisions file, a tier's pocket input, one entry per content digest; role `output`: the legacy record file; role `manifest`: `attestation/digests.json` | All digests are the ones the legacy record already holds; nothing is re-hashed except the legacy file and the manifest. |
| `metrics` | `stage.<stage>.candidates_in` / `.survivors` per funnel stage; `cut.survivors`; `paired_ddE.<analogue>.mean` / `.sem` / `.n` | Only the paired ddE mean has a direction (`"min"`): the cut sorts ascending mean, best group first, so smeltery genuinely knows it. Counts, sem and n have none. Non-finite values and an unranked cut's `null` count are left out. See findings 4-6. |
| `seeds` | `{tier: seed}` from each tier's integer `seed` | `null` (unknown) when no tier records one, or when one records a seed list (docking): finding 7. |
| `dependencies` | ferric (the legacy identity), Python (`host.python`), then rdkit, numpy, and every optional package the run imported, each through `record.package_identity`; binaries only a tier recorded (xtb, pdb2pqr) | One mapping for every package (below). A conversion uses only what the record says: unrecorded core packages are `unknown`, never filled from today's environment. |
| `ext.smeltery` | `tiers`, `input_digest`, `host`, `started_utc`, `legacy_schema_version` | Everything chemistry-shaped (tier settings: method, basis, functional, docking settings) is here. |

### Dependency identity: one ladder, no special case

`record.package_identity(dist, module)` is the ladder `ferric_identity()` always used, generalised: a package's own
build stamp (`<module>.__build__`, ferric is the one that has one) is VERIFIED; else `direct_url.json` (git install:
VERIFIED with the commit; local directory: INFERRED from its HEAD; a wheel file: INFERRED with pip's hash); else an
index install is VERIFIED by its immutable version. New: a package installed by something other than pip or uv
(conda, a distro) is INFERRED, because a version string from another channel is not an immutable index release.
`core_record.dependency(name, identity)` maps every result the same way: `VERIFIED...` -> `verified`, `INFERRED...` ->
`inferred`, anything else -> `unknown`; `build_sha` = the commit when it is 40/64 hex; `evidence` = the provenance
sentence verbatim. A version a tier recorded at run time (`rdkit.__version__`, `xtb --version`) is `inferred`: the
tier read a string, not where the package came from. Python is `verified` from `platform.python_version()`.

## Schema findings (what smeltery needed that `attestation.run/1` cannot say)

Each is handled by leaving the value out of the core fields (it stays in the legacy record, which `data` binds by
digest), never by bending another field. Proposed changes are for attestation to decide; attestation was not edited.

1. **JCS loses the int/float distinction, so `input_digest` cannot be re-derived from the core record.** smeltery's
   `input_digest` is sha256 over Python's `json.dumps(sort_keys=True)` of inputs and tiers, which spells `2.0` and
   `2` differently; RFC 8785 writes both as `2`, and the reader returns an int. Measured on the golden run:
   `digest({"inputs": config, "tiers": ext.smeltery.tiers})` from the core record != `input_digest`. Any producer
   that hashes its own config with a non-JCS serializer hits this. Proposed: say in the spec that `config`/`ext`
   values are JSON numbers (no int/float distinction) and that a producer digest over them must be computed over
   JCS bytes to be checkable; or add an optional `config_digest {algorithm, value}` for the producer's own digest.
   Handled: `input_digest` is carried in `ext.smeltery` as smeltery's claim; it verifies only against the legacy
   record (bound in `data`).
2. **`code` has no provenance.** smeltery's own identity is usually INFERRED (an editable install reports the
   checkout's HEAD *now*, which may differ from what was imported), exactly the uncertainty `dependencies` models with
   `provenance`/`evidence`. `code.git_sha` states it with no qualifier. Proposed: give `code` the same
   `provenance` + `evidence` pair as a dependency. Handled: written as the spec's fit check prescribes (commit, dirty,
   remote); the INFERRED qualifier is lost in the core fields and kept in the legacy record.
3. **`code` has no version.** A non-editable install has no commit, only a version (`0.0.2.dev383+g...`); and a
   conversion's `producer.version` is today's smeltery, not the one that ran. Proposed: `code.version` (nullable).
   Handled: left out (the legacy `smeltery_version` / `smeltery.version` keep it).
4. **Metrics have no unit.** `paired_ddE` is in kcal/mol (the legacy `results.units`). Proposed: `unit` (nullable,
   free string or UCUM). Handled: left out; a reader cannot compare across producers without it.
5. **Metrics have no slot for the entity a value is about.** ddE is per analogue; the only per-row qualifiers are
   `step` (training step) and `split` (data split), neither of which a candidate is. Handled the way the spec handles
   tiers: the entity goes in the name (`paired_ddE.<analogue>.mean`), so the analogue's label, not its chemistry,
   reaches a core field. Proposed: an optional `labels: {str: str}` (like Prometheus/MLflow tags) so a reader can
   group by candidate without parsing names.
6. **No undefined or boolean metric value.** An unranked cut has `candidates_out: null` and `unranked: true`; a
   one-pose analogue's sem is NaN. `value` must be a finite number. Handled: left out (a metric's absence is
   ambiguous between "not measured" and "undefined"). Proposed: nothing for booleans (they are not metrics), but the
   spec could say absence is the encoding of "undefined" so readers do not treat it as "not measured".
7. **`seeds` cannot hold a list.** Docking records `seeds: [7]` (one Vina run per seed). `{name: int}` has no slot,
   and naming them `docking.0` would invent names. A partial object would claim the rest were unseeded, since `{}`
   means "no seed was set". Handled: `seeds` is `null` (unknown) for any run whose tiers record a seed list.
   Proposed: `{name: int | [int]}`, or state that `seeds` may be partial.
8. **`run_id` uniqueness is not something smeltery's id has.** `<digest12>` identifies the *inputs*: two runs of one
   config (another OUT, or `--force`) share it within a family, as the spec's fit check suggests using it. Proposed:
   the spec should say what a reader does with two records sharing `(family, run_id)` (both valid: same inputs,
   different executions; told apart by `record_digest`), or require uniqueness and let smeltery suffix a time.
   Handled: `<digest12>`, unchanged, matching the legacy file name.
9. **`Dependency` has no `source`.** Where a package came from (index, git URL, local path, `ferric.__build__`) is
   only in the free-text `evidence`. The spec's own example puts the bare source (`"ferric.__build__"`) in `evidence`;
   smeltery puts the whole provenance sentence there, as asked. Proposed: a nullable `source` beside `evidence`.
   Handled: the sentence carries it (the local path of a directory install is not in the sentence and is dropped).
10. **One identity per dependency name.** A `--temporal` run's SCFs can be written by several ferric builds
    (`results.builds`; mixed builds are refused unless allowed). `dependencies` names are unique, so one record can
    state one ferric. Handled: the finalizing process's ferric. Proposed: allow repeated names with a role/scope, or
    say a run with mixed builds of a dependency records it as `unknown`.
11. **The layout assumes per-step outputs.** smeltery's outputs span stages (one SDF per candidate across the pose
    stages, one SCF log across measurement and analyses) and the GUI contract fixes where they are; moving them into
    `steps/NN-<name>/` would break every viewer. An empty step directory is invisible to the manifest (files only).
    Handled: each `steps/NN-<stage>/` holds only `stage.json` (the stage's funnel row and log rows as recorded);
    outputs stay put and are covered by the manifest. `logs/` is not written: smeltery keeps no per-step logs, and
    a log reconstructed from stage summaries would be fabricated. Proposed: the spec should say `steps/` and `logs/`
    are optional and outputs may live anywhere under the run directory.
12. **`requires-python >= 3.12` without a 3.12 need.** The package is stdlib-only, and its reader and writer
    round-trip the golden record unchanged on CPython 3.11.15 (checked by importing the installed package under 3.11).
    smeltery supports 3.11 and its wheel-smoke CI runs 3.11, so the extra is empty there. Proposed: `>= 3.11` after
    running the schema's own tests on 3.11. Handled: an environment marker on the extra, and a 3.11 smoke check that
    the converter refuses with its notice.
13. **Not on an index.** A pip user of `smeltery[attestation]` cannot resolve it; only uv (through
    `[tool.uv.sources]`) or a manual pinned install can. Proposed: publish (spec Open decision 2). Handled:
    `scripts/wheel_smoke.py` installs the exact pin first (`PREINSTALL`, checked equal to the uv source by a test).

Not schema findings, but found on the way and left as they are: the legacy `started_utc` is not a start (above); the
legacy record has no run-level failed state, so failed runs get no core record.
