# The core run record (`attestation.run/1`, frozen)

smeltery writes a second record beside `OUT/run-<digest12>.json`: the cross-producer core record that attestation
reads, defined by the `attestation-schema` distribution (spec: attestation's
`docs/superpowers/specs/2026-11-19-core-run-record-design.md`). smeltery is pinned at attestation commit **`9fb7eed`**
(`9fb7eed4e37f24cc9e4d7d66688223879d41d98b`, attestation-schema **0.1.0**), where `attestation.run/1` was **frozen**:
its fields, what its reader accepts and the bytes its writer emits no longer change, and any change is a new schema
version (the spec's "v1 freeze" and "Compatibility policy"). Earlier pins: `93bb5ab`, `314e4ff`, `f809522` (drafts).
smeltery depends on the stdlib-only schema package, never on `attestation`. This page is smeltery's half of the fit
check, and the record of every finding and what became of it.

## Install, write, convert

- `smeltery[attestation]` (`attestation-schema>=0.1.0`; Python >= 3.11, smeltery's own floor; CI's 3.11 `test` job
  installs it, so the core record tests run there). attestation-schema 0.1.0 from PyPI (the extra is
  `>=0.1.0,<0.2`: pre-1.0 minor releases may break the API): `uv sync --extra attestation` and `pip install
  smeltery[attestation]` both resolve it from the index, with nothing to install first.
- Every finished `smeltery run` (plain, `--combine`, `--temporal`) writes it last, into OUT. Without the extra the run
  prints one `note:` line and is otherwise unchanged; a schema refusal is a `warning:`, never the run's exit code (the
  legacy record is written and stays smeltery's own source of truth; a campaign job whose run wrote no core record ends
  `failed`). A rerun with `--force` replaces it. `--run-id ID` names the run (a campaign job passes its own: the job
  owns the run_id, below). Partial (`--only-candidate`) records get none: a partial is not a finished run.
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
| `run_id` | `input_digest[:12]`; the run's `--run-id` when given (a campaign job's `JobSpec.run_id`) | Without a job, the same as the legacy file name, identifying the inputs, not the execution (finding 8). Inside a job the submitter owns it (job finding 1, resolved): `state.json` and every terminal `run.json` carry the job's. |
| `family` | `campaign` | |
| `arm` | the submitter's (`smeltery run --arm`, a campaign job's `JobSpec.arm`), else `null` | smeltery has no arm OF ITS OWN, so it never invents one: `benzoic-rhf` and `benzoic-pbe` share campaign `mwe-benzoic-cli` and are really two arms, but nothing names them, so both records say `null`. A submitter that does know (`smeltery al`, whose arm is the loop's iteration -- [active-learning.md](active-learning.md)) passes it at submit, and `jobs.check_spec` holds it to a label. A named arm also emits `arm_metrics` (below). |
| `status` | `"complete"` | A legacy record exists only for a finished run. A failed or cancelled campaign job gets the job backend's record ([jobs.md](jobs.md)); a failed plain `smeltery run` writes none. Never `null`: smeltery ran the experiment, so it knows the outcome (the spec's 5.E reserves `null` for importers). |
| `started_at`, `finished_at` | live run: the `smeltery run` start and the moment the core record is written, both through `attestation_schema.format_timestamp` (`core_record.utc_now`); conversion, `--combine`, `--temporal`: `null` | The legacy `started_utc` is neither (stamped after the measurement tier; under `--temporal`, the earliest SCF's start). It is kept as `ext.smeltery.started_utc` under its own name. |
| `producer` | `{"smeltery", <installed version>}`; a conversion is `smeltery record-core` | |
| `code` | the legacy `smeltery` identity: `commit` -> `git_sha`, `dirty`, `git_remote` -> `repo`, `version` (else the legacy `smeltery_version`), `provenance` from the sentence's ladder level, the sentence as `evidence` | The same ladder as a dependency: an editable install is `inferred`. The install's `file://` source names a machine, not a repository, and is dropped. `version` is never the converter's. **Mixed code** (finding 9): when `results.builds` names more than one smeltery commit, `provenance` is `unknown`, `git_sha` and `dirty` are `null`, and `evidence` is `mixed: <sha> (N rows), <sha> (M rows)`, rows with no smeltery recorded not counted; `version` is `null`, because a build row records a commit and not a version, so smeltery cannot tell that every commit shared one. The committed `danuglipron-halogen` run is one (`1303507...`, 1 row; `f3d6edf...`, 111 rows). The per-build breakdown stays in the legacy record, which `data` binds. |
| `config` | the campaign config the run read, verbatim (parsed TOML): the staged `OUT/config.toml`, accepted only while its sha256 is the one the run recorded in `results.config`; a live run without `--out` (nothing staged) hands over the file it read | The same form with and without a job, in every terminal state (job finding 2, resolved): a job's `JobSpec.config` is exactly this staged config. Opaque to attestation, so the candidates' SMILES live here verbatim. **Unobservable config** (a conversion of a run that staged nothing, or whose staged file is gone, edited or unparseable): `config` is `{}` -- the schema has no `null` for it -- and `ext.smeltery.config_unobserved` says why, so `{}` is never read as "configured with nothing"; never the legacy `inputs` in its place, never today's file. Post-freeze item 4 below. |
| `data` | role `input`: the staged config, its staged inputs, the receptor, the decisions file, a tier's pocket input, one entry per content digest; role `output`: the legacy record file; role `manifest`: `attestation/digests.json` | All digests are the ones the legacy record already holds; nothing is re-hashed except the legacy file and the manifest. |
| `metrics` | `stage.candidates_in` / `stage.survivors` with `labels: {"stage": ...}` per funnel stage; `cut.survivors`; `paired_ddE.mean` / `.sem` / `.n` with `labels: {"analogue": ...}` | What a value is about is a label, never part of the name. Only the paired ddE mean has a direction (`"min"`): the cut sorts ascending mean, best group first, so smeltery genuinely knows it. Counts, sem and n have none. `unit`: mean and sem carry the record's `results.units["dE_int"]` (`kcal/mol`), the quantity they pair; counts and n are `null`; a record that names no unit gives `null`, never an assumed one. An undefined value is `"NaN"` (computed, undefined): a one-pose sem, and an unranked cut's survivor count (`candidates_out: null` beside `unranked: true`, which goes to `ext.smeltery.cut_unranked`). A count a stage does not keep (`structure`) is absent: not reported. |
| `history` | `null` | smeltery logs no curves; every value it records is final, so no `attestation/history.jsonl` is written. |
| `seeds` | `{tier: seed}` from each tier's integer `seed`, `{tier: [seed, ...]}` from a tier's seed list (docking: `{"docking": [7]}`) | `null` (unknown) when no tier records one, when a seed list is not a non-empty list of integers, or when two tiers of one name disagree: never a partial object. |
| `dependencies` | ferric (the legacy identity; `unknown`, naming the builds, when `results.builds` lists more than one ferric: finding 10), Python (`host.python`, `source: "system"`), then rdkit, numpy, and every optional package the run imported, each through `record.package_identity`; binaries only a tier recorded (xtb, pdb2pqr) | Always a list, never `null`: smeltery does observe its environment (a live run measures it; a conversion reads what the record measured). One mapping for every package (below). A conversion uses only what the record says: a core package the record does not identify (rdkit, numpy) is `unknown` with `evidence` saying smeltery imports it on every funnel run and the record does not say which build, never filled from today's environment and never an empty placeholder. An optional package a converted record does not name is left out (whether the run imported it is not known; a list is a lower bound, open finding 2 against `f809522`, resolved at `9fb7eed`). A failed/cancelled job's record (`smeltery.jobs`) writes `dependencies: null`: the backend did not observe the run. |
| `metrics` (an arm's) | `best_paired_ddE` and `mean_paired_ddE` (`core_record.arm_metrics`), emitted ONLY for a run whose submitter named an `arm` | An arm exists to be compared, and `attest runs compare FAMILY` ranks a family's arms by a metric whose name AND labels match across them -- which the per-analogue `paired_ddE.*` cannot do, since each arm screens other molecules and so carries other `labels.analogue`. These two are label-free and directional (`min`, the direction the cut itself ranks on), carry the quantity's unit, and are derived from this run's own `results.paired_ddE`, which holds analogues only (never the parent). A run with no finite analogue mean gets neither. Every run without an arm -- every run outside a job -- is unchanged. |
| `ext.<submitter>` | the submitter's own `ext` namespaces (`JobSpec.ext`, `smeltery run --ext-json`), beside `ext.smeltery`, which is never rewritten | `smeltery al` writes `ext.smeltery.al` (the loop, the iteration index, the proposer's observed identity and seeds, the batch it proposed, the predicted cost). The namespace `smeltery` itself is refused: it is the run's own. |
| `ext.smeltery` | `tiers`, `input_digest`, `inputs` (the legacy `inputs`: what `input_digest` hashes with the tiers, derived from the config once the plan is built), `host`, `started_utc`, `legacy_schema_version`, `cut_unranked`, `reuse` (with a stage store), `config_unobserved` (only when `config` could not be observed) | Everything chemistry-shaped (tier settings: method, basis, functional, docking settings) is here, and the cut's `unranked` flag (a flag is not a metric). |

### Dependency identity: one ladder, no special case

`record.package_identity(dist, module)` is the ladder `ferric_identity()` always used, generalised: a package's own
build stamp (`<module>.__build__`, ferric is the one that has one) is VERIFIED; else `direct_url.json` (git install:
VERIFIED with the commit; local directory: INFERRED from its HEAD; a wheel file: INFERRED with pip's hash); else an
index install is VERIFIED by its immutable version. New: a package installed by something other than pip or uv
(conda, a distro) is INFERRED, because a version string from another channel is not an immutable index release.
`core_record.dependency(name, identity)` maps every result the same way: `VERIFIED...` -> `verified`, `INFERRED...` ->
`inferred`, anything else -> `unknown`; `build_sha` = the commit when it is 40/64 hex; `evidence` = the provenance
sentence verbatim; `source` = where it was installed from, from the identity's own `source`, in the schema's closed
vocabulary (finding 12, widened at the freeze): `index` (a package index; which one is not recorded, so never
`index:pypi`), `<vcs>+<url>` for a VCS install, the VCS named by PEP 610's `vcs_info.vcs` (`git+`, `hg+`, `svn+`,
`bzr+`; credentials stripped), `url:<url>` for an archive (`archive_info`) installed from a non-`file:` URL, with
credentials and the query string stripped (a signed URL's query is its credential) and only a `#sha256=` fragment kept,
`file` (a local directory, wheel or archive: a `file:` path names a machine and is not written), `conda` for a conda,
mamba or micromamba install (the channel is not recorded), `system` for any other installer (a distro package manager)
and for Python itself, and `null` for a stamped build (`ferric.__build__` says how the build was identified, not where
it was installed from) and for everything known only from a version string (a tier's `xtb --version`: smeltery cannot
tell a system xtb from a conda one). A version a tier recorded at run time (`rdkit.__version__`, `xtb --version`) is `inferred`: the
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
| 13 | Not on an index | Decided at the freeze (publish to PyPI as its own release line, `schema-v<X.Y.Z>`) | Done: attestation-schema 0.1.0 from PyPI, content-identical to `9fb7eed`; the git pin, `PREINSTALL` and the `preinstall` subcommand are gone. |

## Second round (filed against `314e4ff`) and what became of it

smeltery filed four findings while adopting `314e4ff` (numbered on from the first round in the spec's "Producer
findings", smeltery second round). The schema owner accepted all four at `f809522`, together with two user-directed
changes; smeltery re-pinned there.

| # | Finding | Status at `f809522` | What smeltery writes now |
|---|---|---|---|
| 9 | Mixed builds of the experiment code itself: `danuglipron-halogen`'s rows came from two smeltery commits, and `code` could state only one | Resolved by schema change: `code` takes the dependency rule (`unknown`, `git_sha: null`, `evidence` naming each commit and its row count), and the reader refuses `unknown` with a `git_sha` or `build_sha` | `code` for a run whose `results.builds` names more than one smeltery commit: `unknown`, `git_sha`/`dirty`/`version` `null`, `evidence` `mixed: 130350724068f91bcfb8fea8b9d399c90886e67d (1 row), f3d6edf945d5c96ffe1d97a183dda914e911efbb (111 rows)` for `danuglipron-halogen`. The mixed-ferric entry was already `build_sha: null`, so the new refusal changed nothing there. |
| 10 | The one-spelling timestamp did not sort as text (`...:37.5Z` < `...:37Z`) | Resolved by schema change: always exactly six fraction digits | Unchanged code (`format_timestamp` everywhere: the record's `started_at`/`finished_at`, and `state.json`'s `updated_at` and `history[].at` in `smeltery.jobs`, including the Temporal workflow's start time); the live-run test now compares the two timestamps as text, as the spec now allows. |
| 11 | `from_dict` and the model disagreed on a non-finite value's type, and the spelling helper was private | Resolved: `attestation_schema.spell_value` is public and `RunRecord.to_dict()` returns the wire form; `from_dict` still takes only JSON | `core_record.wire_value` (smeltery's copy) is deleted; `metrics` spells values through `attestation_schema.spell_value`. |
| 12 | `source` had no vocabulary | Resolved by schema change: `index[:<name or url>]`, `git+<url>`, `file`, `conda[:<channel>]`, `system`, or `null` (reader-enforced); widened at `9fb7eed` (open finding 1 below) | The mapping above. Python is `system`; a distro install is `system`; a conda-family install is `conda`; a wheel from a URL is `url:<url>`. |

The two user-directed changes, adopted:

- **`history` is a sidecar reference** (`null` or `{path, sha256, series, points}` for `attestation/history.jsonl`).
  smeltery has no curves, so it writes `null` and no sidecar; `HistoryWriter` / `write_history` are not needed. A
  failed/cancelled job's record also writes `null`.
- **`dependencies` is nullable** (`null` = not observed, `[]` = observed, none). smeltery's own records always observe
  (a list); the job backend's failed/cancelled record writes `null`, now unconditionally: `smeltery.jobs`'s
  `_NULLABLE` branch, which wrote `[]` for the schema before `314e4ff`, is deleted.

## Findings decided at the freeze (`9fb7eed`)

smeltery filed two open findings and five job-backend findings against `f809522`. The schema owner decided all seven
before freezing `attestation.run/1` (the spec's "Producer findings", 5.A-5.D); smeltery re-pinned at `9fb7eed` and
applied each decision. None is open.

### Open findings against `f809522`: resolved

1. **`dependencies[].source` had no kind for a direct-URL archive or a non-git VCS.** *Resolved by schema change
   (5.A):* the vocabulary widens by `url:<url>` (credentials, `url:file:` and any query string refused; a `#sha256=`
   fragment kept) and `hg+`/`svn+`/`bzr+` beside `git+`. *Applied:* `record._distribution_identity` names the VCS from
   PEP 610's `vcs_info.vcs` in its provenance sentence (`VERIFIED from direct_url.json (hg install)`; a missing `vcs`
   stays git, as before), and `core_record.source` maps it to `<vcs>+<url>`; an `archive_info` install from a
   non-`file:` URL is `url:<url>` with userinfo and the query stripped and only a `#sha256=` fragment kept
   (`core_record.archive_url`); a `file:` archive stays `file`. `tests/test_core_record.py` checks each kind, and that
   the frozen reader accepts every one written.
2. **A `dependencies` list could not say it is partial.** *Resolved by spec text, no field (5.B):* a list is observed,
   not exhaustive -- every entry is a dependency the run ran on, and a name's absence says nothing (a
   `dependencies_complete` flag was rejected: `true` would be false for any real run). *Applied:* nothing to change;
   smeltery's converted records (ferric, Python, tier-recorded versions; core imports it knows of as `unknown`;
   optional imports left out) are correct as written.

### Job-backend findings: resolved

1. **`JobSpec.run_id` was not the record's `run_id`.** *Decided (5.C.1):* the submitter owns `run_id`; it names the run
   in the handle, `state.json` and `run.json`, whoever writes the record, and `settle` fails a job whose record names
   another. *Applied:* the local runner runs `smeltery run ... --run-id=<job run_id>` and the Temporal backend sets
   `RunInput.run_id`, which the finalize activity passes to `emit_core_record`; `core_fields` uses it instead of
   `input_digest[:12]` (which stays in `ext.smeltery.input_digest`). Not done: defaulting a job's run_id to
   `input_digest[:12]` at submit (the spec's suggestion) -- see [jobs.md](jobs.md#run_id), it would mean building the
   plan (structure prep, docking, the pocket, gates and the tiers' version probes) in the submitting process.
2. **`config` changed meaning with the outcome.** *Decided (5.C.2):* `config` is `JobSpec.config`, what the run was
   given, in every terminal state; derived values go in `ext.<producer>`; a producer uses the same form with and
   without a job. *Applied:* `config` is the campaign config everywhere (the Mapping table); the legacy `inputs` moved
   to `ext.smeltery.inputs`, in `smeltery run`, `--combine`, `--temporal` and `smeltery record core` alike.
3. **`JobSpec.ext` had no path into a producer-written record.** *Decided (5.C.3):* the contract carries it, and a
   backend that cannot hand `ext` (and `family`/`arm`) to its producer refuses the spec at `submit`; `JobSpec.ext`
   stays. *Applied:* unchanged -- `check_spec` keeps refusing a spec with `ext`, a `family` other than the config's
   `campaign`, or a non-`None` `arm`, because `smeltery run` has no way to carry them.
4. **Temporal did not push settle.** *Decided (5.C.4, interface text):* `state.json` may lag, `run.json` is
   authoritative, every `status`/`cancel`/`result` settles a job found to have ended, and `settle` takes the
   scheduler's `started_at`/`ended_at` when it has them. *Applied:* the Temporal backend passes `started_at` (the first
   `WORKFLOW_TASK_STARTED` event) and `ended_at` (the workflow's close time), so a backend-written record's
   `finished_at` and the terminal history entry are the workflow's end, not the time of the look.
5. **`state.json`, its lock, `settle` and `terminal_record` belonged in attestation-schema.** *Done (5.D):* they are
   `attestation_schema.state` (format unchanged, evidenced by two `state.json` files smeltery's own code wrote).
   *Applied:* `smeltery.jobs`' own implementation is deleted (state_path, record_path, state_lock and its `fcntl`
   import, read_state, _write_state, init_state, _refuse_existing's state half, _append, transition, job_status,
   started_at, settle, terminal_record, handle_in); `smeltery.jobs.settle` is a thin wrapper that supplies what the
   schema cannot know (producer `smeltery jobs`, smeltery's `code`, `ext["smeltery.job"]`). Differences callers see:
   `read_state` returns a frozen `StateFile` (attributes, `.status`, `.started_at`, `.to_dict()`; `--json` prints
   `to_dict()`); a malformed file raises `StateFormatError` (a `ValueError`); `IllegalTransition` is
   `IllegalTransitionError`; `transition` refuses a terminal state (that is `settle`'s); the job's message lives in
   `state.json` only (it was `ext["smeltery.job"].message`), and a manifest refused for a symlink is said in that
   message (it was `ext["smeltery.job"].manifest_refused`); without `flock` (Windows) every state write is refused.
   smeltery's task-23 additions survive: `settle` merges the writer's own `ext` namespaces into the terminal record, so
   `ext["smeltery.job"]` = `{backend, job_id, stages_completed}`; the reuse provenance of a complete record is the
   run's own `ext.smeltery.reuse`, which `settle` adopts untouched. No friction there.

Not schema findings, but found on the way and left as they are: the legacy `started_utc` is not a start (above); the
legacy record has no run-level failed state, so a failed plain run (outside a job) gets no core record; a build-stamped dependency's
`source` is `null` because `record.package_identity` stops at the stamp and does not also read where the package was
installed from.

## Post-freeze findings: candidates for `attestation.run/2`

`attestation.run/1` is frozen, so each of these lives in `ext` (or is left out) for v1, and none is worked around in
a core field. They are filed for the schema owner as v2 candidates.

### Stage reuse (from task 23; filed before the freeze, not decided by it)

From resumability (`smeltery.reuse`, [jobs.md](jobs.md#resumability-resubmitting-reuses-completed-stages)).

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

### Found while adopting the frozen schema (`9fb7eed`)

4. **`config` cannot say "not observed".** Every other "the producer could not say" in v1 is `null` (`dirty`,
   `direction`, `seeds`, `dependencies`, a data `sha256`, now `status`), but `config` must be an object. A conversion
   of a run that staged no config (or whose staged file is gone or edited since) therefore writes `{}`, which a reader
   comparing configs across a family cannot tell from "configured with nothing"; smeltery says why in
   `ext.smeltery.config_unobserved`. A nullable `config` (`null` = not observed) would carry it in the core.
5. **A job's run_id and the producer's natural identity.** With the submitter owning `run_id` (5.C.1), a complete
   smeltery record inside a job no longer carries its input digest as `run_id`, so two jobs of identical inputs have
   different run_ids and are not grouped as re-executions unless the submitter chose the same one; the digest is in
   `ext.smeltery.input_digest` only. The spec's remedy (default the run_id to the digest at submit) needs the plan
   built at submit, which smeltery does not do (jobs.md). A core "inputs identity" slot, distinct from `run_id`, would
   let a reader group re-executions across submitters; the `config_digest` rejected in round 1 was one form of it.
