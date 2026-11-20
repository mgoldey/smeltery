# Dataset export: runs as Parquet or Arrow

`smeltery export` writes finished runs as one columnar file, one row per (ligand, target, tier), for pandas, polars,
DuckDB or anything else that reads Parquet or Arrow. It reads the run record (`run-<digest12>.json`, see
[gui-contract.md](gui-contract.md) section 1) and recomputes nothing the record states.

```bash
pip install 'smeltery[dataset]'          # pyarrow; the row builder itself needs only the core install
smeltery export parquet out/run-a out/run-b/run-0123456789ab.json -o runs.parquet
smeltery export arrow out/run-a -o runs.arrow      # Arrow IPC file format, same table and metadata
```

Each `RUN` is an OUT directory (every `run-*.json` in it) or one record. Several runs concatenate in the order given;
the same run twice (same input digest) is refused. In Python: `smeltery.dataset_export.export_rows(paths)` returns the
rows as dicts without pyarrow, and `write_dataset(paths, dest, "parquet" | "arrow")` writes them.

## Rows

A row exists for each candidate (the parent too) and each tier that declares a per-pose quantity the candidate
carries. A tier that writes only poses (`paired_poses`) has no score and no row. Units and floors come from the
tiers' own declarations, `Tier.produces()` and `Tier.systematic_floor()`, found by the recorded tier name and rebuilt
from the recorded settings; no tier name is special-cased. A tier that cannot be rebuilt without a provider object
(docking) is read through its class's `QUANTITY` and the record's `results.units`, and its floor is reported as
unresolved, not as zero.

| Columns | Meaning |
|---|---|
| `ligand`, `is_parent` | the candidate's name in the run |
| `smiles`, `smiles_form`, `smiles_recorded` | RDKit-canonical SMILES (`smiles_form = rdkit-canonical`), or the recorded text when RDKit is missing, cannot parse it, or `--no-canonical-smiles` was given (`as-recorded`); the recorded text is always kept |
| `target`, `target_kind`, `target_sha256` | what the run was measured against: the `[structure]` spec, else the pocket file, else the docking receptor (`kind` `structure`, `pocket`, `receptor`); a run with none was measured in a bare point-charge field and is identified as `field:<12 hex of the field's digest>` (`kind = field`) |
| `tier`, `tier_settings` | the tier's recorded name, and its recorded settings as JSON |
| `score_quantity`, `score`, `score_unit`, `score_sem`, `score_n` | the tier's headline quantity (the funnel's cut quantity, `dE_int`, when the tier declares it, else its first declared one): the mean over the candidate's poses, its standard error and the pose count |
| `score_floor`, `score_floor_source` | the systematic floor THE TIER declares for that quantity. Null while unmeasured, and the source says whether that is "tier declares no measured floor" or "could not be rebuilt from the record" |
| `paired_ddE`, `paired_ddE_sem`, `paired_ddE_n`, `paired_ddE_floor`, `paired_ddE_floor_source` | on the tier that measures the cut quantity, for each analogue: the paired ddE against the parent, from `results.paired_ddE`, and the floor THE CUT used (`inputs.floor`). With an unmeasured tier floor that is the config's explicit value, and the source says so. Unit is `score_unit` |
| `scores` | list of structs `{quantity, unit, value, sem, n, systematic_floor, floor_source, kind}` for every quantity of the tier (`kind = per_pose_mean`), plus the paired ddE (`kind = paired_vs_parent`) |
| `decision`, `decision_reason` | the candidate's funnel outcome, repeated on each of its rows: `pass` (a cut survivor), `drop` (ranked outside `keep`, or removed before the cut, which says PoseBusters when that gate failed), `reference` (the parent is never cut), `undecided` (the cut was unranked at the boundary) |
| `user_decision`, `user_decision_reason` | from `decisions-<digest12>.json` beside the record (status and reason), else null |
| `run_id`, `family`, `core_record_sha256` | the run's identity as its CORE record (`OUT/attestation/run.json`, [core-record.md](core-record.md)) states it, and the sha256 of that file's bytes -- the digest a dataset record's `source_runs[]` and the lineage resolver use. A job run's `run_id` is the submitter's (`--run-id`), not `input_digest[:12]`. Without a core record (one belongs to a legacy record when its `data[]` lists that file as `output`) `core_record_sha256` is null and `run_id`/`family` fall back to what a core record says for an unnamed run: `input_digest[:12]` and the campaign. A core record listing the legacy record with another sha256 is stale and refused. Changed in `schema_version` 2: `run_id` was always `input_digest[:12]`; that value is not a column of its own any more (it is the first 12 hex of `input_digest`, and the digest in `record_file`) |
| `input_digest`, `record_file`, `campaign`, `started_utc` | from the legacy record (`run-<digest12>.json`) |
| `record_sha256` | the LEGACY record's self-hash as written (sha256 of its JSON minus this key), not the core record's digest: no core reader resolves it. Null for records that predate it (the committed prebuilt runs do). Not re-verified here: `smeltery verify-run` does that |
| `smeltery_version`, `smeltery_commit`, `smeltery_dirty` | which smeltery ran |
| `ferric_version`, `ferric_git_sha`, `ferric_dirty`, `ferric_provenance_level`, `ferric_provenance`, `ferric_source`, `ferric_build_basis` | which ferric ran, per ROW (see "Ferric per row" below). `ferric_git_sha` and `ferric_dirty` are the record's ferric `commit` and `dirty` (from `ferric.__build__` when the build is stamped; null commit for an index release, whose version pins it). `ferric_provenance_level` is `VERIFIED`, `INFERRED` or `UNKNOWN`, the first word of the full `ferric_provenance` label |

Single-sample standard errors are null (they are NaN in memory).

## File metadata

Key-value metadata on the Parquet file and the Arrow schema: `smeltery.schema_version` (`3`; bumped when a column is
added, renamed, removed or retyped -- 2 added `family` and `core_record_sha256` and took `run_id` from the core record), `smeltery.units` (JSON `{quantity: unit}`, a list of units where runs disagree;
includes `ddE`), `smeltery.smiles_form`, `smeltery.runs` (the run ids, in order), `smeltery.grain` and
`smeltery.generator`. The schema is built once in `arrow_schema()` and is identical for every export, whatever the
runs contain, so files of different runs concatenate (`tests/test_dataset_export.py` pins it across all five prebuilt
runs).

## Limits

- The score is a mean over poses of values the record already holds, not a new measurement; use `scores` and the
  record for per-pose values.
- A candidate with no `results.per_pose` entry has no rows. A record that predates pose output cannot be exported.
- A quantity two tiers both declare belongs to the later tier, as in the run itself.
- A recorded quantity no recorded tier declares is exported under the tier name `unattributed` rather than dropped.

## Splits and a PyTorch dataset

A model needs the same train/valid/test rows every time. A split is a small JSON file beside the table; the table is
never rewritten.

```bash
smeltery export split scaffold runs.parquet -o split-scaffold.json --seed 0     # RDKit Murcko scaffolds
smeltery export split target runs.parquet -o split-target.json --seed 0 --fractions train=0.6,valid=0.2,test=0.2
smeltery export split verify split-scaffold.json runs.parquet                    # exits 2 if the table changed
```

The file records `kind`, `algorithm_version` (the assignment rule; bumped when it changes), `schema_version`, `seed`,
`parameters` (fractions, `generic`, `smiles_column`), `table.sha256` and the identity of every row in each partition.
A row's identity is `[run_id, ligand, target, tier]`, the export's grain.

- **Groups do not straddle partitions.** Every row of one scaffold (or one target) is in one partition. Groups are
  shuffled by the seed and filled greedily to the row fractions (row counts, not group counts), so small tables give
  uneven partitions; a partition that would be empty is refused. Acyclic molecules have the empty scaffold and are one
  group. The prebuilt runs together have three scaffolds and three targets, hence the 50/25/25 fractions in the tests.
- **The digest is the SHA-256 of the table file's bytes**, not of the row identities: identities alone would pair a
  re-export with different scores to a split made on the old numbers. The cost is that the same content written by
  another pyarrow version may differ in bytes; the split then refuses and is re-made. Loading against a table with a
  different digest raises `SplitError`, as does a split whose partitions are not exactly the table's rows. The digest
  catches a changed table, not an edited split that still covers the same rows; keep splits in version control.
- **A scaffold split refuses without RDKit** (and on a SMILES RDKit cannot parse) instead of grouping some other way.
- **Deterministic.** Same table and seed: a byte-identical file (sorted keys, no timestamps or paths). RDKit's scaffold
  for a molecule could in principle change across RDKit releases; the recorded partitions, not a recomputation, are the
  artifact.

```python
from smeltery.dataset_splits import make_split, write_split
from smeltery.dataset_torch import ExportDataset

write_split(make_split("runs.parquet", "scaffold", seed=0), "split-scaffold.json")
train = ExportDataset(
    "runs.parquet",
    features=["score_n", "score_floor"],
    target="score",
    split="split-scaffold.json",
    partition="train",
    tiers=["docking"],
    decisions=["pass", "drop"],
)
x, y = train[0]  # float32 tensors; train.identity(0) names the row
```

`ExportDataset` reads only the table. Features and target are numeric columns you name; a null feature is NaN, a row
with a null target is dropped (`n_null_target`). `tiers=` and `decisions=` filter rows, `transform(sample)` post-processes
`(x, y)`. It is a plain map-style class, not a `torch.utils.data.Dataset` subclass, so importing it never imports
torch; `DataLoader` accepts it. torch is the separate `torch` extra (`pip install 'smeltery[torch]'`), imported when the
dataset is built; `tensors=False` yields plain floats without it. Splits need only the `dataset` extra.

## Dataset directories (`attestation.dataset/1`)

`export parquet` writes one file, and nothing outside it says which runs it came from. `export dataset` writes a
directory with a record that does, in attestation-schema's `attestation.dataset/1` (a DRAFT record kind; attestation
`docs/superpowers/specs/2026-11-19-dataset-records-design.md`), so a training run that names the dataset by digest
resolves, by digest, to the core records of the smeltery runs and from them to the ferric build
(`attestation_schema.resolve_lineage`).

```bash
pip install 'smeltery[dataset,attestation]'   # pyarrow, and attestation-schema >= 0.2.0 (the first with dataset/1)
smeltery record core out/run-a                  # each source run's core record, if the run did not write one
smeltery export dataset out/run-a out/run-b -o datasets/ddE/1 --dataset-id ddE --version 1 \
    --split scaffold:0 --fractions train=0.8,valid=0.1,test=0.1 [--license CC-BY-4.0] [--format arrow]
smeltery export split target datasets/ddE/1 -o datasets/ddE/2      # a NEW version with the split added
smeltery export split verify scaffold-seed0 datasets/ddE/1          # a split name (or file) checked inside a dataset
```

```
DIR/runs.parquet                    the table, exactly as `export parquet` writes it
DIR/splits/<kind>-seed<N>.json      each --split, made on that table (`table.sha256` names it)
DIR/attestation/digests.json        sha256 of every file above; the sha256 of THIS file is the content digest
DIR/attestation/dataset.json        the record, written last
```

Written in that order into a staging directory that is renamed onto `DIR` at the end. The record:

| Field | From |
|---|---|
| `dataset_id`, `version`, `name` | `--dataset-id` (default: the runs' campaign; runs spanning campaigns must name it), `--version`, `--name` |
| `created_at`, `producer`, `code` | now; `smeltery export` and smeltery's version; smeltery's own identity, on the core record's ladder |
| `files[]`, `content_sha256` | every file of the directory, by `attestation_schema.dataset_files` (Parquet `application/vnd.apache.parquet`, Arrow `application/vnd.apache.arrow.file`, splits `application/json`) |
| `rows`, `columns[]` | the row count; `arrow_schema()`'s names and type spellings, with a `unit` on `score*` and `paired_ddE*` (value, sem, floor) only when one unit holds for every row |
| `splits[]` | each split file: name `<kind>-seed<N>`, path, sha256, kind, seed |
| `source_runs[]` | each run's core record: `run_id`, `family`, sha256 of `run.json` (read strictly, and refused unless canonical, so the digest is its `record_digest`). A run with no core record is listed with `record_sha256: null`, which the resolver cannot link by digest |
| `license` | `--license`, an SPDX expression; **null by default**, meaning not stated. Never smeltery's `MIT OR Apache-2.0`: that licenses the code, not a receptor's or ligand set's terms |
| `ext.smeltery` | `schema_version` (the column layout), `grain`, `units`, `smiles_form`, `generator`, `table` (path, format), `writer` (the pyarrow that wrote the table: its bytes, hence the content digest, depend on it), `splits` (each split's `algorithm_version`, `schema_version`, `parameters`, kind, seed) and, for a later version, `derived_from` (the previous record's digest, content digest, version, and the files added) |

- **ferric cross-check.** A row of a single-build run must equal the `build_sha` of the `ferric` dependency in its
  run's core record (null included). A row of a mixed-build run is accepted when its sha is one of the builds the run
  used (the core `build_sha`, or a sha `results.builds` lists) and refused, naming the row and run, when it is not
  (a tampered or mis-joined row). A row with a null sha passes and is counted in `ext.smeltery.ferric_rows`
  (`rows`, `by_build`, `unknown`, `mixed`); the CLI prints a stderr line when any row's build is not stated.

### Ferric per row (`schema_version` 3)

`schema_version` 2 copied the legacy record's one `ferric` block onto every row. For a `--temporal` run finalized with
`allow_mixed_builds` that block names the process that finalized the run, not the ones that measured: the committed
`danuglipron-halogen` run wrote 111 SCF rows under ferric `b22183b`, 1 under `81f411c`, and 46 with no stamp, its core
record says ferric `unknown`, and the column said `81f411c` for all 13 candidates -- so the export refused it.

Now a row states the build of ITS OWN SCFs, in `ferric_build_basis`:

| basis | when | `ferric_git_sha` / `dirty` / level |
|---|---|---|
| `run_record` | `results.builds` names at most one ferric build (every run but a mixed one) | the record's `ferric` block, as before |
| `scf_rows` | mixed run; every SCF row of the candidate in `calcs.jsonl` carries the same `build` stamp | that stamp's `git_sha`, `dirty`; `VERIFIED` |
| `mixed_scf_rows` | mixed run; the candidate's own SCFs came from several builds | null, null, `UNKNOWN`; `ferric_provenance` lists the shas |
| `unrecorded` | mixed run; any SCF row of the candidate has no stamp, or there is no `calcs.jsonl` beside the record | null, null, `UNKNOWN`; `ferric_provenance` counts the unstamped rows and names the builds the others carry |

`results.builds` carries only counts per build, not which rows, so the per-row identity is read from `calcs.jsonl`
(the log finalize wrote the record from; each row's `candidate` and `build`). A row is a mean over its candidate's
poses and legs, so it has a build only when every one of those SCFs does. The mixed-run `ferric_version` is null (a
stamp has none) and the run-level stamp is never used as a fallback. Today's prebuilt `danuglipron-halogen` therefore
exports all 13 rows as `unrecorded` (every candidate has unstamped SCFs), reported, not refused.

Why 3 and not a note on 2: `ferric_git_sha` may now be null where it never was, and a reader joining on it must not
read a null as "an index release" (null also means that for a `run_record` row); `ferric_build_basis` is the column a
reader checks. A column was added as well, which the rule already counts as a bump. Single-build runs have every
old value unchanged.
- **Immutable.** An existing non-empty `DIR` is refused; `write_split` refuses a file inside a dataset directory.
  `smeltery export split KIND DIR -o NEWDIR [--version V]` hard-links (or copies) every file of `DIR` into `NEWDIR`,
  adds the split, and writes a new manifest and record (`version` defaults to the old one plus one when it is a
  number or `vN`). `DIR` is untouched, so a run trained on its content digest still resolves to its record.
- **Reading one** (`dataset_record.open_dataset`, which `ExportDataset` and `export split` use) first checks the whole
  directory with `attestation_schema.conformance.check_dataset_dir` -- every file's sha256 and size, the content
  digest -- and refuses a mismatch. `ExportDataset("datasets/ddE/1", split="scaffold-seed0", partition="train",
  dataset_sha256=...)` takes a split NAME, refuses a directory whose content digest is not the one given, and
  `lineage_entries()` returns the `data[]` entries the training run's core record lists: the dataset by content digest
  (`role: train`) and the split file by its digest (`role: split`).
- **Needs attestation-schema >= 0.2.0** (`smeltery.dataset_record.REQUIRED_SCHEMA`), the first release with
  `attestation_schema.dataset`. With 0.1.0 (the `attestation` extra's floor today) every entry point refuses with a
  message naming that version, and `tests/test_dataset_record.py` skips. Raising the extra's floor to 0.2.0 makes
  `test_the_dataset_tests_cannot_skip_once_the_extra_requires_the_release` require the module, so CI's test job
  (which installs `attestation` and `dataset`) runs them from that commit on.

### Producer findings against the draft schema

Found by writing these records; filed against attestation's draft `attestation.dataset/1`, not worked around in the
record.

1. **A split file carried into a new version is `ambiguous` in lineage.** Versioning by copying the files (the spec's
   rule) gives the old split file the same sha256 in both versions. The resolver matches each `data[]` entry on its
   own, so a run listing (version 1 by content digest, its split file by digest) gets the split entry matched to BOTH
   versions and an `ambiguous` issue, although its content entry already pins version 1. Lineage is then `ok` but
   not `verified`. Candidate fixes: prefer a split/file match inside a dataset the same run matched by content, or let
   the split entry name its dataset. (`test_a_later_split_is_a_new_version_and_the_old_one_still_resolves`.)
2. **No link from a version to the version it was made from.** Nothing in the record says version 2 is version 1
   plus a split; `resolve_lineage` relates them only through shared `source_runs`. smeltery states it in
   `ext.smeltery.derived_from`, which no reader understands.
3. **A split's partition is not expressible.** A run trains on ONE partition of a split (`train`), but a `data[]`
   entry can name only the split file (`role: split`); `splits[]` has no partition names and the training role cannot
   say which one. Partition names and fractions are in `ext.smeltery.splits[*].parameters` and in the split file.
4. **Split parameters have no slot.** `splits[]` holds kind and seed only; the assignment rule's version
   (`algorithm_version`) and parameters (fractions, generic scaffolds) go to `ext`. A consumer reproducing a split
   needs both.
5. **`columns[].unit` is per column, not per row.** smeltery's `score` column carries different quantities per row
   (`score_quantity`, `score_unit`); the unit is stated only when one holds for every row, else null. The nested
   `scores` list carries a unit per element, which `columns[]` cannot describe at all.
6. **The exporter's environment has no slot.** The table's bytes (so the content digest) depend on the pyarrow that
   wrote it; the record deliberately has no `dependencies`, so smeltery puts the version in `ext.smeltery.writer`.
7. **`created_at` makes two exports of the same bytes two records.** Same content digest, different
   `dataset_digest`; given both, a resolver reports `ambiguous`. Deliberate in the spec (metadata is not content), but
   it means re-running an export is never a no-op for lineage.
8. **No version to detect the feature by.** attestation main's `packages/attestation-schema/pyproject.toml` still says
   `version = "0.1.0"` with `attestation_schema.dataset` present, and the package has no `__version__`; smeltery
   detects the module by import, and the release that ships it must carry a new version (0.2.0) for a floor to mean
   anything.

smeltery-side findings: the table's metadata includes smeltery's version (`smeltery.generator`), so the same rows exported by another smeltery
build have another content digest.
