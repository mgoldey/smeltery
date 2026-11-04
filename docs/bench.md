# Bench

Bench is the application over the smeltery funnel: one local web app in which a computational
chemist opens a structure, edits and launches a pipeline, and reads its runs, and a bench chemist
reads a run's report and answers it. It shows runs, tasks, and verdicts;
it does not schedule work, set priorities, or bound memory. `memory_budget_gb` sizes predicted
arrays and is not a process ceiling.

## Starting it

```
pip install 'smeltery[bench]'          # the wheel ships the built UI
smeltery bench ROOT [--study pkg.module] [--port 8765] [--open]
```

ROOT is searched recursively for run records (`run-*.json`). Bench keeps its own state (comments,
decisions, campaigns, attached artefacts, task logs) under `ROOT/.smeltery/bench/`. `--study`
names a study by Python module path and may be repeated.

From a source checkout the UI must be built first; without it `smeltery bench` exits 2 and says so:

```
cd ui && npm ci && npm run build      # writes src/smeltery/bench/web/ (build output, not committed)
```

The UI is React and TypeScript in `ui/`. Every figure is built in Python (`smeltery.gui.views`)
and drawn by Plotly; structures are drawn by Mol* from MolViewSpec scenes the API builds; 2D
depictions and atom picking use RDKit.js. The pipeline exists only as TOML, carried through
`smeltery.gui.pipeline.Pipeline`; every view of it is derived from that text.

## Areas and pages

**Bench** (`/bench`) is the home page. It lists the runs under ROOT with each one's verdict, the
work panel (running and finished tasks, their logs, cancel), campaign notebooks, and the
configured studies.

- `/studies/{module}`: a study's step graph and, for each condition and step, whether the
  result is cached or still to run.
- `/campaigns/{id}`: a campaign notebook. Entries are append-only. A correction is a new entry
  that supersedes the old one, which stays readable. Attached runs are recorded by the sha256
  of their record.
- `/compare`: two runs chosen as arms, side by side, with the difference of their ddE values.

**Run** (`/run/{idx}/...`) is the computational chemist's instrument panel for one run. A context
bar shows the campaign, input digest and verdict.

- `overview`: the verdict and what the run did.
- `results`: the forest plot of ddE with the floor band, the resolved table, and per-pose values.
- `poses`: each candidate's poses in the pocket (Mol*), with the per-pose energies.
- `structure`: open a local PDB (or a PDB id when network access is allowed), its hash and
  origin, what preparation drops, drawn in 3D.
- `pipeline`: the pipeline editor. Stages and knobs are edited as forms or as TOML in place; every
  issue is listed at once. Pocket sites are placed by clicking two atoms of the parent. The
  provisional result shows what changes before anything runs. Launch starts the run as a task.
- `decisions`: keep, reject or hold each candidate with a reason from the core's vocabulary;
  the core refuses, for example, a within-tie choice that has no note.
- `proposals`: candidates proposed for review. A proposal starts no work.

**Report** (`/report/{idx}`) is what a bench chemist reads: the verdict, figures and poses, with
comment threads anchored to the run, a candidate or a pose, and proposals. It offers no control
that starts work. "Snapshot" writes a self-contained `report-<digest>.html` beside the run for
archiving.

## Verdict states

- RESOLVED: a cut with survivors; `winner` is the top survivor.
- UNRANKED: the cut could not order the candidates (tie group spans the boundary).
- INCOMPLETE: a required result key is missing (e.g. a run whose task died mid-SCF). Never
  rendered as a blank or as a winner.

## API

The schema is served at `/openapi.json`; `python -m smeltery.bench.openapi` prints it, and
`cd ui && npm run gen:api` regenerates `ui/src/api/schema.d.ts` from it. A route or response
change without that regeneration fails CI.

- `/healthz`, `/api/whoami`
- runs: `/api/runs`, `/api/runs/{idx}` and its `verdict`, `ddE`, `resolved`, `per_pose`,
  `figures/{name}`, `scene`, `pose_atoms`, `decisions`, `comments`, `proposals`, `snapshot`;
  `/api/compare`; `/api/decisions/vocabulary`, `/api/proposals`, `/api/proposals/vocabulary`
- work: `/api/tasks`, `/api/tasks/{id}/log`, `/api/tasks/{id}/cancel`; `/api/catalog`,
  `/api/methods/{key}`; `/api/pipeline/{check,edit,site,from_run,launch}`; `/api/provisional`;
  `/api/structure`, `/api/structure/text`, `/api/structure/{sha}.pdb`
- notebook: `/api/campaigns`, `/api/campaigns/{id}/entries`; `/api/studies`,
  `/api/studies/{module}/dag`, `/api/studies/{module}/cells`

Every other path serves the app.

## Ports and local adapters

1. RunStore - `FilesystemRunStore` (SQLite index cache over run-*.json; records win over index)
2. ArtifactStore - `FilesystemArtifactStore` (sha256-verified bytes)
3. Identity - `LocalIdentity` (getpass, single trusted user)
4. Clock - `LocalClock` (UTC)
5. StructureSource - `LocalStructureSource` (local file; network only with allow_network)
6. CommentStore - `SqliteCommentStore` (anchored threads)
7. ProjectStore - `SqliteProjectStore` (campaigns, append-only entries)
8. TaskQueue - `SubprocessTaskQueue` / `TemporalTaskQueue`

## Layering

Core modules never import `smeltery.bench`. Ports declare Protocols only. Adapters are the
only place with sqlite3, Path.home, or Temporal references, with one named exception:
`src/smeltery/tasks.py` also imports temporalio for the Temporal task backend. A task runs
at most once; a dead worker's task is reported failed, not retried.
