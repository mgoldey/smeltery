# The core run record (`attestation.run/1`, v1-DRAFT)

smeltery writes a second record beside `OUT/run-<digest12>.json`: the cross-producer core record that attestation
reads, defined by the `attestation-schema` distribution (spec: attestation's
`docs/superpowers/specs/2026-11-19-core-run-record-design.md`, pinned at commit `f809522`, the revision that answered
smeltery's second round below; first written against `93bb5ab`, then `314e4ff`). smeltery depends on the
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
| `code` | the legacy `smeltery` identity: `commit` -> `git_sha`, `dirty`, `git_remote` -> `repo`, `version` (else the legacy `smeltery_version`), `provenance` from the sentence's ladder level, the sentence as `evidence` | The same ladder as a dependency: an editable install is `inferred`. The install's `file://` source names a machine, not a repository, and is dropped. `version` is never the converter's. **Mixed code** (finding 9): when `results.builds` names more than one smeltery commit, `provenance` is `unknown`, `git_sha` and `dirty` are `null`, and `evidence` is `mixed: <sha> (N rows), <sha> (M rows)`, rows with no smeltery recorded not counted; `version` is `null`, because a build row records a commit and not a version, so smeltery cannot tell that every commit shared one. The committed `danuglipron-halogen` run is one (`1303507...`, 1 row; `f3d6edf...`, 111 rows). The per-build breakdown stays in the legacy record, which `data` binds. |
| `config` | the legacy `inputs` (what `input_digest` hashes, with the tiers) | Opaque to attestation, so the candidates' SMILES live here verbatim. |
| `data` | role `input`: the staged config, its staged inputs, the receptor, the decisions file, a tier's pocket input, one entry per content digest; role `output`: the legacy record file; role `manifest`: `attestation/digests.json` | All digests are the ones the legacy record already holds; nothing is re-hashed except the legacy file and the manifest. |
| `metrics` | `stage.candidates_in` / `stage.survivors` with `labels: {"stage": ...}` per funnel stage; `cut.survivors`; `paired_ddE.mean` / `.sem` / `.n` with `labels: {"analogue": ...}` | What a value is about is a label, never part of the name. Only the paired ddE mean has a direction (`"min"`): the cut sorts ascending mean, best group first, so smeltery genuinely knows it. Counts, sem and n have none. `unit`: mean and sem carry the record's `results.units["dE_int"]` (`kcal/mol`), the quantity they pair; counts and n are `null`; a record that names no unit gives `null`, never an assumed one. An undefined value is `"NaN"` (computed, undefined): a one-pose sem, and an unranked cut's survivor count (`candidates_out: null` beside `unranked: true`, which goes to `ext.smeltery.cut_unranked`). A count a stage does not keep (`structure`) is absent: not reported. |
| `history` | `null` | smeltery logs no curves; every value it records is final, so no `attestation/history.jsonl` is written. |
| `seeds` | `{tier: seed}` from each tier's integer `seed`, `{tier: [seed, ...]}` from a tier's seed list (docking: `{"docking": [7]}`) | `null` (unknown) when no tier records one, when a seed list is not a non-empty list of integers, or when two tiers of one name disagree: never a partial object. |
| `dependencies` | ferric (the legacy identity; `unknown`, naming the builds, when `results.builds` lists more than one ferric: finding 10), Python (`host.python`, `source: "system"`), then rdkit, numpy, and every optional package the run imported, each through `record.package_identity`; binaries only a tier recorded (xtb, pdb2pqr) | Always a list, never `null`: smeltery does observe its environment (a live run measures it; a conversion reads what the record measured). One mapping for every package (below). A conversion uses only what the record says: a core package the record does not identify (rdkit, numpy) is `unknown` with `evidence` saying smeltery imports it on every funnel run and the record does not say which build, never filled from today's environment and never an empty placeholder. An optional package a converted record does not name is left out (whether the run imported it is not known; open finding 2 against `f809522`). A failed/cancelled job's record (`smeltery.jobs`) writes `dependencies: null`: the backend did not observe the run. |
| `ext.smeltery` | `tiers`, `input_digest`, `host`, `started_utc`, `legacy_schema_version`, `cut_unranked` | Everything chemistry-shaped (tier settings: method, basis, functional, docking settings) is here, and the cut's `unranked` flag (a flag is not a metric). |

### Dependency identity: one ladder, no special case

`record.package_identity(dist, module)` is the ladder `ferric_identity()` always used, generalised: a package's own
build stamp (`<module>.__build__`, ferric is the one that has one) is VERIFIED; else `direct_url.json` (git install:
VERIFIED with the commit; local directory: INFERRED from its HEAD; a wheel file: INFERRED with pip's hash); else an
index install is VERIFIED by its immutable version. New: a package installed by something other than pip or uv
(conda, a distro) is INFERRED, because a version string from another channel is not an immutable index release.
`core_record.dependency(name, identity)` maps every result the same way: `VERIFIED...` -> `verified`, `INFERRED...` ->
`inferred`, anything else -> `unknown`; `build_sha` = the commit when it is 40/64 hex; `evidence` = the provenance
sentence verbatim; `source` = where it was installed from, from the identity's own `source`, in the schema's closed
vocabulary (finding 12): `index` (a package index; which one is not recorded, so never `index:pypi`), `git+<url>` (a
git install, credentials stripped), `file` (a local directory or wheel file: the path names a machine and is not
written), `conda` for a conda, mamba or micromamba install (the channel is not recorded), `system` for any other
installer (a distro package manager) and for Python itself, and `null` for a stamped build (`ferric.__build__` says how
the build was identified, not where it was installed from), for everything known only from a version string (a tier's
`xtb --version`: smeltery cannot tell a system xtb from a conda one), and for a wheel fetched from a URL (no kind in the
vocabulary: open finding 1 against `f809522`). A version a tier recorded at run time (`rdkit.__version__`, `xtb --version`) is `inferred`: the
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
| 9 | `Dependency` has no `source` | Resolved by schema change (`dependencies[].source`, credentials refused) | At `314e4ff`: `index`, `git+<url>`, `file`, the installer, or `null`; at `f809522` the closed vocabulary (finding 12 below). |
| 10 | One identity per dependency name; a `--temporal` run can mix ferric builds | Still deferred (per-scope identities), with a rule in the spec meanwhile: a mixed dependency is `unknown`, `evidence` naming the mix | Adopted the rule: a run whose `results.builds` lists more than one ferric is `unknown` with the builds and their row counts in `evidence`, instead of the finalizing process's ferric. The committed `danuglipron-halogen` run is one (two ferric builds: 1 and 111 rows). |
| 11 | The layout assumes per-step outputs | Resolved by spec text (`steps/` and `logs/` optional; outputs anywhere under the run directory, covered by the manifest) | Unchanged layout: `steps/NN-<stage>/stage.json` kept, no `logs/`, outputs where the GUI contract puts them. |
| 12 | `requires-python >= 3.12` without a 3.12 need | Resolved by schema change (floor 3.11, tested in the schema's CI) | The extra has no marker; CI's 3.11 `test` job installs it, the 3.11 wheel smoke converts two prebuilt runs instead of checking a refusal. |
| 13 | Not on an index | Still deferred (the spec's Open decision 2) | Unchanged: `[tool.uv.sources]` git pin; `scripts/wheel_smoke.py` `PREINSTALL` (and `preinstall`, which CI's test job installs from) has the same pin. |

## Second round (filed against `314e4ff`) and what became of it

smeltery filed four findings while adopting `314e4ff` (numbered on from the first round in the spec's "Producer
findings", smeltery second round). The schema owner accepted all four at `f809522`, together with two user-directed
changes; smeltery re-pinned there.

| # | Finding | Status at `f809522` | What smeltery writes now |
|---|---|---|---|
| 9 | Mixed builds of the experiment code itself: `danuglipron-halogen`'s rows came from two smeltery commits, and `code` could state only one | Resolved by schema change: `code` takes the dependency rule (`unknown`, `git_sha: null`, `evidence` naming each commit and its row count), and the reader refuses `unknown` with a `git_sha` or `build_sha` | `code` for a run whose `results.builds` names more than one smeltery commit: `unknown`, `git_sha`/`dirty`/`version` `null`, `evidence` `mixed: 130350724068f91bcfb8fea8b9d399c90886e67d (1 row), f3d6edf945d5c96ffe1d97a183dda914e911efbb (111 rows)` for `danuglipron-halogen`. The mixed-ferric entry was already `build_sha: null`, so the new refusal changed nothing there. |
| 10 | The one-spelling timestamp did not sort as text (`...:37.5Z` < `...:37Z`) | Resolved by schema change: always exactly six fraction digits | Unchanged code (`format_timestamp` everywhere: the record's `started_at`/`finished_at`, and `state.json`'s `updated_at` and `history[].at` in `smeltery.jobs`, including the Temporal workflow's start time); the live-run test now compares the two timestamps as text, as the spec now allows. |
| 11 | `from_dict` and the model disagreed on a non-finite value's type, and the spelling helper was private | Resolved: `attestation_schema.spell_value` is public and `RunRecord.to_dict()` returns the wire form; `from_dict` still takes only JSON | `core_record.wire_value` (smeltery's copy) is deleted; `metrics` spells values through `attestation_schema.spell_value`. |
| 12 | `source` had no vocabulary | Resolved by schema change: `index[:<name or url>]`, `git+<url>`, `file`, `conda[:<channel>]`, `system`, or `null` (reader-enforced) | The mapping above. Python is now `system`; a distro install is `system`; a conda-family install is `conda`; a wheel from a URL is `null` (open finding 1 below). |

The two user-directed changes, adopted:

- **`history` is a sidecar reference** (`null` or `{path, sha256, series, points}` for `attestation/history.jsonl`).
  smeltery has no curves, so it writes `null` and no sidecar; `HistoryWriter` / `write_history` are not needed. A
  failed/cancelled job's record also writes `null`.
- **`dependencies` is nullable** (`null` = not observed, `[]` = observed, none). smeltery's own records always observe
  (a list); the job backend's failed/cancelled record writes `null`, now unconditionally: `smeltery.jobs`'s
  `_NULLABLE` branch, which wrote `[]` for the schema before `314e4ff`, is deleted.

## Open findings against `f809522`

Found while adopting `f809522`; each is left as it is in smeltery (not worked around) for the schema owner.

1. **`dependencies[].source` has no kind for a direct-URL archive.** `pip install https://host/x-1.0-py3-none-any.whl`
   leaves a `direct_url.json` with `archive_info` and an `https://` URL. It is not an index (`index:<url>` would claim
   one), not `file` (the spec defines that as a local path or wheel), and not `git+`. smeltery writes `null`, and the URL is
   then recorded nowhere (`evidence` names only the wheel file and pip's hash). The same holds for a non-git VCS install (`hg+`,
   `svn+`), which `record.package_identity` labels a git install today. A `url` kind (`url:<url>`, credential-checked
   like `index:`) would cover both.
2. **A `dependencies` list cannot say it is partial.** The spec reads a list as "what the run ran on" (its argument for
   MLflow's `null`), but a converted smeltery record observes only part of the environment: ferric, Python and the
   versions tiers recorded, not the optional packages the run may have imported. smeltery lists the core packages it
   knows it imported as `unknown` with evidence (true: the run ran on them) and leaves the optional ones out, so the
   list is incomplete with no marker. Neither `null` (smeltery did observe ferric and Python) nor a complete list is
   honest; a record-level "observed in part" (or the list read as a lower bound, said in the spec) would be.

### Job-backend schema findings (open items for the schema freeze)

From implementing `JobBackend` twice (`smeltery.jobs`, [jobs.md](jobs.md)); none is worked around beyond what
jobs.md describes.

1. **`JobSpec.run_id` is not the record's `run_id`.** A job's `run_id` is chosen at submit (the run directory's
   identity); a complete smeltery record's `run_id` is the input digest `<digest12>`, known only once the run has
   staged its inputs. So `state.json` and the complete `run.json` in one run directory carry different `run_id`s,
   while a failed/cancelled record carries the job's. The spec should say which one names the run, or let the record
   carry both (the job's as a backend field).
2. **`config` changes meaning with the outcome.** A failed or cancelled record holds the campaign config the job was
   given (TOML, parsed: a run that did not finish never computed `inputs`); a complete record holds smeltery's
   `inputs` (what `input_digest` hashes). A reader comparing `config` across one family's records compares two
   different things. The spec should say which `config` a job's record holds, or that a backend's record carries the
   submitted config under its own name.
3. **`JobSpec.ext` has no path into a producer-written complete record.** The backend writes failed/cancelled
   records and could carry `spec.ext`, but the complete record is the run's own (`smeltery run` derives it), and
   nothing passes the spec's `ext` to it. smeltery therefore refuses a spec with `ext` (`check_spec`) rather than put
   it in one record and not the other. The contract needs a way to hand `ext` (and `family`/`arm`) to the producer, or
   should drop `ext` from `JobSpec`.
4. **Temporal does not push settle.** `settle` (end once, an existing record decides, record before state) runs when
   someone looks (`status`, `cancel`, `result`); the Temporal workflow does not write `state.json` when it ends, so a
   finished Temporal job's `state.json` keeps its last observed state (`submitted` or `running`) until someone looks,
   the failed/cancelled record is written only then, and the terminal entry's `at` (and `finished_at` of a record the
   backend writes) is the time of that look, not of the end. The contract does not say who must settle or when; it should say a backend settles at the end, or that
   `state.json` may lag and `run.json` is authoritative.
5. **`state.json`, its lock, `settle` and `terminal_record` belong in attestation_schema.** `read_state` /
   `init_state` / `transition` (with the history checks), `state_lock` (`flock` on `attestation/`), `settle` and the
   minimal failed/cancelled record builder (minus smeltery's `code`) are schema rules every backend needs, written in
   `smeltery.jobs` to move unchanged (jobs.md, "What attestation-schema should own").

The open findings smeltery listed against `314e4ff` (numbered 1-4 there) are findings 9-12 in the table above.

Not schema findings, but found on the way and left as they are: the legacy `started_utc` is not a start (above); the
legacy record has no run-level failed state, so failed runs get no core record; a build-stamped dependency's
`source` is `null` because `record.package_identity` stops at the stamp and does not also read where the package was
installed from.

### Stage-reuse schema findings (open items for the schema freeze)

From resumability (`smeltery.reuse`, [jobs.md](jobs.md#resumability-resubmitting-reuses-completed-stages)); each is
carried in `ext` rather than squeezed into a core field.

1. **No slot for "this stage was reused from another run".** A resubmitted run takes a completed stage's output from
   an earlier run instead of recomputing it. The core record has nowhere to say that, nor which run and which content
   digest it came from, so smeltery writes it to `ext.smeltery.reuse` (per stage: `digest`, `upstream`,
   `output_digest`, `reused`, `reused_from: {digest, run_id, run_dir, at}`) and to `steps/NN-<stage>/stage.json`
   under `reuse`. The spec should say whether reuse is provenance the core names (a per-step `reused_from`
   referencing another record's `(family, run_id, record_digest)`), or producer business.
2. **A cancelled or failed record cannot list the steps that completed.** The steps layout is optional and written
   only by a finished run; a backend's minimal record has no field for "these stages completed before the stop".
   smeltery writes `ext["smeltery.job"].stages_completed` (null when the run left no stage log, as a Temporal job
   does). A per-step status in the layout, or a record-level list, would let a reader tell a cancel before any work
   from a cancel one stage short of done.
3. **`started_at`/`finished_at` and wall-clock times of a reused stage.** A reused stage was not run by this record's
   run, so its time is not this run's; smeltery leaves its funnel `seconds` null. The record has no per-step time to
   say this with.
