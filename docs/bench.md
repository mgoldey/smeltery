# Bench

Bench is the application over the smeltery funnel: one local web app in which a computational
chemist opens a structure, edits and launches a pipeline, and reads its runs, and a bench chemist
reads a run's report and answers it. It shows runs, tasks, and verdicts, and it schedules the runs
it launches: one at a time by default, each with a memory ceiling and an optional wall-clock limit
(see [Queueing and limits](#queueing-and-limits)). It still sets no priorities: the queue is
first-in, first-out. `memory_budget_gb` sizes the arrays one calculation predicts and is a different
number from the task's ceiling.

## Starting it

```
pip install 'smeltery[bench]'          # the wheel ships the built UI
smeltery bench ROOT [--study pkg.module] [--port 8765] [--max-runs 1] [--open]
                   [--host 127.0.0.1] [--allow-host NAME] [--token SECRET]
```

ROOT is searched recursively for run records (`run-*.json`). Bench keeps its own state (comments,
decisions, campaigns, attached artefacts, task logs, method defaults) under `ROOT/.smeltery/bench/`;
pipelines are folders under `ROOT/pipelines/`. `--study`
names a study by Python module path and may be repeated.

From a source checkout the UI must be built first; without it `smeltery bench` exits 2 and says so:

```
cd ui && npm ci && npm run build      # writes src/smeltery/bench/web/ (build output, not committed)
```

The UI is React and TypeScript in `ui/`. Every figure is built in Python (`smeltery.gui.views`)
and drawn by Plotly; structures are drawn by Mol* from MolViewSpec scenes the API builds; 2D
depictions and atom picking use RDKit.js. The pipeline exists only as TOML, carried through
`smeltery.gui.pipeline.Pipeline`; every view of it is derived from that text.

## Security

Bench starts work on the machine it runs on, so it trusts only what it can tell is local. The guard
is `smeltery.bench.security`, put in front of every route by `build_app`:

- **Host allow-list.** A request whose `Host` header is not `localhost`, a loopback address, the
  `--host` it binds, or a name passed with `--allow-host` (repeatable) gets 400. This defeats DNS
  rebinding, where a hostile page re-resolves its own name to 127.0.0.1.
- **Origin check.** A POST, PUT, PATCH or DELETE carrying an `Origin` whose host is not allowed (or
  `Origin: null`) gets 403. Same-origin requests and requests with no Origin (curl, scripts) pass.
- **Token off loopback.** `--host` anything but loopback (e.g. `0.0.0.0`) requires `--token` or
  `SMELTERY_BENCH_TOKEN`; without one `smeltery bench` exits 2. With a token, every request except
  `/healthz` and `/livez` needs `Authorization: Bearer <token>` or the cookie set by visiting the
  `/?token=<token>` URL printed at startup (HttpOnly, SameSite=Strict; the redirect drops the token
  from the address bar). Tokens are compared in constant time. On loopback a token is optional.
- **No arbitrary commands.** There is no route that runs a command the client chooses: tasks come
  only from launching or previewing a pipeline, which run `smeltery run` on a checked config.

Binding `0.0.0.0` and browsing by the machine's name needs that name allowed:
`smeltery bench ROOT --host 0.0.0.0 --allow-host bench.lab --token "$(openssl rand -hex 16)"`.
The token travels in clear over HTTP; put Bench behind TLS when it leaves a trusted network.

## The workspace

The pipeline is the page. A pipeline is a folder `ROOT/pipelines/<name>/` holding `config.toml`
(the same file `smeltery run` reads) and the input files it names. The TOML text is the only
state: every form in the workspace writes lines into it, and nothing about a pipeline exists only
in the browser.

### Starting

The Bench home page (`/bench`) opens on **Start here**: the three numbered ways to start a pipeline,
with copying a working pipeline marked as the easiest. Below it, **Your work** lists the pipelines in
the workspace and the runs under ROOT, then the work panel (running and finished tasks, their logs,
cancel), and last the campaign notebooks and configured studies.
A pipeline starts in one of three ways. Names are lower case letters, digits, `-` and `_`.

- **Start from a working pipeline.** Five pipelines ship with smeltery. Each is copied into the
  workspace as your own pipeline, with the input files its config reads and a finished example run,
  so the result, the views and the provenance can be read before anything is changed. The packaged
  original is never written to, and the config is copied exactly (workspace defaults are not applied).
  - `benzoic-rhf`: benzoic acid and two para analogues (4-F, 4-Cl) in a clamp of two +1 charges,
    ranked by field-interaction RHF. Re-running needs only the base install.
  - `benzoic-pbe`: the same clamp ranked with KS-DFT (PBE). Re-running needs only the base install.
  - `docking`: ethanol docked with Vina into a committed pentapeptide pocket; propanol and butanol
    paired to its poses, gated by PoseBusters and MMFF strain, then ranked by RHF. Re-running needs
    the `docking` and `posebusters` extras and `pdb2pqr30` on PATH.
  - `danuglipron-halogen`: the danuglipron halogen campaign as a pipeline: 12 F/Cl/CH3 analogues of
    danuglipron at four aromatic sites, paired to poses from a bound conformer (`[bound_pose]`, the
    cryo-EM conformer plus seeded jitters) in the 7LCJ pocket, ranked by field-interaction RHF/STO-3G,
    with a remedy ladder for SCFs that do not converge and a `[charge_sensitivity]` check (AMBER
    against CHARMM). Re-running needs `pdb2pqr30` and is a long Temporal run (see
    [temporal-runs.md](temporal-runs.md)). It ships two runs:
    - `imported/`: the 2026-10-16 result of this campaign, **imported** rather than re-run. An
      imported run is a record converted from an original result file (kept beside it under
      `imported/original/`) by an importer; nothing the original lacks is invented. Keys the original
      did not record are listed in `results.not_recorded`; a view that needs one of them answers that
      the run was imported and the key was not recorded, instead of leaving it blank or filling it in. The import is
      UNRANKED at the declared 1.26 kcal/mol floor.
    - `run/`: the pipeline's own run, made with `smeltery run --temporal`: a long Temporal run (hours
      of SCFs on several workers), not something to repeat casually.
  - `structure`: the structure-first path. A PDB file is prepared for Vina, the pocket field is cut
    around a stated centre, then the `docking` pipeline runs. Same requirements as `docking`.
- **A blank pipeline.** Give a campaign name, a parent (name and SMILES) and one or more analogues.
  The pipeline gets paired poses, a pocket, the RHF measurement and the cut, with the workspace
  defaults of the Methods tab copied in. It is not runnable yet: the pocket needs charges or a file
  and the cut needs `keep`, and the steps say so.
- **A pipeline from a run.** "Start from run" copies the `config.toml` that run kept, with the input
  files it copied, exactly. For a run that has no `config.toml` (made before runs kept one) the
  pipeline is rebuilt from the run record: candidates, parent, the cut's `keep` and `z`, a floor
  that came from the config, and the gates that ran. The record holds pocket charges as positions,
  not as the atom sites that placed them, so hand-placed sites are not recovered and the workspace
  says so. The source run is never changed; running the new pipeline makes a new run directory.

A run's Pipeline tab opens (and the first time creates) the pipeline that made that run.

### The drawn pipeline

The workspace (`/pipelines/<name>`) draws the steps in the order `smeltery run` executes them:
Active site, Ligands, Paired poses, Pose checks, ΔE_int, Cut. Steps can be added and removed, never
reordered, and the ranked quantity is always ΔE_int. Each step shows one state:

- **ready**: nothing to report.
- **needs input**: `Pipeline.check()` reports an error on this step; the error is shown on this step
  only, in the check's own words.
- **off**: an optional step (the pose checks, docking) that this pipeline does not place.
- **changed**: the draft differs from the kept config in this step's methods or settings.
- **result will change**: a later step that is placed, after the first changed step. Its output
  depends on the edit, so it would differ in a new run.

Issues that belong to no step are listed above the drawing.

### Editing a step

Selecting a step opens its panel:

- **Inputs**: what flows in from which step and what the step gives to the next.
- **Method**: where a step offers a choice (RHF or KS-DFT; the three ways of giving an active site)
  the choice, and every setting of the placed methods with a one-line explanation taken from the
  method's own definition. A setting that differs from the kept config shows its previous value and
  can be reset; a setting that differs from the workspace default says so.
- **View**: what the step produced (below).
- **Impact**, a strip under the panel: wall time, memory per calculation against the budget, free
  memory against `require_free_gb`, which steps change, whether the edit makes a new input digest
  (a new run recomputes every step; nothing is cached), and where the poses behind the estimates came
  from. These come from the measurement tier's `estimate_cost` and `estimate_memory`, the same
  memory facts `smeltery run --plan` prints, and `Pipeline.check()`. They are computed without docking
  or an SCF, from poses placed by RDKit, or from the source run's poses when nothing upstream of the
  poses changed. When poses do not exist yet the strip says "cannot say until poses exist" and offers
  **Preview poses**, which runs `smeltery run --plan` on the kept config as a task. Memory is a
  per-calculation figure; a long run has been measured holding about 11 GB against a prediction of about 2 GB, so leave headroom beyond it.
- The lines the edit writes to the config, as a diff. The config itself is a **Download config.toml** button above
  the steps (the draft, when edits are not kept yet), with the raw text folded under **Edit as TOML** for whoever
  wants to type it.

Edits are a **draft** until you press **Keep**, which saves the text as the pipeline's `config.toml`.
**Discard** restores the selected step to the kept text, and **Discard all** drops every edit. Keep
writes the config even while it has errors, because some changes take several edits (switching
the active site to a structure also needs docking). Only **Run** is blocked: it runs the kept
config, is disabled while the kept config has an error (each one is listed, in the CLI's own words)
or the draft has edits that are not kept. Saving over a config that changed on disk since you
opened it is refused rather than overwritten.

Step edits change only the lines they touch, so comments, ordering and layout in the TOML survive.
The TOML pane (under **Edit as TOML**) edits the same text directly; if it does not parse, step edits stay blocked
until it does and the error names the line.

The three columns of a step (Inputs, Method, View) are resized by dragging the dividers between them, by the arrow
keys when a divider has focus, and reset by double-clicking one. The widths are remembered in that browser only.

**Run** copies every input file the config reads into the run directory and runs from there (see
Provenance). The run directory defaults to a dated folder under `ROOT/out/`.

### What each step's view shows

1. **Active site**, three ways to give it, each editable:
   - a protein structure (`[structure]`): a local `.pdb` (a PDB id or accession only with network
     allowed), its hash and origin, chains and HETATM groups, a reference ligand or a centre and the
     `pocket_cutoff`. "Use as active site" writes `[structure]` and turns docking on;
     **Choose a PDB file...** copies a file from the browser into the pipeline folder and opens it; the
     config then names it by its plain name, relative to that folder. Every setting that names an input
     file (the methods' `file_knobs`: `[structure] spec`, `[pocket] file`, `[docking] receptor`,
     `[bound_pose] file`) offers the same picker in the Method column, and the field starts from what the
     config already says, so the Method column and this view never name two different structures;
   - a prepared pocket file with a receptor and box (`[pocket] file` with `[docking]`);
   - hand-placed charges: click two atoms of the parent to place a charge.
   For the first two the view builds the point-charge field without docking or an SCF, draws it in 3D
   with the cutoff sphere and the reference ligand, and states the charge count, the net charge and
   the nearest charge to the centre. A `.pdb` needs `pdb2pqr30` to assign charges; if it is missing
   the view says so. Hand-placed charges are placed on the parent's pose when the pipeline runs, so
   there is nothing to preview. Switching away from hand-placed charges asks before dropping them.
2. **Ligands**: the campaign, parent and analogues, editable (add, remove, rename, change a SMILES,
   choose the parent; removing the parent is refused). Each is checked before anything runs, with
   RDKit only: the SMILES parses, its formal charge, the electron count at that charge (the
   closed-shell RHF and DFT tiers cannot run an odd count), whether STO-3G covers its elements, and
   the core it shares with the parent, highlighted. ferric's bundled STO-3G reaches xenon, so a
   brominated or iodinated ligand is covered; one with an element past it is flagged as uncovered,
   and it would fail at the SCF. The core search is the
   one `smeltery run` uses, with the same time limit; a timeout is reported as a problem, not as
   "no core".
3. **Paired poses**: each ligand's poses in the site, with the atoms copied exactly from the parent
   (the core) drawn apart from the atoms placed fresh. Needs a run to show.
4. **Pose checks**: a pass, fail or "not checked" for every pose, with the failed PoseBusters
   criteria and the MMFF strain in kcal/mol. A pose is "not checked" when neither gate judged it,
   for example strain recorded with no limit set; that is not a pass. `smeltery run` evaluates every
   gate on every pose before failing the stage, so the failures can be read.
5. **ΔE_int**: every pose of every ligand as a marker. Clicking one shows that pose in the field
   and each ligand's value there; the SCF log lines of that pose are one click away.
6. **Cut**: the forest plot and resolved matrix of the Results page.

A view that needs a run says plainly that the step has not run. It never shows an estimate in place
of a result.

### RHF or KS-DFT

The ΔE_int step offers `[field_interaction]` (RHF, the default) or `[field_dft_interaction]` (KS-DFT:
`functional`, `basis`, `memory_budget_gb`, `require_free_gb`, ...). A config selects one, never both;
choosing one in the step removes the other. The memory check counts basis functions for STO-3G only:
with another basis, or an element outside the STO-3G table, memory is "cannot say" and the run will
not refuse on it. `smeltery run CONFIG --plan` prints the same memory estimate (largest pose, basis
functions, GB against the budget, free memory against `require_free_gb`) without running an SCF.

## The Methods tab

`/methods` lists every method a pipeline can place. For each: what it measures (or "nothing" for a
method that prepares, refuses or decides), its cost model and memory model, with their limits written
out (for example "memory is modelled for STO-3G only"), its systematic floor in kcal/mol or
"unmeasured", the pipelines in this workspace that place it, and a table of every setting with its
built-in default, a workspace default and what the setting does.

A workspace default is **copied** into a step when the method is added to a pipeline, and the step
then holds the value explicitly in its config. A config never refers to workspace defaults, so
changing one reaches no existing pipeline and a run can always be redone from its own directory.
Defaults are stored as plain TOML in `ROOT/.smeltery/bench/methods.toml`, one table per method; a
hand-broken file is reported with its line. Pipelines copied from a prebuilt pipeline or a run
get no workspace defaults. The pocket sites are a per-pipeline setting and have no default.

To add a method to the catalog, see [methods.md](methods.md).

## Provenance and lineage

A run started with `--out OUT` (as Bench's Run does) leaves a self-contained directory:

- `run-<digest>.json`, the record, which carries the smeltery source commit and whether the tree
  was dirty (read from the checkout at run time, not the version stamped at install), the ferric build,
  the host, the start time, and the wall time of every step.
- `config.toml`, the config as run, with each input path rewritten to the copy below.
- `inputs/<sha256>/<name>` for every file the config reads (the structure file, pocket file,
  docking receptor, decisions file), and `inputs/manifest.json` mapping each copy to the source it
  came from. A PDB id is not a file and is left as it is. The record names the config and each input
  by sha256, so the directory re-runs after the originals move.
- `calcs.jsonl`: one line per SCF: candidate, pose, leg (`vacuum` or `field`), converged, iterations,
  energy_hartree, seconds, and always `field_model` and `rung`. `field_model` names the pocket's
  charge model (the pdb2pqr force field of the primary pocket, else `primary`; an extra model from
  `[charge_sensitivity]` carries its own name; a `--temporal` vacuum leg, shared by every model, has
  `null`). `rung` is the remedy-ladder rung that produced the value (0 without a ladder). Lines
  written by a `--temporal` run also carry `digest`, the first 12 characters of the input digest;
  such a run can also leave `outcome` lines for a leg that did not produce a value (`not_converged`,
  `clash_refused`, `timeout` or `error`, with the error and rungs tried). The record's
  `results.calcs` gives `file`, `n` (lines), `primary` (the primary field model) and `by_model`
  (SCF lines per field model, with `shared_vacuum` for the vacuum legs; outcome lines are not
  counted). A run whose SCF fails still leaves the calculations that finished. See
  [temporal-runs.md](temporal-runs.md).
- `calcs.partial.jsonl` and `progress.json`, only while a plain (not `--temporal`) run is unfinished or
  after it was interrupted: every SCF is appended to `calcs.partial.jsonl` as it finishes (fsync'd; the
  `calcs.jsonl` line plus `digest` and `config_sha256`, 12 characters each), and `progress.json` gives
  `partial` (that file's name), `total_scfs` (the planned count: a vacuum and a field leg per pose, at
  rung 0; a remedy ladder can add lines), `reused`, `digest` and `config_sha256`. SCFs done so far is the
  partial file's line count. Rerunning the same command into OUT reuses every finished SCF of the same
  input digest and staged config (it prints `resuming: k of N SCFs reused`) and computes only the rest;
  the record and `calcs.jsonl` are the ones an uninterrupted run writes. A checkpoint of other inputs is
  refused (exit 2) unless `--force` discards it. Both files are removed when the record is written.
  SCFs of an analysis that re-measures (`[charge_sensitivity]`) are not checkpointed.
- `gates.json`, only when a gate rejected a pose and the run stopped (exit 3): every pose's result.
- the pose SDFs, and for `--plan --out`, `plan.json` with the input digest, estimated seconds and memory.

Without `--out` nothing is staged and the record goes to `out/`.

To trace a number, press Trace beside a candidate on the Results page. The lineage panel shows the
per-pose values the paired ΔΔE averages (candidate, parent, difference), then what made them: each
step's settings and seconds, the pocket field (charge count and how it was cut), the engine build, the
smeltery commit, the input digest, the config file and its hash, the calculation log and when the run
started. "Open the pipeline that made this" opens the run's pipeline in the workspace, and "Change a
setting and re-run" opens it at the ΔE_int step. The panel only joins fields the record holds. A run
made before these were recorded shows "not recorded for this run" for each missing item; nothing is
filled in.

## Other areas

**Methods** (`/methods`) is described above.

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
- `pipeline`: opens the workspace on the pipeline that made this run (see above).
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
  `figures/{name}`, `scene`, `pose_maps`, `pose_atoms`, `gates`, `calcs`, `lineage`, `decisions`,
  `comments`, `proposals`, `snapshot`; `/api/compare`; `/api/decisions/vocabulary`,
  `/api/proposals`, `/api/proposals/vocabulary`
- workspace pipelines: `/api/pipelines` (list, create from a prebuilt, a run or blank),
  `/api/pipelines/{name}` (read, save with a base hash), `/api/pipelines/{name}/launch`,
  `/api/pipelines/{name}/preview`; on the draft text: `/api/pipelines/steps`, `/edit`, `/impact`,
  `/ligands`, `/ligands/edit`, `/pocket_preview`, `/active_site`; `/api/prebuilt`
- methods: `/api/methods` (list with workspace defaults), `PUT /api/methods/{key}` (set them);
  `/api/catalog`, `GET /api/methods/{key}`
- work: `GET /api/tasks?limit=` (the newest 100 by default, at most 1000; queued and running tasks are chosen before finished ones, so history never hides live work), `/api/tasks/{id}/log` (read from the end of the file, so a huge log costs the same as a short one), `/api/tasks/{id}/cancel`; the earlier pipeline
  routes `/api/pipeline/{check,edit,site,from_run,launch}` and `/api/provisional`;
  `/api/structure`, `/api/structure/text`, `/api/structure/{sha}.pdb`
- notebook: `/api/campaigns`, `/api/campaigns/{id}`, `/api/campaigns/{id}/entries`;
  `/api/studies`, `/api/studies/{module}/dag`, `/api/studies/{module}/cells`

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
9. PipelineStore - `FilesystemPipelineStore` (`ROOT/pipelines/<name>/`; saves are checked against the hash of the text they were based on)
10. MethodDefaults - `FilesystemMethodDefaults` (`ROOT/.smeltery/bench/methods.toml`)

## Layering

Core modules never import `smeltery.bench`. Ports declare Protocols only. Adapters are the
only place with sqlite3, Path.home, or Temporal references, with one named exception:
`src/smeltery/tasks.py` also imports temporalio for the Temporal task backend. A task runs once
unless it was submitted with `retries` (0-5); a dead worker's task is reported failed, never retried
on the orchestrator's own initiative. The queue's own scheduling (which queued task starts next)
lives in its sqlite database, not in the server process, so it survives a restart.

### Tasks outlive Bench

`SubprocessTaskQueue` starts each task under its own supervisor process
(`smeltery/bench/adapters/task_supervisor.py`, in a new session). The supervisor is the task's
parent: it writes the heartbeat, the peak memory and the exit code to `tasks.sqlite` itself, and
enforces the memory limit and the retries. Bench only reads that database and sends signals, so
stopping or restarting Bench does not stop a task, and a restarted Bench reads how it ended.

Every pid is stored with its start time (`/proc/<pid>/stat` field 22, plus the boot id) and is
trusted or signalled only while that still matches. When Bench opens the queue, a `running` task
whose supervisor is gone is marked `failed` with the note "supervisor lost" (its own process is
killed if it is verifiably still running); a pid that now names another process (a reboot, or pid
reuse) fails the task and is never signalled. The database is in WAL mode with a 30 s busy
timeout, since several supervisors write to it at once.

### Queueing and limits

A launched run waits its turn. `smeltery bench --max-runs N` (default 1) sets how many launched runs
may execute at once; the rest sit in `queued` and start, oldest first, as slots free. Previews and
tasks submitted through `POST /api/tasks` are exempt: they are short and are not what fills memory.
Cancelling a queued run means it never starts. The queue is first-in, first-out, with no priorities.

One run at a time is the default because of what a long run actually holds: on this machine a DFT run
was measured at ~11 GB resident against a ~2 GB per-calculation prediction, and once it outgrew free
memory it paged the RI tensor every SCF iteration and took 67 minutes on a molecule whose peers took
140 s. Two clicks on Run must not start two of those.

Each launched run therefore gets a memory ceiling, derived rather than guessed:

- the per-calculation prediction from the pipeline's own preview (`preview/plan.json`,
  `memory.estimate.total_gb`, the figure `smeltery run --plan` prints), times a safety factor of 6
  for the run-level growth above;
- at least 4 GB, since a run is not only its SCF (the interpreter, RDKit, docking);
- never more than 80% of `MemTotal`, so a run is stopped rather than left to swap;
- with no preview, or a preview made for other poses, the host share alone; with neither, no cap.

Over the ceiling the task is killed and reported `OOM` with how far over it went. A wall-clock limit
is optional (`timeout_s` on the launch and task-submit requests): past it the task is killed and
reported `failed`, with a note saying it timed out. Neither a memory nor a time limit is ever
retried: both are verdicts about the command.

### Worker health

The work panel reports what a running task is actually doing, because "running" alone cannot tell a
long SCF from a hung one:

- **Last output** is the age of the task's most recent write to its log. A plain subprocess speaks no
  heartbeat protocol, so what it writes is the only liveness it gives for free.
- **stalled** marks a running task silent for longer than the queue's threshold (`stall_after_s`,
  five minutes by default). It is a place to look, not a verdict: an SCF can legitimately print
  nothing for hours, so the status stays `running` and nothing is killed.
- **Peak memory** is the largest resident set seen for the task *and its children*, sampled every
  `sample_interval_s`. A cap that ignored children would never fire, since smeltery's tasks shell out.
- **OOM** marks a task killed for exceeding the `memory_limit_gb` it was submitted with, and its note
  says how far over it went. Exceeding the cap is a verdict about that command, so it is never
  retried. The Temporal queue refuses a limit, and a timeout, rather than accepting one it cannot
  enforce.
