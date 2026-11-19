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
| `run_id`, `input_digest`, `record_file`, `campaign`, `started_utc` | `run_id` is the first 12 hex of `input_digest`, the digest in the record's file name |
| `record_sha256` | the record's self-hash as written; null for records that predate it (the committed prebuilt runs do). Not re-verified here: `smeltery verify-run` does that |
| `smeltery_version`, `smeltery_commit`, `smeltery_dirty` | which smeltery ran |
| `ferric_version`, `ferric_git_sha`, `ferric_dirty`, `ferric_provenance_level`, `ferric_provenance`, `ferric_source` | which ferric ran. `ferric_git_sha` and `ferric_dirty` are the record's ferric `commit` and `dirty` (from `ferric.__build__` when the build is stamped; null commit for an index release, whose version pins it). `ferric_provenance_level` is `VERIFIED`, `INFERRED` or `UNKNOWN`, the first word of the full `ferric_provenance` label |

Single-sample standard errors are null (they are NaN in memory).

## File metadata

Key-value metadata on the Parquet file and the Arrow schema: `smeltery.schema_version` (`1`; bumped when a column is
added, renamed, removed or retyped), `smeltery.units` (JSON `{quantity: unit}`, a list of units where runs disagree;
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
