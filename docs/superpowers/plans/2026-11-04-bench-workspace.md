# Bench Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the pipeline the page: drawn, edited step by step in place (inputs, method, interactive view,
impact), configurable through a Methods tab, startable from prebuilt pipelines with finished runs, and every
number traceable to the calculations that made it.

**Architecture:** Phase 1 changes `smeltery run` so each run directory carries its config, its input files,
the source commit, timings, per-SCF logs and per-pose gate results. Phases 2-6 build on that in Bench: a
lineage inspector, a steps model in Python (`smeltery.gui.steps`) served by new `/api/pipelines/*` routes and
drawn by a new Workspace page, per-step views, a Methods tab with workspace defaults, and packaged prebuilt
pipelines. The config TOML stays the only pipeline state; every edit is a format-preserving text edit.

**Tech Stack:** Python 3.12, FastAPI, pydantic, tomlkit (new, `bench` extra); React 19 + TypeScript 5.9,
TanStack Query, openapi-fetch, Mol*/MolViewSpec, RDKit.js, Plotly (figures built in Python); pytest,
Playwright (offline), axe.

**Spec:** `docs/superpowers/specs/2026-11-04-bench-workspace-design.md` (read it first, especially
"Settled after review").

## Global Constraints

- The config TOML is the pipeline. Every form writes TOML through `Pipeline` + `smeltery.gui.config_text`;
  no pipeline state lives only in the browser or a database.
- A run must re-run from its own directory: `config.toml` and every input file it reads are inside it.
- Workspace method defaults are copied into a step's config when the method is added, never referenced at run time.
- Estimates, explanations and "fits" come from code (`estimate_cost`, `estimate_memory`, `memory_estimate`,
  `Pipeline.check`, catalog `help`), never from UI text. A model that cannot say shows "cannot say".
- Previews never run docking or SCFs on an edit (spec, settled item 4).
- A draft may be saved with errors; only Run is blocked while `Pipeline.check()` has an error.
- Colours only from `ui/src/styles/tokens.css`; every new page is added to `ui/e2e/routes.ts` (axe, dark mode).
- No new scientific figures except the per-pose ΔE_int plot of step 5, built in Python like the others.
- Bench layering: core (`smeltery.*` outside `bench`) never imports `smeltery.bench`; stores are ports + adapters.
- Gates per AGENTS.md before each commit: ruff check, ruff format --check, `uvx ty@0.0.96 check`, the bench
  gate, the core-file table; UI: `npm run typecheck && npm run test && npm run build`, the feature's spec;
  `npm run gen:api` after any API change. Every task's tests are mutation-checked (break the code, see them fail).
- Do not commit `scripts/temporal_ddE.py`, `benchmarks/`, `.superpowers/`, `ui/node_modules/`, `src/smeltery/bench/web/`.

## Review Focus

1. **A run made before Phase 1** (no `config`, no `smeltery` identity, no `calcs`) opened in the lineage
   inspector or the workspace: every missing item reads "not recorded for this run", nothing errors. → Task 2.1.
2. **A config whose relative paths point outside its folder** (`../tests/data/pocket_pep5.pdb`), copied as a
   draft or prebuilt or run from another cwd: inputs resolve against the config's own directory. → Task 1.3.
3. **The TOML pane holds text that does not parse** while the user also edits a step: the step edit is refused
   with the parse error and line, the pane text is not overwritten. → Task 3.4 (parse error from Task 1.2).
4. **A ligand with an element ferric's STO-3G cannot build (Br)**: the ligands view and impact strip say so
   before any run, instead of the run failing in step 5. → Task 4.2.
5. **Two tabs saving the same draft**: the second Keep, based on a stale file, gets 409 naming the conflict and
   loses nothing. → Task 3.1.

---

## Phase 1: Provenance in every run directory

### Task 1.1: Record the smeltery source commit

**Files:** Modify `src/smeltery/record.py`; Test `tests/test_record_identity.py` (create)

**Interfaces:** Produces `smeltery_identity() -> dict` with keys `version`, `source`, `commit`, `dirty`,
`provenance` (same shape and provenance strings as `ferric_identity()`), and `RunRecord.smeltery: dict`
(default `smeltery_identity()`). `smeltery_version` stays for old readers.

- [ ] **Step 1: Write failing tests**
```python
def test_an_editable_install_records_the_checked_out_commit_not_the_build_stamp():
    ident = smeltery_identity()
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()
    assert ident["commit"] == head and ident["provenance"].startswith("INFERRED")


def test_the_record_carries_it_and_it_is_not_in_the_input_digest():
    a = RunRecord("c", {"x": 1}, [], {})
    b = RunRecord("c", {"x": 1}, [], {}, smeltery={"commit": "other"})
    assert "smeltery" in json.loads(a.to_json()) and a.input_digest == b.input_digest
```
- [ ] **Step 2:** `pytest tests/test_record_identity.py -v` → FAIL (`smeltery_identity` undefined).
- [ ] **Step 3: Implement `smeltery_identity()`** by the same `direct_url.json` / `_git` logic as `ferric_identity`
  applied to the `smeltery` distribution; add the `smeltery` field to `RunRecord` after `ferric`.
- [ ] **Step 4:** tests pass; also `pytest tests/test_cli.py tests/test_run_outputs.py -q`.
- [ ] **Step 5: Commit** `record: stamp the smeltery commit a run actually used`.

### Task 1.2: Format-preserving config edits

**Files:** Create `src/smeltery/gui/config_text.py`; Modify `pyproject.toml` (`tomlkit>=0.13` in `bench` and
`gui` extras); Test `tests/test_config_text.py` (create)

**Interfaces:**
- `apply(text: str, new_cfg: dict) -> str`: the text edited so it parses to `new_cfg`, keeping comments,
  order and layout of everything unchanged; tables removed from `new_cfg` are removed with their comments.
- `set_value(text: str, dotted: str, value) -> str` (used by Task 1.3).
- `diff_lines(old: str, new: str) -> list[tuple[str, str]]` (`("+"|"-"|" ", line)`).
- `ConfigTextError(ValueError)` with `.line` for text that does not parse.

- [ ] **Step 1: Failing tests:** `test_every_example_round_trips_byte_for_byte` (each `examples/*.toml`:
  `apply(t, tomllib.loads(t)) == t`); `test_setting_one_knob_keeps_every_comment` (mwe_docking: set
  `poses.n_poses = 4`; every `#` line of the original is still present, `tomllib.loads(result)` equals the edited
  dict); `test_removing_a_table_removes_only_it`; `test_unparseable_text_raises_with_the_line`.
- [ ] **Steps 2-5:** fail → implement → pass → commit `gui: edit config text without losing comments`.

### Task 1.3: A run directory holds its config and inputs

**Files:** Create `src/smeltery/run_inputs.py`; Modify `src/smeltery/cli.py` (`main` run path, `write_outputs`),
`src/smeltery/bench/use_cases/launch_run.py` (replace `_stage`); Test `tests/test_run_inputs.py` (create),
`tests/bench/test_launch.py`

**Interfaces:**
- `INPUT_PATHS: tuple[tuple[str, str], ...] = (("structure", "spec"), ("pocket", "file"), ("docking", "receptor"), ("decisions", "file"))`
- `stage_inputs(text: str, config_dir: Path, out_dir: Path) -> tuple[str, dict[str, dict]]`: copies every
  `INPUT_PATHS` value that names an existing file (resolved against `config_dir`) to
  `out_dir/inputs/<sha256><suffix>`, returns the config text with those values rewritten to the relative path
  `inputs/<sha256><suffix>` (format-preserving, Task 1.2's `set_value`) and
  `{"structure.spec": {"source": str, "sha256": str, "copy": str}, ...}`. A value that is not a file (a PDB id)
  is left as is and not listed.
- CLI: `smeltery run CONFIG --out OUT` writes `OUT/config.toml` (staged text), records
  `rec.results["config"] = {"file": "config.toml", "sha256": <of staged text>, "inputs": <map>}`, and runs from
  the staged copy. Without `--out` nothing changes.
- `LaunchRun.run` calls `stage_inputs` instead of `_stage`.

- [ ] **Step 1: Write failing tests**
```python
def test_a_run_directory_reruns_after_the_original_inputs_move(tmp_path):
    # data source: examples/mwe_docking.toml's own relative paths
    out = run_cli("examples/mwe_docking.toml", tmp_path / "out", plan_only=True)  # --plan --out stages too
    staged = (tmp_path / "out" / "config.toml").read_text()
    assert "../tests" not in staged and "inputs/" in staged
    shutil.move(tmp_path / "out", tmp_path / "moved")
    assert main(["run", str(tmp_path / "moved" / "config.toml"), "--plan"]) == 0


def test_a_pdb_id_is_left_alone_and_comments_survive_staging(tmp_path):
    ...
    # structure.spec = "5R83" stays "5R83"; a "# keep me" comment line is still in the staged text


def test_the_record_names_each_input_by_sha256(tmp_path):
    ...
    # rec["results"]["config"]["inputs"]["pocket.file"]["sha256"] == sha256 of the source file
```
Plus in `tests/bench/test_launch.py`: Launch on a pipeline whose `[pocket] file` is relative to the config's
directory (not the server cwd) stages it (fails today: `_stage` resolves against cwd).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement. **Step 4:** pass, plus the `cli.py` core-file tests.
- [ ] **Step 5: Commit** `run: a run directory carries its config and every input it read`.

### Task 1.4: Step timings and a per-SCF log

**Files:** Modify `src/smeltery/cli.py` (`_stage`, `_log`, `write_outputs`), `src/smeltery/tiers.py`
(`FieldInteraction.run`, `FieldDftInteraction.run`); Test `tests/test_cli.py`, `tests/test_calc_log.py` (create)

**Interfaces:**
- Each `stage_log` entry gains `seconds: float` (wall time of that `_stage` call).
- Both tiers gain `calc_log: list[dict]` (field, `repr=False, compare=False`, reset at `run` start), one dict per
  SCF: `{"candidate", "pose", "leg": "vacuum"|"field", "converged", "iterations", "energy_hartree", "seconds"}`
  (`energy_hartree` = `energy` for RHF, `_dft_energy(result)` for DFT).
- `write_outputs` writes `OUT/calcs.jsonl` (one line per entry) and sets
  `results["calcs"] = {"file": "calcs.jsonl", "n": len(calc_log)}`. A non-converged SCF is logged before the
  tier raises, so a failed run still leaves its log (write it in `main`'s `StageError` path too when `--out` is set).

- [ ] **Step 1: Failing tests:** `test_every_scf_is_logged_with_its_leg_iterations_and_time` (needs_ferric; tiny
  water case from `golden_path`: 2 entries per pose, `iterations > 0`, legs `{"vacuum","field"}`);
  `test_a_failed_scf_leaves_its_log` (`max_iter=1` → exit 3 and `calcs.jsonl` exists with `converged false`);
  `test_each_stage_records_its_seconds` (injected `_KnownValues` run: every `stage_log` entry has `seconds >= 0`).
- [ ] **Steps 2-4:** fail → implement → pass (+ core-file tests). **Step 5: Commit** `run: per-step seconds and a log line per SCF`.

### Task 1.5: Gates record every pose before failing

**Files:** Modify `src/smeltery/cli.py` (`_run_gates`, `write_outputs`); Test `tests/test_cli.py`

**Interfaces:** `results["gates"] = {"posebusters": {cand: PoseReport.to_dict()}, "strain_kcal": {cand: [float]}}`
for the gates that ran. On any failure every candidate is still evaluated, `OUT/gates.json` is written with the
same dict, and one `StageError` names every failing candidate and pose (exit 3, unchanged).

- [ ] **Step 1: Failing test:** extend `test_posebusters_rejects_unrelaxed_analogue_poses_loudly_with_the_reason`:
  with two failing analogues, the error names both, and `gates.json` lists per-pose `failed_checks` for every
  candidate including the passing parent.
- [ ] **Steps 2-5:** fail → implement → pass → commit `run: pose checks report every pose, then fail`.

---

## Phase 2: Lineage on the results that exist

### Task 2.1: Lineage API

**Files:** Create `src/smeltery/bench/api/lineage.py` (router, included in `app.py`); Modify
`src/smeltery/bench/api/models.py`; Test `tests/bench/test_lineage.py` (create)

**Interfaces:** `GET /api/runs/{idx}/lineage?candidate=NAME` → `Lineage`:
```
candidate, quantity ("dE_int"), parent, mean, sem, n,
poses: [{pose:int, value:float, parent_value:float, difference:float}],
steps: [{stage, kind, seconds|None, settings: dict}],        # stage_log joined with rec.tiers by name
field: {n_charges:int, provenance: dict|None},
engine: dict (rec.ferric), smeltery: dict|None, digest, started_utc,
config: {file, sha256, inputs}|None, calcs: {file, n}|None,
missing: list[str]                                            # names of the items above this run did not record
```
404 for an unknown candidate, 409 for the parent (it has no ΔΔE) and for INCOMPLETE runs (via `common.run`).

- [ ] **Step 1: Failing tests:** `test_lineage_numbers_are_the_records_own` (data source: the fixture record's
  `per_pose` and `paired_ddE`; `difference == value - parent_value` per pose and `mean` equals the record's);
  `test_a_run_from_before_provenance_lists_what_it_did_not_record` (the `gui_fixture` record has no `config`/`calcs`/
  `smeltery` → those three in `missing`, status 200); `test_the_parent_and_unknown_candidates_are_refused`.
- [ ] **Steps 2-4:** fail → implement → pass (bench gate). `npm run gen:api`.
- [ ] **Step 5: Commit** `bench: lineage of a paired ΔΔE`.

### Task 2.2: Lineage inspector

**Files:** Create `ui/src/components/LineageInspector.tsx`; Modify `ui/src/pages/run/Results.tsx` (the Report is unchanged); Test `ui/e2e/lineage.spec.ts` (create)

**Interfaces:** `<LineageInspector idx candidate onClose />` renders in the shell's `.inspector`; the Results
table's candidate rows and the per-pose values are buttons (`aria-label="Trace {candidate}"`).
Sections: the per-pose table (as `workspace.html` panel 3), "Made by" (steps with settings and seconds, field,
engine, smeltery, digest, config file, ran at), items in `missing` shown as "not recorded for this run".
"Open the pipeline that made this" and "Change a setting and re-run" are added in Task 3.4, once pipelines exist.

- [ ] **Step 1: Failing spec:** clicking "Trace 4-F" on `/run/0/results` shows a per-pose table whose values equal
  `/api/runs/0/lineage?candidate=4-F` (data source: the API) and "not recorded for this run" for `config`.
- [ ] **Steps 2-4:** fail → implement → pass; UI gates. **Step 5: Commit** `bench: click a result, see what made it`.

---

## Phase 3: The drawn pipeline, edited in place

### Task 3.1: Workspace pipelines on disk

**Files:** Create `src/smeltery/bench/ports/pipeline_store.py`, `src/smeltery/bench/adapters/pipeline_store.py`;
Create `src/smeltery/bench/api/pipelines.py`; Modify `app.py` (`create_app(..., pipeline_store=None)`), `models.py`;
Test `tests/bench/contracts/test_pipeline_store.py`, `tests/bench/test_pipelines_api.py` (create)

**Interfaces:**
- Port `PipelineStore`: `list() -> list[str]`, `get(name) -> PipelineFile(name, text, sha256, folder: Path)`,
  `put(name, text, base_sha256: str | None) -> PipelineFile` (raises `StaleWrite` when `base_sha256` is not the
  current file's), `create(name, text, files: dict[str, Path]) -> PipelineFile` (copies `files` into the folder).
- `FilesystemPipelineStore(root)`: `ROOT/pipelines/<name>/config.toml`, names `[a-z0-9][a-z0-9_-]{0,63}`.
- Routes: `GET /api/pipelines`; `GET /api/pipelines/{name}`; `PUT /api/pipelines/{name}` body `{toml, base_sha256}`
  → 200 with issues (saves even with errors), 409 on `StaleWrite`, 400 on `ConfigTextError` (message has the line);
  `POST /api/pipelines` body `{name, from_run?: int, from_prebuilt?: str, blank?: {campaign, parent, candidates}}`
  → 201. `from_run` uses the run's `config.toml` when recorded (sites recovered), else today's
  `pipeline/from_run` reconstruction with its note.

- [ ] **Step 1: Failing tests:** contract tests (put/get round trip, stale write refused, names validated);
  `test_a_draft_with_errors_is_saved_and_reports_them`; `test_a_second_save_on_a_stale_base_is_409_and_keeps_the_first`;
  `test_from_a_run_with_a_recorded_config_keeps_its_sites` (data source: a fixture run dir with `config.toml`
  containing `[[pocket.site]]`).
- [ ] **Steps 2-5:** fail → implement → pass (bench gate, `gen:api`) → commit `bench: pipelines are files in the workspace`.

### Task 3.2: The steps model

**Files:** Create `src/smeltery/gui/steps.py`; Test `tests/test_gui_steps.py` (create)

**Interfaces:**
```python
STEPS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("active_site", "Active site", ("structure", "pocket", "docking")),
    ("ligands", "Ligands", ()),                       # campaign, parent, candidates, decisions
    ("poses", "Paired poses", ("paired_poses",)),
    ("checks", "Pose checks", ("posebusters", "forcefield", "gfn2")),
    ("measure", "ΔE_int", ("field_interaction", "field_dft_interaction")),
    ("cut", "Cut", ("cut",)),
)
@dataclass
class StepView:
    key: str; label: str; summary: str                 # summary from catalog Method.summary
    state: Literal["ready", "needs_input", "off", "changed", "stale"]
    inputs: list[dict]   # {"from": step key | None, "text": str}
    gives: list[dict]    # {"to": step key, "text": str}
    methods: list[str]; chosen: str | None             # catalog keys offered / placed
    params: list[dict]   # {"name","value","default","help","kind","changed_from"|None,"workspace_default"|None}
    issues: list[Issue]
def steps(p: Pipeline, baseline: Pipeline | None = None, defaults: dict | None = None) -> list[StepView]
```
`changed`: the step's methods or params differ from `baseline`; `stale`: any step after the first changed one;
`needs_input`: an error issue belongs to one of its methods; `off`: an optional step with nothing placed.

- [ ] **Step 1: Failing tests:** `test_the_six_steps_cover_every_wired_method_once`;
  `test_editing_poses_marks_poses_changed_and_measure_and_cut_stale`; `test_a_missing_measurement_is_needs_input_on_measure`;
  `test_each_param_reports_its_previous_value_only_when_changed`.
- [ ] **Steps 2-5:** fail → implement → pass (`tests/test_gui_*.py`) → commit `gui: the six steps of a pipeline`.

### Task 3.3: Impact of an edit

**Files:** Modify `src/smeltery/cli.py` (split `describe_memory` into `memory_estimate(plan) -> dict` + text);
Create `src/smeltery/bench/use_cases/impact.py`; Modify `api/pipelines.py`; Test `tests/bench/test_impact.py`

**Interfaces:** `POST /api/pipelines/impact` body `{toml, baseline_toml, source_run?: int}` → `Impact`:
`{seconds: {value|None, basis}, memory: memory_estimate dict, changes: [step keys], new_digest: bool,
poses_from: "computed" | "source run" | "none", issues, preview_task: str|None}`.
Rule (spec settled 4): with no `[docking]`/`[structure]`, `build_plan` runs (no SCF) and estimates use its poses;
otherwise if the config sections up to poses (`structure`, `pocket`, `docking`, `candidates`, `parent`, `poses`)
equal the source run's recorded config, estimates use that run's poses (`results.poses` SDFs); else
`poses_from = "none"` and every pose-dependent value is `None` with basis "cannot say until poses exist".
`POST /api/pipelines/{name}/preview` submits `smeltery run <file> --plan --out <folder>/preview` as a task and
returns its id; `--plan --out` writes `preview/plan.json` (`{digest, seconds, memory}`) and pose SDFs; impact uses a
`plan.json` whose digest matches.

- [ ] **Step 1: Failing tests:** `test_a_hand_placed_pipeline_gets_live_estimates` (benzoic: `seconds.value > 0`,
  `memory` equals `memory_estimate(build_plan(cfg))`); `test_a_docking_edit_upstream_of_poses_cannot_say`
  (mwe_docking with `seeds` changed and no source run → `poses_from == "none"`, `seconds.value is None`);
  `test_a_docking_edit_downstream_reuses_the_source_runs_poses`; `test_impact_never_runs_docking` (monkeypatch
  `cli._dock_parent` to raise; impact still answers).
- [ ] **Steps 2-5:** fail → implement → pass (core-file tests for cli.py, bench gate, `gen:api`) → commit
  `bench: what an edit costs, without running it`.

### Task 3.4: The Workspace page

**Files:** Create `ui/src/pages/run/Workspace.tsx`, `ui/src/components/workspace/{PipelineDrawing,StepPanel,ParamRow,ImpactStrip,ConfigDiff}.tsx`;
Modify `ui/src/App.tsx` (route `/pipelines/:name`, run tab "Pipeline" opens the workspace for that run),
`ui/src/pages/BenchHome.tsx` (list pipelines, "New pipeline"), `ui/e2e/routes.ts`; Delete the old
`ui/src/pages/run/Pipeline.tsx` and `pipeline/{Palette,KnobForm}.tsx` once nothing imports them;
Test `ui/e2e/workspace.spec.ts` (create; port the still-meaningful cases of `pipeline.spec.ts`, then delete it)

**Interfaces:** consumes Tasks 3.1-3.3. Layout per mockup `step-in-place.html`: drawing on top (state per step),
selected step below with columns Inputs | Method | View (View is a placeholder until Phase 4), impact strip with
Discard / Keep, config diff under it, TOML pane (read/write) beside. Keep → `PUT` with `base_sha256`; Run uses the
existing launch route, disabled with the error count while `check()` has errors. The lineage inspector (Task 2.2)
gains "Open the pipeline that made this" (`POST /api/pipelines {from_run}` then navigate) and "Change a setting and
re-run" (the same, opening the step the clicked number came from: `measure`).

- [ ] **Step 1: Failing spec:** `test("edit n_poses in place: old value shown, later steps marked, Keep writes one diff line")`
  (data: after Keep, `GET /api/pipelines/x` text differs from before only in `n_poses`); `test("a draft with an error
  is saved and Run says why it is blocked")`; `test("unparseable TOML in the pane blocks a step edit with the line number")`;
  `test("night mode: every workspace surface uses token colours")` (dark scheme, no computed colour outside the token set).
- [ ] **Steps 2-5:** fail → implement → pass (full UI gates) → commit `bench: the pipeline is the page`.

---

## Phase 4: What each step shows

All views live in `ui/src/components/workspace/views/<Step>View.tsx`, receive `{pipeline, step, sourceRun}`, and
read data from the APIs below; before a run they show what needs no SCF and say what needs a run.

### Task 4.1: Active site, three ways

**Files:** `views/ActiveSiteView.tsx`; Modify `api/pipelines.py`; Test `tests/bench/test_pocket_preview.py`, `ui/e2e/active_site.spec.ts`

**Interfaces:** choices Structure | Pocket file + receptor | Hand-placed. "Use as active site" from the structure
panel (existing `/api/structure`) writes `[structure]` (spec, reference_ligand or center, pocket_cutoff) and adds
`docking`. `POST /api/pipelines/pocket_preview {toml}` → `{n_charges, net_charge, nearest_ang, scene}` or 409
naming the missing extra/binary (`pdb2pqr30`, `gemmi`) when prep cannot run here. Hand-placed reuses `SitePlacer`.

- [ ] Tests: preview on `tests/data/pocket_pep5.pdb` matches `load_pocket` on the same file (data source);
  missing `pdb2pqr30` → 409 with its name; e2e switches hand-placed → structure and the TOML gains `[structure]`
  and `[docking]`. Commit `bench: load an active site into step 1`.

### Task 4.2: Ligands

**Files:** `views/LigandsView.tsx`; Modify `api/pipelines.py`; Test `tests/bench/test_ligands_check.py`, e2e.

**Interfaces:** `POST /api/pipelines/ligands {toml}` → per candidate `{name, smiles_ok, formal_charge,
electrons_even, sto3g_ok, core_atoms: list[int]}` (`sto3g_ok` false when an element is outside
`cost._STO3G_NBF`; `core_atoms` from the same MCS `PairedPoses` uses). View: `Structure2D` with core highlighted,
a row per check. The view edits the list: add, remove or rename a candidate, change its
SMILES, pick the parent (writes `[candidates]` and `parent`; removing the parent is refused with the reason).

- [ ] Tests: a Br analogue gets `sto3g_ok False` with the element named (Review Focus 4); an odd-electron radical gets
  `electrons_even False`; core atoms for 4-F are every atom but the F and its ring position's H counterpart (data
  source: `PairedPoses().run` scaffold map). Commit `bench: ligands checked before they run`.

### Task 4.3: Poses, checks, measure, cut

**Files:** `views/{PosesView,ChecksView,MeasureView,CutView}.tsx`; Modify `src/smeltery/cli.py` (record
`results["pose_maps"] = {cand: [[parent_atom, cand_atom], ...]}` from `PairedPoses.scaffold_maps`); Create
`src/smeltery/gui/views/per_pose_values.py` (`per_pose_values_figure(table, run, quantity) -> go.Figure`, styled
through `_style.layout`, themed by `?theme=`); Modify `api/runs.py` (`FIGURES += ("per_pose_values",)`); Tests
`tests/bench/test_figures.py`, `tests/test_cli.py`, `ui/e2e/step_views.spec.ts`

**Interfaces:** Poses: Mol* overlay of a ligand's poses, core atoms (from `pose_maps`) drawn solid, others outlined;
pick ligand and pose. Checks: `results.gates` table, per pose pass/fail and failed checks or strain. Measure: the
per-pose ΔE_int figure; clicking a pose (Plotly `plotly_click`) shows that pose in the field and each ligand's
value and difference, with "SCF log" opening that pose's `calcs.jsonl` lines. Cut: existing forest + matrix.

- [ ] Tests: `pose_maps` recorded and equal to `scaffold_maps`; the new figure's points equal the record's
  `per_pose` (data source) and pass the dark-theme colour test from `test_figures.py`; e2e clicks pose 3 in the
  measure view and sees benzoic and 4-F values matching the record. Commit `bench: each step shows what it produced`.

---

## Phase 5: The Methods tab

### Task 5.1: Every setting explained, workspace defaults copied in

**Files:** Modify `src/smeltery/gui/catalog.py` (`help` for every param of every wired method, taken from the
tier docstrings and `docs/tiers/*.md`); Create `src/smeltery/bench/ports/method_defaults.py`,
`adapters/method_defaults.py` (`ROOT/.smeltery/bench/methods.toml`, edited via `config_text`); Modify
`gui/pipeline.py` (`Pipeline.add(key, params=None, at=None, defaults: dict | None = None)` copies the method's
defaults into `params`), `api/pipelines.py` (`GET /api/methods`, `PUT /api/methods/{key}`); Create
`ui/src/pages/Methods.tsx` (route `/methods`, rail link "Methods"); Tests `tests/test_gui_pipeline.py`,
`tests/bench/test_methods_api.py`, `ui/e2e/methods.spec.ts`

**Interfaces:** `GET /api/methods` → per method `{key, label, summary, quantity, params (with default, help,
workspace_default), cost_model: str, memory_model: str, floor: float | None, used_by: [pipeline names]}`.

- [ ] Tests: `test_every_wired_param_has_help` (fails today for several knobs); `test_a_default_is_copied_not_referenced`
  (set `field_dft_interaction.basis = "def2-svp"` as workspace default, add the method: config text contains
  `basis = "def2-svp"`; delete `methods.toml`: the config still runs the same `--plan` digest); e2e: a step whose value
  differs from the workspace default says so. Commit `bench: configure methods once, see them everywhere`.

---

## Phase 6: Prebuilt pipelines

### Task 6.1: Package the working configs with finished runs

**Files:** Create `src/smeltery/prebuilt/{benzoic-rhf,benzoic-pbe,docking,structure}/` each with `pipeline.toml`,
`about.md` (two lines: what it shows, what re-running needs), its input files, and `run/` (the output of
`smeltery run pipeline.toml --out run`); Create `scripts/build_prebuilt.py` (re-runs all four; `docking` and
`structure` need `--extra docking --extra posebusters` and `pdb2pqr30`); Modify `pyproject.toml` (package data),
`api/pipelines.py` (`GET /api/prebuilt`; `from_prebuilt` in `POST /api/pipelines` copies config + inputs, and the
example run is listed as the pipeline's source run), `BenchHome.tsx` ("Start from a working pipeline");
Tests `tests/test_prebuilt.py`, `ui/e2e/prebuilt.spec.ts`, `scripts/wheel_smoke.py` (bench extra: prebuilt present)

- [ ] Tests: `test_each_prebuilt_run_is_its_configs_run` (`--plan` digest of `pipeline.toml` == the committed
  record's `input_digest`; verdict not INCOMPLETE); `test_starting_from_a_prebuilt_copies_it_and_leaves_it_alone`;
  e2e: start from `benzoic-pbe`, the workspace opens with every step ready and step 5's view showing the committed
  numbers. Commit `bench: start from a pipeline that works`.

---

## Finish

- [ ] Full Python suite and full Playwright suite (AGENTS.md milestone commands); update `docs/bench.md`
  (workspace, Methods, prebuilt, lineage) and the README's Bench paragraph; screenshots of every new page in light
  and dark, reviewed, attached to the PR as for #119.
