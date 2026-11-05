# GUI contract: what a run writes, and the API the viewers read it through

This is the interface between the run (`smeltery run`), the data layer (`smeltery.gui.results` and
`smeltery.gui.scenes`), and the viewers (the Marimo app, the HTML report). Each of these is built against this page;
changing a field here means changing every side.

## 1. Run outputs (`smeltery run --out OUT`)

Unchanged: `OUT/run-<digest12>.json` (the `RunRecord`); `OUT/structure/` when `[structure]` is used.

Added under `record.results`. None of these feed `input_digest`, which hashes inputs and tiers only.

| Key | Shape | Meaning |
|---|---|---|
| `per_pose` | `{candidate: {quantity: [float, ...]}}` | Raw per-pose values for every candidate, parent included, for each `per_pose` quantity a stage wrote (`dE_int`, and `E_mmff`, `E_mmff_relaxed` and `E_gfn2` when those gates ran). In each quantity's declared unit. |
| `units` | `{quantity: unit}` | The unit of each quantity in `per_pose`. |
| `candidate_order` | `[name, ...]` | The candidates in config order, parent included, after any `[decisions]` rejections are dropped. The record is written with sorted keys, so this is where the order lives. |
| `poses` | `{candidate: {"file": "poses/<file>.sdf", "sha256": hex, "n_poses": int}}` | One SDF per candidate. The path is relative to `OUT`. |
| `field` | `[[q, x, y, z], ...]` | The pocket point charges, with positions in Angstrom, in the poses' frame. |
| `receptor` | `{"file": str, "sha256": hex}` or `null` | A receptor `.pdb` in the poses' frame, when one exists: `structure/receptor.pdb`, or the `[pocket] file` when it is a `.pdb`. A path outside `OUT` is stored as the absolute path. |
| `decisions_in` | `{"file": str, "sha256": hex, "excluded": [name, ...]}` or absent | Only when `[decisions]` was given (section 3). |
| `pose_maps` | `{analogue: [[analogue_atom, parent_atom], ...]}` | Atoms of each analogue copied exactly from the parent's pose (0-based, pose-SDF atom order). Absent in records written before pose maps. |
| `calcs` | `{"file": str, "n": int, "primary": str, "by_model": {model: int}}` or `null` | The per-SCF log beside the record (`calcs.jsonl`, or `calcs-<candidate>.jsonl` for a `--only-candidate` partial), its line count, the primary field model's name, and the lines per field model. `null` for a combine of partials that kept no log: no SCF count is known. Rows below. |
| `pose_index` | `{candidate: [original_pose, ...]}` or absent | Only after a pairwise pose drop (`--temporal`). Every candidate, parent included, lists the ORIGINAL index of each pose it kept, in order: `per_pose[c][q][k]` and SDF record `k` are original pose `pose_index[c][k]`. Absent means identity (record `k` is pose `k`). |
| `dropped_poses` | `[{"candidate", "pose", "leg", "field_model", "reason", "outcome", "rungs_tried", "dropped_with"?}, ...]` or absent | Only when a `--temporal` leg failed every rung. `pose` is the original index; `leg` is `"vacuum"` or `"field"`; `field_model` is `null` for a vacuum leg (shared by every model); `reason` is the last error text, `outcome` e.g. `"not_converged"`, `rungs_tried` the remedy rungs run (`[0, 1]`). `dropped_with` (the candidates whose ddE lost the pose: every candidate for a parent pose, else `[parent, candidate]`) is present for a primary-model or vacuum drop only; a drop under an extra charge model is that model's alone (`charge_sensitivity`). |
| `charge_sensitivity` | `{model: {...}}` or absent | Only with `[charge_sensitivity]`: per extra charge model, `primary_model`, `paired_ddE` (`{analogue: {"primary", "model", "delta", "n"}}`), `sign_flips`, `spearman`, `floor`, `n`, `n_dropped`, `dropped_poses` (`{analogue: [original_pose, ...]}`), `parent_dropped_poses` (`[original_pose, ...]`), `excluded`, `failures` (`{candidate: {pose: error}}`), `field` (the model's pocket provenance), `note`, and `calcs` (`{"n": int}`: its SCF lines are in `calcs.jsonl` under `field_model = <model>`). A model that could not be measured is `{"error": str, "stage": "field" \| "measure" \| "summary"}`. |
| `config` | `{"file": "config.toml", "sha256": hex, "inputs": {...}}` or absent | The config as staged into `OUT` with an explicit `--out`, its digest, and the input files copied under `OUT/inputs/`. |
| `imported` | `{"importer": str, "original": "original/<file>", "sha256": hex, "input_digest": str, "derived": {...}}` or absent | Only in a record brought in by `smeltery.bench.importers` (section 1.1). |
| `not_recorded` | `[key, ...]` or absent | Only in an imported record: the contract keys the original lacks, `results` keys bare (`"per_pose"`) and `inputs` keys prefixed (`"inputs.cut"`). |

At the record's top level, `smeltery` is `{"version", "source", "commit", "dirty", "provenance"}` (which smeltery
source ran; an editable install reports the checkout's HEAD and `dirty`; `provenance` says whether that was VERIFIED
or INFERRED), or `null` in a record written before it was kept. `ferric` has the same shape for the engine.

### The SCF log (`calcs`)

One JSON object per line. An SCF line: `candidate`, `pose` (the ORIGINAL pose index: the log is written before any
drop), `leg` (`"vacuum"` or `"field"`), `converged`, `iterations`, `energy_hartree`, `seconds`, `field_model` (`null`
on a `--temporal` vacuum leg, which every field model shares), `rung` (0 without a remedy ladder). A `--temporal` line
also has `digest` (the first 12 hex of `input_digest`), `started_utc` and `build`. A `--temporal` outcome line (a leg
that gave no value) has `candidate`, `pose`, `leg`, `field_model`, `outcome` (`"not_converged"`, `"timeout"`,
`"clash_refused"`, `"error"`), `rungs_tried`, `error` and `digest`, and no `converged`. Bench's
`GET /api/runs/{idx}/calcs?candidate=&pose=` takes `pose` as the record position and maps it through `pose_index`.

### 1.1 Imported records

An importer keeps the original's `inputs` and `tiers` unchanged (nothing renamed, dropped or added), so
`input_digest` is the original's and still the hash of them; `results.imported.input_digest` says so. A value the
importer derives for a key the original lacks is never written in place: it is
`results.imported.derived["inputs.<key>" | "results.<key>"] = {"value": ..., "from": "<where it came from>"}`, and
`smeltery.gui.results.contract_record(record)` lays those values over `inputs` and `results` for the viewers
(`load_run` and Bench's verdict read through it). The original's file is copied byte for byte to `original/`.

### Pose SDFs

- One SDF file per candidate. Record `k` is pose `k`, unless the run dropped poses: then record `k` is original pose
  `results.pose_index[candidate][k]`, which `smeltery_pose_index` also states. The poses are paired by ORIGINAL index:
  analogue pose `i` is built on parent pose `i`, and after a drop the two records holding pose `i` may sit at different
  positions in the two files.
- Atom order is the candidate's SMILES with explicit hydrogens (`Chem.AddHs(Chem.MolFromSmiles(smiles))`), the same
  order as `Pose.symbols`. Bonds come from the SMILES, never perceived from coordinates.
- Molecule name: `<candidate> pose <i>`. Properties on each record:
  - `smeltery_candidate` and `smeltery_pose_index`;
  - `smeltery_parent` (`"1"` on the parent, else `"0"`);
  - one `<quantity>` property per `per_pose` quantity of that candidate, formatted `%.10g`;
  - `paired_ddE` on analogues only, also `%.10g`.
- File name: the candidate name with every character outside `[A-Za-z0-9._-]` replaced by `_`. If two candidates end
  up with the same file name, the run fails.

## 2. Data layer: `smeltery.gui.results` and `smeltery.gui.scenes`

No GUI library is imported. RDKit and numpy are allowed. All tables are `list[dict]`.

```python
# smeltery.gui.results
@dataclass
class Run:
    record: dict  # the parsed run-*.json
    out_dir: Path  # where `poses/...` paths resolve
    @property campaign -> str
    @property parent -> str
    @property candidates -> dict[str, str]  # name -> SMILES, config order
    @property analogues -> list[str]  # candidates minus the parent, config order
    @property floor -> float
    @property floor_source -> str
    @property z -> float
    @property keep -> int
    @property groups -> list[list[str]]  # tie groups from results.cut, best first
    @property survivors -> list[str] | None
    @property unranked -> bool

def load_run(path: str | Path) -> Run: ...
# path is a run-*.json, or an OUT directory holding exactly one (else ValueError naming the ones found)

def ddE_table(run: Run) -> list[dict]: ...
# One row per analogue, in cut order (groups flattened):
#   candidate, smiles, mean, sem, n, lo, hi (mean -/+ z*sem), unpaired_sem, group (0-based index into run.groups),
#   survivor (True / False / None when the cut was unranked)

def resolved_matrix(run: Run) -> tuple[list[str], list[list[bool]]]: ...
# Names in ddE_table order. [i][j] is smeltery.funnel.resolved(m_i, m_j, run.z, run.floor), and False on the
# diagonal. Uses the funnel's own predicate: the GUI never decides more than the cut did.

def pose_indices(run: Run, name: str, n: int) -> list[int]: ...
# The original pose index of each of `name`'s n recorded poses: results.pose_index[name], else 0..n-1.

def per_pose_table(run: Run, quantity: str = "dE_int") -> list[dict]: ...
# One row per (analogue, kept pose), cut order: candidate, pose (ORIGINAL index, the pairing key), position (its place
# in the analogue's per_pose list and SDF), parent_position (the paired parent pose's place in the parent's), value,
# parent_value, paired (value - parent_value). Paired through pose_index, so an analogue-only drop is no error.
# Raises KeyError naming the quantities present if `quantity` was not recorded.

def dropped_poses(run: Run) -> list[dict]: ...  # results.dropped_poses, unchanged ([] when none)

def funnel_rows(run: Run) -> list[dict]: ...  # results.funnel, unchanged

def pose_sdf(run: Run, candidate: str) -> str: ...  # the SDF text; checks its sha256 against the record

# smeltery.gui.scenes
def pose_scene(run: Run, candidate: str, pose: int, *, with_parent: bool = True) -> dict: ...
# A MolViewSpec state as a JSON-ready dict (molviewspec builder `.get_state()`, then `json.loads`): the receptor
# as cartoon plus ball-and-stick within 5 A of the ligand, when there is one; the candidate's SDF record `pose`; the
# parent's pose paired with it (same original index, via pose_index) in a muted colour when with_parent; and the field charges as spheres, coloured by sign.
# molviewspec is imported inside the function, so `smeltery.gui` still imports without it.
```

## 3. Decisions

`smeltery.decisions` is a core module with no GUI library, so the CLI can read decisions files.

```json
{
  "schema": 1,
  "campaign": "mwe-benzoic-cli",
  "run": "<input_digest of the run these decisions were made on>",
  "decisions": [
    {"candidate": "4-F", "smiles": "OC(=O)c1ccc(F)cc1", "status": "keep", "reason": "cut_survivor",
     "note": "", "user": "mg", "time": "2026-10-28T20:00:00Z"}
  ]
}
```

- `status` is one of `keep`, `reject`, `hold`.
- `reason` is one of `cut_survivor`, `within_tie_choice`, `liability`, `synthesis`, `pose_implausible`, `novelty`,
  `other`.
- `note` is required when `reason` is `within_tie_choice` or `other`.
- Each candidate appears at most once.
- API: `Decision`, `DecisionSet` (`load`, `dump`, `validate`, `set(candidate, status, reason, note, user)`), and
  `STATUSES` and `REASONS`.
- A config may name a decisions file:

  ```toml
  [decisions]
  file = "decisions.json"
  ```

  Rejected candidates are dropped before the run and listed in `results.decisions_in.excluded`. Rejecting the parent
  is a config error.

## 4. Viewers

- `smeltery.gui.app`: a Marimo app with four views (Pipeline, Results, Grid with decisions, Pose).
  `main(argv) -> int` serves it for an OUT directory. `--desktop` opens it in a pywebview window when pywebview is
  installed, and says how to install it when it is not.
- `smeltery.gui.report`: `write_report(run, path)` writes one self-contained HTML file (RDKit.js and Mol\* from a
  CDN, with the data inlined). `main(argv) -> int`.
- The CLI subcommands `smeltery gui` and `smeltery report` call those `main`s.
- The `gui` extra in `pyproject.toml` is `marimo`, `anywidget`, `mols2grid`, `molviewspec` and `plotly`.
  `pywebview` is a separate `desktop` extra.
