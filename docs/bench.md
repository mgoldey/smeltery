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
smeltery bench [ROOT] [--demo] [--study pkg.module] [--port 8765] [--max-runs 1] [--open]
                       [--host 127.0.0.1] [--allow-host NAME] [--token SECRET]
                       [--log-level info] [--log-json] [--stop-tasks-on-exit]
smeltery bench --demo --open           # the one-command tour: finished runs, no SCF
```

ROOT is searched recursively for run records (`run-*.json`). Bench keeps its own state (comments,
decisions, campaigns, attached artefacts, task logs, method defaults) under `ROOT/.smeltery/bench/`;
pipelines are folders under `ROOT/pipelines/`. `--study`
names a study by Python module path and may be repeated.

### Defaults and environment

Each setting takes the first of: the command-line flag, the environment variable, the default.

| Setting | Flag | Environment | Default |
|---|---|---|---|
| ROOT | positional `ROOT` | `SMELTERY_BENCH_ROOT` | `$XDG_DATA_HOME/smeltery/bench`, else `~/.local/share/smeltery/bench` |
| Host | `--host` | `SMELTERY_BENCH_HOST` | `127.0.0.1` |
| Port | `--port` | `SMELTERY_BENCH_PORT` | `8765` |
| Token | `--token` | `SMELTERY_BENCH_TOKEN` | none (required off loopback) |

ROOT is created on first use; with no ROOT Bench never searches the current directory (which would
walk `.venv` and `node_modules`). A port that is already taken prints `port N on HOST is not
available` and exits 1; pass `--port` or set `SMELTERY_BENCH_PORT`. A `SMELTERY_BENCH_PORT` that is
not a number exits 2.

`--demo` copies every prebuilt pipeline (below) into ROOT, with its inputs and finished example
run, then opens on a finished run (`benzoic-rhf`, RESOLVED). It runs no SCF, and it is idempotent:
a pipeline already in ROOT is left as it is, so a second `--demo` adds nothing and never
overwrites your edits.

From a source checkout the UI must be built first; without it `smeltery bench` exits 2 and says so:

```
cd ui && npm ci && npm run build      # writes src/smeltery/bench/web/ (build output, not committed)
```

The UI is React and TypeScript in `ui/`. Every figure is built in Python (`smeltery.gui.views`)
and drawn by Plotly; structures are drawn by Mol* from MolViewSpec scenes the API builds; 2D
depictions and atom picking use RDKit.js. The pipeline exists only as TOML, carried through
`smeltery.gui.pipeline.Pipeline`; every view of it is derived from that text.

## Docker

The repo's `Dockerfile` builds the UI in a Node stage and installs `.[bench]` into a slim Python image
(non-root user, base images pinned by digest). The default command is `--demo`, so the container
serves finished example runs and runs no SCF.

```
docker compose up --build              # or: docker build -t smeltery-bench . && docker run ... (below)
docker compose logs bench              # the sign-in URL, with the token
docker run --rm -p 127.0.0.1:8765:8765 -v smeltery-data:/data -e SMELTERY_BENCH_TOKEN=secret smeltery-bench
```

- **Token.** The container binds `0.0.0.0`, so Bench requires a token (see Security). Set
  `SMELTERY_BENCH_TOKEN` to choose one; if it is unset the entrypoint (`docker/entrypoint.sh`)
  generates a random one for that start and Bench prints the `open http://localhost:8765/?token=...`
  sign-in URL on stderr (`docker logs`). A generated token changes on every start.
- **Data.** `/data` is the Bench ROOT (`SMELTERY_BENCH_ROOT`) and a volume. `--demo` seeds it once
  and never overwrites it, so your edits survive a restart.
- **Shutdown.** `compose.yaml` passes `--stop-tasks-on-exit` (the image also sets
  `SMELTERY_BENCH_STOP_TASKS_ON_EXIT=1`), so `docker stop` cancels tasks cleanly; see Shutdown.
- **Logs.** JSON, one object per line (`SMELTERY_BENCH_LOG_JSON=1`).
- **Health.** The image's `HEALTHCHECK` calls `/healthz`, which needs no token. `/readyz` checks
  the root and the task queue but needs the token (`Authorization: Bearer`).
- **Network.** Compose publishes the port on `127.0.0.1` only. To serve other machines, drop that
  prefix, add `--allow-host NAME` to `command`, and put TLS in front.
- **Build context.** `.dockerignore` keeps `ui/node_modules`, `.venv`, `.git`, `benchmarks/` and the
  host's `src/smeltery/bench/web` out; the image installs and builds its own. The version baked in is
  `0.0.0+docker` unless you pass `--build-arg SMELTERY_VERSION=X`.

The CI job `docker` builds the image, runs it, and asserts `/readyz` is 200 and `/api/runs` is
non-empty.

## Logging

`--log-level {debug,info,warning,error}` (default `info`; env `SMELTERY_BENCH_LOG_LEVEL`) and
`--log-json` (env `SMELTERY_BENCH_LOG_JSON=1`) go to stderr, for Bench, uvicorn and the task
supervisors alike (a supervisor inherits both settings and stderr, so one task's trail reads the same
whichever process wrote the line; so a pipe on Bench's stderr, as in `smeltery bench | tee log`, stays open until the tasks end). The one-line startup URL is unchanged and is not JSON.
`--log-json` writes one object per line with `ts`, `level`, `logger`, `message`, the structured
fields below, and `exc` (a traceback) when there is one.

At `info`, every task lifecycle transition is a line on the `smeltery.bench.tasks` logger carrying
`task_id` and `event`: `submit`, `promote` (a supervisor was started), `start`, `retry` (with the
attempt and backoff), `oom`, `timeout`, `cancel`, `supervisor_lost`, and `exit` (with `status` and
`returncode`; a failure is a warning). At `debug` there is one line per HTTP request, and each
attempt. Exceptions that Bench turns into a response or a fallback are logged with their traceback.
A request line's `?token=` value is redacted, and no command line, environment or token is ever
logged.

## Shutdown

Ctrl+C or SIGTERM makes uvicorn stop gracefully, and Bench's shutdown hook then runs. Tasks run in
their own sessions under their own supervisors, so by default they **outlive Bench**: the hook logs
one warning naming every queued or running task (id and pid) so the operator knows what is still
going, and the next Bench on the same ROOT picks them up again (a queued run starts, a running one is
reported as it was).

`--stop-tasks-on-exit` (env `SMELTERY_BENCH_STOP_TASKS_ON_EXIT=1`) stops them instead. Queued
tasks are cancelled first, in one transaction, so a finishing supervisor cannot promote one into the
slot a dying task frees; then each running task's process group gets SIGTERM, and SIGKILL after a
2 s grace. A group is signalled only while the recorded pid still has the recorded identity (boot id
and start time), so a pid reused since is never touched. Stopped tasks read `cancelled` with a note
saying why. A second Ctrl+C makes uvicorn exit without waiting, and a SIGKILL runs no hook at all;
neither stops anything, and the tasks are then found by the next Bench as above.

Use `--stop-tasks-on-exit` in Docker: the container's tasks live in its process tree, and a
container that stops (or restarts) with orphaned supervisors inside it would lose them to the
runtime's SIGKILL with no record of why. With the flag a stop leaves clean `cancelled` rows instead.
Leave it off on a workstation where a multi-hour run should survive closing the terminal. The task
queue on Temporal runs work elsewhere and is not touched either way.

## Security

Bench starts work on the machine it runs on, so it trusts only what it can tell is local. The guard
is `smeltery.bench.security`, put in front of every route by `build_app`:

- **Host allow-list.** A request whose `Host` header is not `localhost`, a loopback address, the
  `--host` it binds, or a name passed with `--allow-host` (repeatable) gets 400. This defeats DNS
  rebinding, where a hostile page re-resolves its own name to 127.0.0.1.
- **Origin check.** A POST, PUT, PATCH or DELETE carrying an `Origin` whose host is not allowed (or
  `Origin: null`) gets 403. Same-origin requests and requests with no Origin (curl, scripts) pass.
- **Launch output stays in ROOT.** The `out` of a launch is resolved (`~`, `..` and symlinks
  followed) and must lie strictly inside ROOT, a relative `out` being read from ROOT; anything else
  is a 400 `{"reason": ...}` and nothing is staged.
- **One error shape.** Every 4xx/5xx body is `{"reason": "<sentence>"}` (the `Problem` model, the
  default response of every route in the OpenAPI schema); a 422 also carries the structured
  `errors` list. Handlers live in `src/smeltery/bench/errors.py`.
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

## Read-only

`smeltery bench ROOT --read-only` (or `SMELTERY_BENCH_READ_ONLY=1`; default off) serves Bench so it can
be shown publicly: every POST, PUT, PATCH and DELETE gets **403** `{"reason": "this Bench is read-only: ..."}`,
whatever the route. It is enforced once, in the security middleware, so a route added later is covered.
Order of refusal: Host (400), Origin (403), token (401), then read-only (403); an unauthenticated client never
learns the mode from a write.

- **Still served:** the UI, every GET (runs, results, poses, reports, lineage, figures, exports),
  `/healthz`, `/livez` and `/readyz`.
- **No safelist.** Some POSTs only compute (the pipeline editor's `steps`, `edit` and `impact`, the
  structure lookup, which can also fetch from the network). They are refused with the rest: a demo that cannot
  render one is better than one that can be made to write, fetch or compute on a visitor's say-so.
- **The UI follows.** `GET /api/whoami` carries `read_only`. The UI then shows a "Read-only" badge and hides
  starting, importing, running, editing, cancelling, recording a decision, commenting, proposing and archiving.
  The pages that exist only to write (the pipeline editor, Decisions, Structure) are replaced by a sentence
  saying so, and their tabs are not offered. Methods and the run's Pipeline view stay readable.
- **Pair it with a seeded workspace and a token or a proxy:** `--demo --read-only`, as in the commented
  line in `compose.yaml`. Read-only does not authenticate anyone; the token rules above still apply off loopback.

## The workspace

The pipeline is the page. A pipeline is a folder `ROOT/pipelines/<name>/` holding `config.toml`
(the same file `smeltery run` reads) and the input files it names. The TOML text is the only
state: every form in the workspace writes lines into it, and nothing about a pipeline exists only
in the browser.

### Starting

The Bench home page (`/bench`) opens on **Start here**: the three numbered ways to start a pipeline,
the first of which copies an example. Below it, **Your work** lists the pipelines in the workspace
and the runs under ROOT, then the work panel (running and finished tasks, their logs, cancel), and
last the campaign notebooks and configured studies.
A pipeline starts in one of three ways. Names are lower case letters, digits, `-` and `_`.

- **Examples you can copy.** Five pipelines ship with smeltery. Each is copied into the
  workspace as your own pipeline, with the input files its config reads and a finished example run,
  so the result, the views and the provenance can be read before anything is changed. The packaged
  original is never written to, and the config is copied exactly (workspace defaults are not applied).

  They are not peers, and the page does not list them as five alphabetical rows. `GET /api/prebuilt`
  returns a curated order with a `tier` per entry — each example declares its own `rank`, `tier` and
  optional `variant_of` in its folder's `gallery.toml`, not in a list in library code, which would
  have to name every campaign it ships (`tests/test_migration_rules.py`). The page renders one card
  per tier with the variants folded under the example they vary: **Start with this one**
  (`benzoic-rhf`, with `benzoic-pbe` under it), **What an inconclusive
  result looks like** (`docking`, with `structure` under it), and **Real campaigns**
  (`danuglipron-halogen`, a read-only reference). An example that does not resolve is shipped as a
  lesson, not hidden: each `about.md` has a third line (`teaches`) stating what its committed run
  came out as, which is what the card shows. A sixth prebuilt has to ship a `gallery.toml` of its own
  or the route refuses to list any of them; two examples claiming one `rank` are refused too. The
  file describes the shipped example, so starting a pipeline from one does not copy it.
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

The page opens with **what the funnel cannot tell you**: the measured limits of
[limits.md](limits.md), counted in the summary and one click from open. They are the frame a method
is chosen inside, so they sit above the catalogue rather than only on a report someone reads later.

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

The **Provenance** tab ends with the run's config as text, comments and all: the file the record
names, read from beside the run, with its sha256. A run whose config was not kept says so instead of
showing a config rebuilt from the record, which would read as provenance while being a guess; a file
edited since the run read it is shown but flagged, because a run is reproducible only from the bytes
it ran on.

## Other areas

**Methods** (`/methods`) is described above.

- `/studies/{module}`: a study's step graph and, for each condition and step, whether the
  result is cached or still to run.
- `/campaigns/{id}`: a campaign notebook. Entries are append-only. A correction is a new entry
  that supersedes the old one, which stays readable. Attached runs are recorded by the sha256
  of their record.
- `/compare?a=&b=`: two runs chosen as arms, side by side, with the difference of their ddE values.
  The arms are in the URL, so a comparison can be linked to and sent on. A run's Results page links in
  with its own id already set ("Compare this run with another"), and each arm links back to its run.

**Run** (`/run/{run_id}/...`) is the computational chemist's instrument panel for one run. A
context bar shows the campaign, input digest and verdict.

The five tabs follow the path a run is read along -- what it says, what it rests on, what you
decide, what to do next -- rather than listing every page that mentions the run:

- `results`: the verdict, the forest plot of ddE with the floor band, the resolved table, and
  per-pose values. It ends with "Decide on these candidates", which is the next step.
- `poses`: each candidate's poses in the pocket (Mol*), with the per-pose energies.
- `decisions`: keep, reject or hold each candidate with a reason from the core's vocabulary;
  the core refuses, for example, a within-tie choice that has no note. Each row carries its own
  mean +/- SEM and whether the cut kept it, so the judgement is made beside the number. Once
  something is recorded, "Start the next round" writes a new pipeline from this run's config with
  the decisions carried (see below).
- `proposals`: candidates proposed for review. A proposal starts no work. Tick the ones you agree
  with and "Start the next round" adds them to the new pipeline's `[candidates]`.
- `provenance`: the verdict and what the run did -- every setting, hash and log the record holds.

Two pages that used to be tabs are reached otherwise: the workspace for this run's pipeline is the
"Open the pipeline" button in the context bar, and the structure viewer is `/structure`, which
belongs to no run. Links written before the move still resolve.

**The next round.** A decision is only useful if it reaches the next run. "Start the next round",
on either `decisions` or `proposals`, creates a pipeline from this run's config with
`[decisions] file` pointing at a copy of the decisions (so `smeltery run` drops what you rejected
before posing) and any accepted proposals added to `[candidates]`. The original run is never
changed. The new config opens with a header comment naming what it carried, and Bench reads that
comment back, so the pipeline explains itself on every later visit and to anyone who only ever
reads the TOML.

**Report** (`/report/{run_id}`) is what a bench chemist reads: the verdict, figures and poses, with
comment threads anchored to the run, a candidate or a pose, and proposals. It offers no control
that starts work. Under the verdict, "Download this report" hands over a self-contained
`report-<digest>.html`: every number, plot and table inline, its only external references the pinned,
integrity-hashed 2D/3D libraries. `POST /api/runs/{run_id}/snapshot` writes the same bytes beside the
run instead, for a notebook that archives from the server.

## Addressing a run

A run's `run_id` is `<input_digest[:12]>-<hash of its path under ROOT>`: derived from the run
itself, so adding or removing other runs never changes it and a bookmark, a shared link or an
in-flight decision keeps naming the same record. `GET /api/runs` returns it as `id`.

An older link whose path segment is a bare integer (`/run/3`, `/api/runs/3/ddE`) still resolves,
as the position in `GET /api/runs`; the answer carries `Deprecation: true` and a `Link` header
with the stable URL. Nothing writes that form any more -- no response and no page link carries a
position, and `/api/compare?a=&b=` takes ids too.

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
- runs: `/api/runs`, `/api/runs/{run_id}` and its `verdict`, `ddE`, `resolved`, `per_pose`,
  `figures/{name}`, `scene`, `pose_maps`, `pose_atoms`, `gates`, `calcs`, `lineage`, `decisions`,
  `comments`, `proposals`, `snapshot`, `report.html` (the same frozen report as a download),
  `config` (the config the run read, as text); `/api/compare`; `/api/decisions/vocabulary`,
  `/api/proposals`, `/api/proposals/vocabulary`
- workspace pipelines: `/api/pipelines` (list, create from a prebuilt, a run or blank),
  `/api/pipelines/{name}` (read, save with a base hash), `/api/pipelines/{name}/launch`,
  `/api/pipelines/{name}/preview`; on the draft text: `/api/pipelines/steps`, `/edit`, `/impact`,
  `/ligands`, `/ligands/edit`, `/pocket_preview`, `/active_site`; `/api/prebuilt`
- methods: `/api/methods` (list with workspace defaults), `PUT /api/methods/{key}` (set them);
  `/api/catalog`, `GET /api/methods/{key}`
- work: `GET /api/tasks?limit=` (the newest 100 by default, at most 1000; queued and running tasks are
  chosen before finished ones, so history never hides live work), `/api/tasks/{id}/log` (read from the
  end of the file, so a huge log costs the same as a short one), `/api/tasks/{id}/cancel`;
  `/api/provisional`;
  `/api/structure`, `/api/structure/text`, `/api/structure/{sha}.pdb`
- notebook: `/api/campaigns`, `/api/campaigns/{id}`, `/api/campaigns/{id}/entries`;
  `/api/studies`, `/api/studies/{module}/dag`, `/api/studies/{module}/cells`

Every other path serves the app.

## Database versions

Every sqlite file Bench writes (`tasks.sqlite`, the comment, project and run-index stores) carries a
schema version in `PRAGMA user_version`. Opening one applies only the migration steps above its
version, in a single transaction, so a failed migration leaves the file as it was; a file written
before versioning (version 0) is adopted the same way. A file whose version is newer than the
running Bench knows is refused at startup with a message naming the file: it was written by a newer
Bench, so upgrade Bench rather than editing the file, or move it aside to start fresh. Bench never
writes to a database it refuses, so the newer Bench can still open it.

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
