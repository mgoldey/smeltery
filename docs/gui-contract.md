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

### Pose SDFs

- One SDF file per candidate. Record `i` is pose `i`. The poses are paired by index: analogue pose `i` is built on
  parent pose `i`.
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

def per_pose_table(run: Run, quantity: str = "dE_int") -> list[dict]: ...
# One row per (analogue, pose): candidate, pose, value, parent_value, paired (value - parent_value).
# Raises KeyError naming the quantities present if `quantity` was not recorded.

def funnel_rows(run: Run) -> list[dict]: ...  # results.funnel, unchanged

def pose_sdf(run: Run, candidate: str) -> str: ...  # the SDF text; checks its sha256 against the record

# smeltery.gui.scenes
def pose_scene(run: Run, candidate: str, pose: int, *, with_parent: bool = True) -> dict: ...
# A MolViewSpec state as a JSON-ready dict (molviewspec builder `.get_state()`, then `json.loads`): the receptor
# as cartoon plus ball-and-stick within 5 A of the ligand, when there is one; the candidate's pose `pose`; the
# parent's pose `pose` in a muted colour when with_parent; and the field charges as spheres, coloured by sign.
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
