# Bench: one application for the whole funnel

**Status: design, approved for planning (2026-11-03).**

One application a computational chemist drives from a workstation: open a structure, assemble a
pipeline by clicking, run it, look at the poses, read the ΔΔE with its floor, decide, and hand a
bench chemist something they can read and answer. ELN discipline, Avogadro's directness, IQmol's
sense that the method is visible rather than hidden.

## Intent, and why

smeltery's instruments are good and nearly unusable. The paired-ΔΔE cut, the systematic floor, the
tie groups, the run record, the decisions file: all of this exists, is tested, and is reached today
by hand-writing TOML and reading JSON. The method hierarchy is already data a GUI can draw
(`gui/catalog.py`), a pipeline is already an editable object that validates itself
(`gui/pipeline.py`), poses already render as MolViewSpec scenes (`gui/scenes.py`), and the ΔΔE
views already exist (`gui/views/`). What does not exist is one place to stand.

**The primary user is a computational chemist.** They own the method: which tier, what basis, which
pocket prep, what floor. They need to drive interactively without losing any provenance that makes
a result defensible.

**The bench chemist is the audience, and a contributor of judgement.** They receive a link. They
need to understand what came back and why one molecule beat another without knowing what STO-3G is,
and they need to be able to disagree usefully -- "nitro on an aniline, we're not making that" is
real information. Their chemistry sense flows back; the method does not flow out to them.

This asymmetry is the design's spine. Method decisions belong to the computational chemist.
Judgement is shared. The run record is for reproducing; `decisions-<digest>.json` is for judgement,
and it is already a separate file for exactly this reason.

### The result that must survive translation

The most important thing this GUI communicates is often **"this comparison does not resolve."**

It is also the result a bench chemist is least equipped to read and most likely to misread as "the
computer picked the first one." `smeltery.gui.views.compare` already carries the scar: a pooled
comparison read -3.24 kcal/mol where the per-cell median was -1.21, because one arm accepted nothing
in several cells. The pooled number was reported before the per-cell number was computed.

Every surface therefore obeys one rule: **the display makes exactly the decisions `funnel.cut`
made, and no more.** UNRANKED is a result, rendered as prominently as a winner. Tie groups are
drawn as groups. A candidate chosen from inside a tie group is recorded as a human choice with a
required reason, never as a ranking.

## Goals

1. One front door. Today there are four: `smeltery gui` (Marimo, one run), `smeltery dashboard`
   (stdlib HTTP, runs and tasks), `smeltery report` (static file), `smeltery golden open` (launches
   several). Two web stacks, no shared session, no navigation between them.
2. Open a structure and place a pocket site **by clicking an atom**, not by typing an index.
3. Edit a pipeline visually, with the same rules the CLI enforces, and keep it round-trippable to
   the TOML that is checked into git.
4. Launch and watch work that may run for hours, surviving a worker restart.
5. Campaign level: a study's cells, its DAG, and per-cell comparison, not just one run.
6. A report a bench chemist can read and comment on, plus a frozen snapshot for the ELN record.
7. Replaceable infrastructure. Local SQLite and the filesystem today; a deployed service later is
   an adapter change, not a rewrite.

## Non-goals

- **Not a modelling environment.** No geometry editing, no force-field tinkering in the viewer, no
  manual pose fitting. Avogadro is the reference for *directness*, not for scope; `smeltery
  avogadro` already hands a run to the real Avogadro for that work.
- **Not multi-user, yet.** One trusted local user. The seams are designed in; the features are not
  built.
- **Not an assay database.** No ties to measured affinity. smeltery cannot claim binding free
  energies and this GUI must not imply otherwise.
- **Not a scheduler.** It shows and submits work. It does not set priorities, pack a queue, or bound
  memory.
- **Not a replacement for Marimo.** The notebook stays, attached to a run, for the power user who
  wants to compute something the app does not offer.

## Anti-goals

Things that would make this a failure even if every feature worked:

- **A surface that resolves what the cut refused to resolve.** Any ranking, score, star rating or
  sort order that implies an ordering the floor does not support. This includes a well-meaning
  "overall score" column combining ΔΔE with logP and SA.
- **A second copy of the config rules.** `gui/pipeline.py` ends its checks by calling
  `cli.validate_config` precisely so no drifting second validator exists. Any GUI-side rule that
  can disagree with the CLI is a defect.
- **Hidden method.** A number on screen whose tier, basis, prep and floor cannot be reached in one
  click. IQmol's virtue is that the method is visible.
- **A bench chemist who can spend compute or change the method.** They annotate and propose.
- **Infrastructure assumptions in the domain.** Any `import sqlite3`, `getpass`, `Path("~/...")` or
  Temporal reference inside the core is a layering violation that makes the deployed future a
  rewrite.

## Constraints

- Local single-user software, started on a workstation (`smeltery bench`). A deployed service is
  future work and must not require re-architecture.
- Onion architecture, dependency rule inward only. No local-store assumptions in the domain.
- Python 3.11-3.12, uv, existing extras discipline. ferric builds from source at a pinned commit.
- The existing toolkit-free core (`gui.results`, `gui.scenes`, `gui.pipeline`, `gui.catalog`,
  `decisions`, `funnel`, `study`) is reused unchanged. This design adds layers around it and does
  not fork it.
- A chemist's workstation may have no Temporal server, and this box demonstrated that a long-lived
  worker holds ~2-3x its predicted per-calculation memory. Local execution must not require Temporal
  and must not accumulate memory across molecules.

## Architecture

Four layers. The dependency rule points inward: an inner layer never imports an outer one.

```
                 surfaces
    ┌──────────────────────────────────────┐
    │ FastAPI app · CLI · report writer    │   thin; no rules
    ├──────────────────────────────────────┤
    │ application: use cases               │   orchestrates; speaks only to ports
    ├──────────────────────────────────────┤
    │ ports: RunStore, TaskQueue, ...      │   interfaces, no implementations
    ├──────────────────────────────────────┤
    │ core (EXISTS): funnel, tiers,        │   no I/O, no toolkit, no GUI
    │ record, decisions, study, gui.*      │
    └──────────────────────────────────────┘
         adapters implement ports, outside-in:
         SqliteRunStore · TemporalTaskQueue · SubprocessTaskQueue · LocalIdentity
```

### Core: unchanged

`funnel`, `tiers`, `record`, `decisions`, `study`, and the toolkit-free GUI modules. The floor and
the cut live here, which is why no surface can out-claim them. This design adds nothing to the core
except what section "Core additions" names.

### Ports

Interfaces the application layer talks to. Each is an abstract contract; no implementation detail
leaks through the signature. Defined in `smeltery/bench/ports/`.

| Port | Responsibility | Local adapter | Later |
|---|---|---|---|
| `RunStore` | find, read, index runs and their artefacts | `FilesystemRunStore` over an index in SQLite | Postgres + object store |
| `ArtifactStore` | bytes by content hash (poses, receptors, reports) | filesystem, sha256-verified | S3/GCS |
| `TaskQueue` | submit, list, status, cancel, logs | `SubprocessTaskQueue` (default), `TemporalTaskQueue` | Temporal on a cluster |
| `CommentStore` | threads anchored to a run, candidate or pose | SQLite | Postgres |
| `StructureSource` | fetch a structure by path, PDB id or UniProt accession | local file + existing providers | institutional store |
| `Identity` | who is acting now | `LocalIdentity` -> one trusted user | OIDC |
| `Clock` | now, in UTC | system clock | injected in tests |
| `ProjectStore` | campaigns, their runs, their notebook entries | SQLite | Postgres |

Two ports look pointless locally and are expensive to retrofit, so they exist from day one:

- **`Identity`.** `decisions.json` already carries `user` and `time`. If the app calls
  `Identity.current()` even when the answer is always `getpass.getuser()`, multi-user is an adapter.
  Sprinkling `getpass.getuser()` through view code makes it a migration. **No surface or use case
  may call `getpass` directly.**
- **`CommentStore`.** A single local user barely needs comments, but the commentable report is the
  whole bench-chemist story. The port exists now; the first adapter is SQLite on one machine.
  Multi-writer conflict semantics are deferred, explicitly (see "Deferred").

### Application: use cases

Each use case is a plain function or small class taking its ports as constructor arguments. No
framework types in signatures, so every one is unit-testable with fakes and the FastAPI layer holds
no logic.

```
OpenStructure(StructureSource, ArtifactStore)      -> prepared receptor + metadata
PlacePocketSite(...)                               -> a pipeline edit (see below)
EditPipeline(...)                                  -> Pipeline + list[Issue]
LaunchRun(TaskQueue, RunStore, ArtifactStore,
          Identity, Clock)                         -> task id
ListWork(TaskQueue)                                -> task rows
ReadRun(RunStore, ArtifactStore)                   -> Run + tables + scenes
RecordDecision(RunStore, Identity, Clock)          -> updated DecisionSet
ProposeCandidate(ProjectStore, Identity, Clock)    -> a draft, never a launch
CommentOnRun(CommentStore, Identity, Clock)        -> a thread entry
CompareArms(RunStore)                              -> per-cell comparison + caption
PublishReport(RunStore, ArtifactStore)             -> served view + frozen snapshot
```

### Surfaces

- **FastAPI + Mol\* / RDKit.js**, served locally, opened in the browser. One app, three areas
  (below). FastAPI because the use cases are plain and the deployed future needs a real HTTP layer
  with auth middleware; the existing stdlib dashboard is the right shape but too thin to grow into
  an ELN, and Marimo's session-per-process reactive model is wrong for a multi-user service.
- **CLI**, unchanged commands plus `smeltery bench`.
- **Report writer**, the existing `gui/report.py`, kept for the frozen snapshot.

**Marimo is kept, demoted.** `smeltery gui OUT` stays as a notebook attached to a run, for
computing something the app does not offer. It is not the product shell.

## The three areas

### 1. Bench — the computational chemist's home

Landing surface. Campaigns, recent runs, running work, and what needs attention.

- **Work panel.** Tasks from `TaskQueue`: name, status, elapsed, exit code, log tail, cancel. A
  task's status outlives its worker (verified: kill the worker, status, exit code, timings and log
  still read back). A task runs at most once; a task whose worker died is reported failed, never
  silently retried, and resubmitting is a decision.
- **Campaign panel.** `Study.dag()` drawn as the DAG it already returns (nothing draws it today),
  plus per-cell state.

  **Scope limit, deliberate.** `Study` is an in-memory Python object: `Study(name, steps,
  conditions)` where each `Step` is a Protocol with a `run()` callable. It is constructed **nowhere
  in `src/` or `scripts/`** -- only in `tests/test_study.py` -- and has no serialisation, registry
  or on-disk definition. `Cache` persists step *results* by digest, and a digest cannot be turned
  back into a DAG. So the panel reads studies **declared in Python and imported by name** (a module
  path in settings), and does not invent a study-definition file format. A persisted study
  definition is deferred; it is a core design question of its own, not a GUI feature.

  **`Study.plan()` needs a per-cell breakdown, which it does not have.** It returns study-level
  totals only (`{study, conditions, steps, cached, to_run}`), and `dag()` has no condition axis. The
  panel needs `(condition, step) -> cached | to_run`, so a `plan_cells()` returning that grid is a
  **core addition** (listed below), written beside `plan()` and sharing its key derivation so the two
  cannot disagree. Until it exists the panel shows study-level totals and says so.
- **Attention panel.** Runs with no decision recorded; runs whose cut was UNRANKED; comments
  awaiting a reply; tasks that failed.

### 2. Run — the instrument panel

Where the method is visible and drivable. Tabs over one run or one draft.

**Structure.** Open a local `.pdb`, a PDB id, or a UniProt accession via `StructureSource`
(network only with `allow_network = true`, as today). Shows what the prep did and did not do: the
record already names prepared files by sha256, says EXPERIMENTAL or PREDICTED with licence,
attribution and mean pLDDT around the pocket, and lists what was dropped. All of that is surfaced,
because a silently dropped HETATM is how a result becomes wrong.

**Pipeline.** The palette is `catalog.hierarchy()` grouped by rung. Drag to order, click to add,
forms generated from typed `Param` knobs. `Pipeline.check()` runs on every edit and shows **all**
issues at once -- errors block the launch, warnings and notes are shown -- because a form wants all
of them, not the first.

Two properties are preserved deliberately:

- **Rigour does not order across quantities.** `m.comparable_rigour(other)` is False across
  quantities, and the UI must not draw a single ladder. Asking whether `field_dft_interaction` or
  `designability` is "more rigorous" is a category error, and flattening it is how "scores better"
  comes to mean "is better" -- which is how an unnormalised `dE_int` rewards a generator for adding
  greasy mass.
- **Unwired tiers are shown, not offered.** `surface_esp`, `qmmm`, `rescoring` appear in the palette
  with `wired=False` and the reason, so the hierarchy is complete and honest. `addable()` does not
  offer them and `check()` refuses them.

**Code is the source of truth.** The editor round-trips through `Pipeline.from_toml` /
`to_toml`. A TOML pane sits beside the visual editor, both views of one object, and the file is what
gets committed. A pipeline that checks clean is one the CLI accepts, because `check()`'s last step
is `cli.validate_config` on the compiled config.

**Launching materialises the config into OUT, and that is where relative paths resolve.** A launch
is `["smeltery", "run", "<OUT>/config.toml", "--out", "<OUT>"]`, so the config the run read is
archived beside the record it produced -- the config is an input to the run and belongs with it.
Because `path` knobs (`pocket.file`, `docking.receptor`) are relative to the config's directory,
writing the config somewhere else would silently re-resolve them, which is a provenance bug of
exactly the kind this design exists to prevent. `LaunchRun` therefore takes `ArtifactStore` and
**stages every referenced input into OUT by content hash before writing the config**, rewriting
those knobs to the staged paths. The user picks OUT; the default is a dated directory under the
campaign. A draft the user has also saved to the repo stays the editable source; the staged copy is
the immutable record.

**Clicking an atom places a pocket site, by editing the pipeline's knobs in place.** The viewer is a
typed input widget; the pipeline stays the single source of truth, so there is no second selection
state to drift. Clicking parent atom *i* then atom *j* calls
`Pipeline.set("pocket", site=[...])` with one `{q, atom, from_atom, distance}` entry appended.
`Pipeline.set` rejects unknown knob names and `catalog._sites_ok` validates the payload shape, so
the enforcement already exists. Today those indices are typed by hand; this is the single biggest
"drivable" failure and the clearest win.

**The pose the user clicks on, and why this needs care.** `[[pocket.site]]`'s `atom` and
`from_atom` are indices into **the parent's pose 0** (`cli.py:_place_sites` reads
`parent.poses[0].coords_ang`), and that pose is produced *during* the run. So on a fresh draft there
is nothing to click, and the obvious implementation has a circular dependency.

Resolved as follows, because the index space -- not the geometry -- is what the config stores:

- The index space is **the parent SMILES with explicit hydrogens**, `Chem.AddHs(Chem.MolFromSmiles(smiles))`
  order, which is also `Pose.symbols` order (`docs/gui-contract.md` section 1). It is determined by
  the SMILES alone and is **identical in every pose and every run of that parent**. This is what
  makes clicking safe: the click resolves to an index, and the index is stable even though the
  coordinates are not.
- At edit time the viewer shows a **provisional parent conformer** built in-process from the SMILES
  with the same ETKDG embedding the run uses, labelled *provisional geometry* in the UI. It exists
  to be clicked, never to be scored, and is not written to the record.
- **The atom-order guarantee is a test, not a hope.** A unit asserts that the provisional conformer's
  atom order equals the run's `Pose.symbols` order for the same parent. If that invariant ever
  breaks, charges would be placed on the wrong atoms silently -- the worst failure this design can
  have -- so it is an anchor test, in Tier 2.3 with the other honesty anchors.
- Distance and `q` come from the form, not the click. The click supplies only the two indices.

A site placed this way is therefore reproducible from the config alone, with no dependency on the
provisional geometry that helped a human choose it.

**Poses.** `scenes.pose_scene` as built: receptor cartoon, ball-and-stick within 5 Å, the candidate
coloured, the parent's same-index pose muted at 0.45 opacity, point charges as spheres coloured by
sign (positive blue, negative red). Pose *i* of an analogue is paired to pose *i* of the parent, and
the viewer says so. A clash gate refusal is shown as chemistry, not infrastructure.

**Results.** The existing forest plot (mean ± z·SEM, tie groups as alternating bands, a ±floor band
at group boundaries), the resolved matrix (`funnel.resolved` itself, never a new statistic), and
per-pose paired lines with the unpaired SEM beside them to show why pairing works. The floor and its
source are stated, not implied. Rankograms and SUCRA stay out: they need a noise model, and the cut
combines the floor by max rather than in quadrature, so any Monte Carlo over N(mean, SEM²+floor²)
would resolve pairs the cut refuses to resolve.

**Decisions.** The grid with keep/reject/hold, a closed reason set, and a required note for
`within_tie_choice` and `other`. Written through `DecisionSet.dump` to
`decisions-<digest12>.json`; `validate` is what refuses, and the GUI adds no rule of its own. The
acting user comes from `Identity.current()`.

### 3. Report — what the bench chemist sees

**Two artefacts, because they serve different masters.**

- **Served report (dynamic).** Read-mostly, commentable. Candidates with 2D structures, what won,
  the resolution verdict first and largest, clickable poses. A bench chemist can anchor a comment to
  a run, a candidate or a pose.
- **Frozen snapshot (inert).** The existing `gui/report.py` output: one self-contained HTML file,
  zero external references, archivable, hashed into the ELN entry. It cannot accept a comment, and
  that is what makes it a record.

The served report is generated from the same `gui.results` tables as the frozen one, so they cannot
disagree about numbers.

**The return path has two kinds of traffic, treated differently:**

| Traffic | Lands as | Flows to |
|---|---|---|
| Judgement on what exists ("not this one, nitro on an aniline") | a `Decision` with `user`, `reason`, `note` | `decisions-<digest>.json`, then the next run's exclusions |
| A request for what does not exist ("try the 3-F instead") | a **proposal** on a draft pipeline | the computational chemist's review queue |

A proposal never launches compute. It becomes a candidate in a draft the computational chemist
reviews, because an SCF campaign is not something a comment should be able to start.

**What a bench chemist cannot do:** change the method, change a tier or basis, alter the floor,
spend compute. This keeps provenance intact -- the record says who decided what, and no number
changes because someone clicked something.

## Single source of truth

The owner's rule, stated once so a builder can apply it everywhere: **when two places could hold
the same fact, one of them is wrong.** Every seam in this design resolves the same way.

| Fact | The one owner | Everything else |
|---|---|---|
| What a run was | `run-<digest12>.json` | all surfaces read it; none amends it |
| What the pipeline is | the `Pipeline` object, persisted as TOML in git | visual editor, TOML pane, pocket-site clicks are views and inputs |
| Whether two candidates are distinguishable | `funnel.resolved` | no surface computes its own predicate |
| The floor and its source | the tier / config, via `inputs.floor` | displayed, never recomputed |
| Human judgement | `decisions-<digest12>.json` | the grid and the report write through `DecisionSet` |
| Config legality | `cli.validate_config` | `Pipeline.check()` ends by calling it |
| A task's state | the `TaskQueue` adapter's backing store | panels render it; no shadow status table |
| Who is acting | `Identity.current()` | no `getpass` call anywhere outside its adapter |
| Bytes of an artefact | the sha256 in the record | `ArtifactStore` verifies before serving |
| The numbers in a report | `gui.results` tables | served and frozen reports both render from them |

Three consequences worth making explicit, because they are where a builder is tempted:

- **The pocket-site click edits the pipeline in place.** There is no "viewer selection" state. The
  viewer is a typed input widget over `Pipeline.set("pocket", site=[...])`.
- **The served and frozen reports share a data layer, not templates.** They must agree on numbers;
  only one may hold external references.
- **A derived value is cached, never stored.** An index in SQLite exists to find runs fast; if it
  disagrees with the records on disk, the records win and the index is rebuilt. Nothing is read from
  the index that cannot be recomputed from the files.

## Units of work

Borrowed from the Ralph Wiggum technique, whose transferable idea is not the bash loop but what the
loop requires: **if progress lives in files rather than in an agent's memory, work can stop and
resume anywhere.** The same property makes work decomposable for humans, survivable across context
loss, and parallelisable.

Each unit below obeys four rules:

1. **State in files.** Every unit leaves its result on disk -- code, a test, a port definition, a
   fixture. No unit's output exists only as something an agent remembers.
2. **One verifiable promise.** A single command decides whether it is done. Not "looks right."
3. **Independently landable.** Each merges on its own without breaking the suite. No unit requires
   a later one to be correct.
4. **Reversible.** Each is one commit that can be reverted without unpicking another.

Ordered by dependency. Units within a tier are parallelisable.

### Tier 0 — the seams (no UI)

| # | Unit | Done when |
|---|---|---|
| 0.1 | Port interfaces in `smeltery/bench/ports/`, no implementations | every port imports; `pytest` collects the empty contract suites |
| 0.2 | Contract test suite per port, parameterised over adapters | suites run and fail with no adapter registered |
| 0.3 | `LocalIdentity`, `Clock` | contract suites pass; `grep -rn getpass src/smeltery/bench` finds only the adapter |
| 0.4 | `FilesystemRunStore` + `ArtifactStore` over the existing record format | contract suite passes against a `gui_fixture` run dir |
| 0.5 | `SubprocessTaskQueue`, one unit of work per process | contract suite passes (one process per TASK; flat RSS is 0.6's promise, not this one) |
| 0.6 | Per-candidate measure mode + combine step in the CLI | a 10-molecule run shows flat RSS, not climbing; the combined record is byte-identical to the one-process run |
| 0.7 | `TemporalTaskQueue` wrapping existing `smeltery.tasks` | same contract suite passes |

Tier 0 ships no user-visible change and is the whole architecture. If it is right, the rest is
mechanical; if it is wrong, nothing above it can be fixed cheaply.

0.6 is the only Tier 0 unit that touches the existing CLI rather than adding beside it, and the
`--out` record it produces must be byte-identical to the single-process one -- that equality is its
test. It is sequenced here, not later, because the memory ceiling it removes is what made a
12-hour ΔΔE run salvage 19 of 87 molecules.

### Tier 1 — use cases (no UI)

| # | Unit | Done when |
|---|---|---|
| 1.1 | `ReadRun`, `ListWork` | unit tests with fake ports |
| 1.2 | `EditPipeline` + `PlacePocketSite` over `Pipeline.set` | a click-pair produces the TOML a hand-written config would; round-trip test |
| 1.3 | `LaunchRun`, staging inputs into OUT by hash | fake queue receives argv; the staged config's `path` knobs resolve inside OUT; nothing executes in the test |
| 1.4 | `RecordDecision` through `DecisionSet` | `within_tie_choice` without a note is refused by `validate`, not by new code |
| 1.5 | `CompareArms` reusing `gui.compare` | the pooled-vs-per-cell regression case stays a test |

### Tier 2 — shell and the honesty anchors

| # | Unit | Done when |
|---|---|---|
| 2.1 | FastAPI app skeleton, DI wiring, `smeltery bench` | `TestClient` 200s on `/healthz`; opens a browser |
| 2.2 | Independent-failure test | task backend down: runs 200, tasks 503 with a reason |
| 2.3 | **Honesty anchors**, before any view is pretty | each mutation fails a test: UNRANKED rendered as a winner; INCOMPLETE rendered as a blank verdict; floor band dropped; a proposal launching compute; a surface resolving a pair `funnel.resolved` refuses; the provisional conformer's atom order diverging from the run's |

2.3 comes before the views deliberately. The anchors are the product's integrity, and they are
cheapest to write while the views are still ugly.

### Tier 3 — the three areas (parallelisable)

| # | Unit | Done when |
|---|---|---|
| 3.1 | Bench: work panel | a real task's lifecycle renders; cancel works |
| 3.2 | Bench: campaign panel drawing `Study.dag()` + `plan_cells()` | a study imported by module path renders with cached/to-run per cell |
| 3.3 | Run: structure tab, open local PDB / PDB id | prep provenance shown: sha256, EXPERIMENTAL/PREDICTED, what was dropped |
| 3.4 | Run: pipeline editor over `Pipeline` | all `check()` issues shown at once; TOML pane round-trips |
| 3.5 | Run: click-to-place pocket site over a provisional conformer | clicking two atoms writes `[[pocket.site]]`; no second state exists; atom-order anchor passes |
| 3.6 | Run: pose viewer via `scenes.pose_scene` | parent muted, charges by sign, pose pairing stated |
| 3.7 | Run: results (forest, matrix, per-pose) | floor band drawn; UNRANKED prominent; INCOMPLETE rendered as itself |
| 3.8 | Run: decisions grid | writes `decisions-<digest12>.json`; user from `Identity` |
| 3.9 | Report: served, commentable | a comment anchors to run/candidate/pose |
| 3.10 | Report: frozen snapshot | existing `gui/report.py`; zero external references asserted |
| 3.11 | `Proposal` + review queue | a proposal cannot launch compute (asserted) |

### Tier 4 — the ELN

| # | Unit | Done when |
|---|---|---|
| 4.1 | `ProjectStore` + campaign/entry model | contract suite passes |
| 4.2 | Append-only notebook entries referencing artefacts by sha256 | an entry cannot be edited, only superseded |

**Completion promise.** A unit is done when its one command passes, `ruff check` and `ruff format
--check` are clean, and the full suite is no worse than its baseline. Not when it looks finished.

## Maintainability and the build

The repo's existing habits are good -- conservative ruff rules, a docs contract per tier, anchors
that must do nothing, mutation-tested checks. Three gaps to close while adding ~4000 lines:

**A type checker, which this repo does not have: adopt `ty`.** `pyproject.toml` configures ruff
(`E,F,I,B`) and pytest, and no static type checking at all. Adding ~4000 lines of
ports-and-adapters across layer boundaries without one would be a mistake: the whole value of a
port is that its signature is a contract, and an unchecked contract is a comment.

**`ty`** (Astral, same toolchain as ruff and uv) is the choice: one vendor for lint, format,
resolve and typecheck, no second config language, and it runs via `uvx ty check` with nothing added
to the runtime environment. Pydantic v2 (already present at 2.14.1) validates at the HTTP edge;
`ty` gives static guarantees inside.

**It earns its place already.** Run against the four modules this design builds on, `ty` 0.0.96
reported 11 diagnostics and two were real bugs in code written this week:

- `TemporalTaskManager.list_async` was annotated `-> list[Task]`, but the class defines a `list`
  method that shadows the builtin inside the class body. It works at runtime only because PEP 563
  defers annotations; the annotation does not mean what it says. Fixed by binding `_TaskList =
  list["Task"]` at module scope, where `list` is still the builtin.
- The `temporalio` import guard assigned `run_shell_activity = None` over a function, which `ty`
  reads as an incompatible assignment. The shadowing is deliberate, so it is now annotated as such.

Both are fixed; `src/smeltery/tasks.py` is down to one diagnostic, inside temporalio's own
`execute_activity` overloads rather than in smeltery.

**Rollout, shaped by `ty` being pre-1.0 (0.0.96).** Do not gate CI on a tool at this version across
the whole repo.

1. `[tool.ty]` in `pyproject.toml` with `src/smeltery/bench/` included; a CI job runs
   `uvx ty check` **on that path only** and is required.
2. The rest of the repo is checked in a **non-blocking** advisory job, so drift is visible without
   a tool upgrade being able to break `main`.
3. Pin the version (`uvx ty@0.0.96`) and bump deliberately. Widen the required path when `ty`
   reaches 1.0 or when the advisory job is near-clean, whichever comes first.

Every port signature is fully annotated: that is the layer's whole point, and it is the part a
checker can actually enforce.

**Dependency discipline.** New packages go in a `bench` extra, following the existing pattern where
each extra's comment names who imports it. FastAPI, uvicorn and Jinja2 are the server; pydantic is
already present.

**The served app works offline; the browser assets are vendored, not CDN-loaded.** This is local
software on a workstation, and a chemist's laptop on a plane or inside a restricted network is a
normal case, not an edge one. So Mol\*, RDKit.js and the fonts are all served by the local app from
the installed package, at pinned versions. Three consequences, stated because they cost something:
it adds a vendoring step to the build, it makes the wheel larger, and it is the reason the frontend
open call (plain ES modules vs a bundler) should lean plain -- vendored ES modules need no build
step at all. The existing frozen report keeps inlining its assets for zero external references;
that constraint does not move, and vendoring makes the two consistent rather than contradictory.

**Honest doc surface.** Every tier has a page under `docs/tiers/`; a new surface needs the same.
`docs/bench.md` states what Bench shows, what it refuses to show, and the ports it talks to, and
`docs/gui-contract.md` gains a section for the served-report and comment shapes so the data contract
stays in one place.

### Visual design

A pharma computational chemist's instrument, so the design language is the **laboratory
instrument**, not the SaaS dashboard: dense, quiet, legible at a glance, with the data the only
thing that is allowed to be loud.

**Palette.** Light ground, near-neutral, slightly cool (`#F7F8F8`) with true-black text
(`#15181A`); panel surfaces a half-step lighter than the ground rather than white cards with
shadows. Structure comes from hairlines (`#D8DCDE`), not elevation. The accent is a single
instrument blue (`#1B5E8C`) used only for interactive affordances. Three data colours earn their
place because they already mean something in this codebase and must not be reused decoratively:
charge-positive blue `#2166ac`, charge-negative red `#b2182b` (the existing electrostatics
convention in `scenes.py`), and candidate green `#33a02c`. Dark mode mirrors it with the same
hairline logic.

**One loud thing: the resolution verdict.** Spend the boldness there. A run that did not resolve
says so in type large enough to read across a desk, with the floor value beside it. A run that
resolved shows the winner at the same size. Everything else -- panels, tables, forms -- stays
deliberately quiet. This is the one piece of communication the whole design exists to get right.

**Three verdict states, not two.** `gui.results.load_run` raises unless `paired_ddE`, `unpaired_sem`,
`cut` and `funnel` are all present, so a run whose task died mid-SCF has no verdict at all -- and
the Attention panel lists failed tasks as first-class. The vocabulary is therefore:

| State | When | Rendered as |
|---|---|---|
| RESOLVED | a cut with survivors | the winner, large |
| UNRANKED | a cut that could not separate at the boundary | "does not resolve", equally large, with the floor |
| INCOMPLETE | no cut in the record: failed, killed, or still running | what stages did finish, and why it stopped |

INCOMPLETE is a first-class state with its own rendering, never a blank where a verdict belongs.
A blank is how a reader concludes "no difference" from "no data". The Run area reads such a record
through a guard in the shape of the existing `has_pose_output` / `pose_output` pair -- a partial
record shows what it has and says plainly what it lacks -- rather than by relaxing `load_run`'s
checks, which exist to stop a surface inventing results. Since Tier 2.3's anchors come before the
views, this vocabulary is settled first, by design.

**Typography.** One text family, a humanist sans with real tabular figures (Source Sans 3 or IBM
Plex Sans, both open-licensed and self-hosted, no CDN font dependency); a monospace family
(IBM Plex Mono) reserved for things that are literally code or hashes -- TOML, SMILES, digests,
log output. Monospace is not used for small labels as decoration. Tabular figures are
non-negotiable: columns of ΔΔE values that do not align are harder to read, and this UI is mostly
columns of numbers.

**Layout.** A fixed left rail for the three areas, a persistent context bar naming the run and its
digest (provenance is always on screen, never a click away), and a single scrolling work column
with a right inspector that appears only when something is selected. Tables are left-aligned for
text and right-aligned for numbers. No cards for things that are not independent objects.

**Motion.** Essentially none. Transitions only where they show what changed: an inspector opening,
a task row changing state, a check result appearing. No entrance animations, no hover lifts.
`prefers-reduced-motion` respected.

**Quality floor**, unannounced: keyboard focus visible on every control, the pipeline editor fully
operable without a mouse, colour never the only carrier of meaning (tie groups get bands *and*
labels; charge sign gets colour *and* a glyph), contrast at WCAG AA, and the 3D viewer never the
only way to learn something.

**Copy.** Plain and active. A button says what happens (`Run pipeline`, not `Submit`), and the
toast uses the same word (`Pipeline running`). Errors say what happened and what to do, in the
interface's voice, without apologising. An empty panel invites the next action rather than
explaining itself.

## Core additions

Small, and inside the existing idiom:

1. **`ProjectStore` and a notebook entry.** The ELN unit: a campaign holds runs, decisions,
   comments and dated entries. An entry is append-only and references artefacts by sha256. This is
   the one genuinely new domain concept.
2. **A `Proposal`**, parallel to `Decision`: candidate SMILES, who proposed it, why, and which run
   prompted it. Validated like `Decision` is, with a closed reason set.
3. **`Study.dag()` consumers.** The function exists and returns plain rows; nothing draws it.
4. **`Study.plan_cells()`**, returning `(condition, step) -> cached | to_run`. `plan()` gives
   study-level totals only, which the campaign panel cannot render per cell. Shares `plan()`'s key
   derivation so the two cannot disagree.
5. **A per-candidate measure mode for `smeltery run`, plus a combine step.** Required for
   per-molecule memory isolation (see Execution). Fan out the measurement, reduce in one process,
   because the cut needs every candidate together.
6. **A partial-record guard for `gui.results`**, in the shape of the existing `has_pose_output` /
   `pose_output` pair, so an INCOMPLETE run renders what it has. `load_run`'s checks are not
   relaxed.
7. **A provisional-conformer helper** for pre-run atom picking, with the anchor test that its atom
   order matches the run's `Pose.symbols` order.

Items 4-7 were added after a builder review found that 1-3 were not enough to build the panels this
design describes. Each is testable without a toolkit, matching `decisions.py`. **Nothing else.**

## Execution: local first, Temporal when it earns it

`TaskQueue` has two adapters:

- **`SubprocessTaskQueue` (local default).** One subprocess per unit of work. Requires no server.

**Per-molecule isolation is a CLI capability, not something an adapter can wrap.** `smeltery run`
takes one config and executes every stage for every candidate in one process (`--out` and `--plan`
are its only options), so wrapping `subprocess` around it gives one process for the whole campaign
and RSS still climbs. The memory evidence is specific -- 8.4-13.1 GB resident against a 5.6 GB
prediction, 32 GB of swap, 67 heartbeat timeouts from one memory problem -- and ~5 s of process
startup against ~120 s of SCF is a cheap fix, but only if the work is actually split.

Splitting it requires, as a **core addition**: a per-candidate invocation mode for the measure
stage, and a combine step. The cut cannot be split -- it needs every candidate's per-pose values
together -- so the shape is fan-out on the measurement, then one in-process reduction. That is CLI
and core work with its own tests, sequenced in Tier 0 before any adapter claims flat RSS.

Until it lands, `SubprocessTaskQueue` is honestly one-process-per-task, and unit 0.5's promise is
scoped to that. **Do not state a flat-RSS guarantee an adapter cannot keep.**
- **`TemporalTaskQueue`.** The existing `smeltery.tasks`, for long campaigns and work that must
  survive interruption.

**A budget is not a ceiling.** `memory_budget_gb` sizes predicted arrays (AO cache and RI tensor);
it does not cap a process, and `require_free_gb` samples memory once before a molecule and cannot
see growth across hundreds. The GUI must display it as "predicted arrays," never as a limit, and
must not imply it bounds a worker.

## Testing

Mirrors the layering, and follows the repo's existing habits (mutation-tested checks, anchors that
must do nothing).

- **Core:** unchanged, 1649 tests.
- **Ports:** one contract test suite per port, run against every adapter. A new adapter is correct
  when it passes the suite.
- **Use cases:** unit tests with fake ports. No HTTP, no DB, no Temporal.
- **Surfaces:** FastAPI `TestClient` over fake ports. Independent failure is a test, not a hope:
  with the task backend down, the run list still answers 200 and the task calls answer 503 carrying
  the reason (this property is already tested for the dashboard).
- **Anchors**, in the spirit of `test_anchors.py`: a candidate paired with itself gives exactly 0.0;
  an UNRANKED cut renders as UNRANKED on every surface; the served and frozen reports agree
  numerically; no surface resolves a pair `funnel.resolved` refuses.
- **Mutation-test the honesty checks.** Make UNRANKED render as a winner, drop the floor band, let a
  proposal launch compute: each must fail a test.

## Deferred

Named so a builder does not infer them:

- Multi-writer comment conflicts. The port exists; last-write-wins locally, and concurrent editing
  is a service-era problem.
- Auth, sessions, RBAC. `Identity` has one local adapter.
- Assay data and measured affinity.
- Geometry editing in the viewer.
- Rankograms/SUCRA, until a noise model the cut also uses exists.
- Real-time collaborative presence.

## Open calls the builder may settle

Marked as the builder's to decide, with my lean:

- Frontend build: plain ES modules with import maps, or a bundler. Lean plain, matching the
  dependency-light report; revisit if the pipeline editor's state grows awkward.
- SQLite migrations: hand-written SQL or Alembic. Lean hand-written while there is one adapter.
- Which text/mono families, if Source Sans 3 / IBM Plex are unavailable or unwanted. The
  requirements are fixed, not the names: one humanist sans with **real tabular figures**,
  self-hosted rather than CDN-loaded, and a mono used only for code, hashes and log output.

Settled elsewhere and *not* open, despite looking like a choice: whether the served and frozen
reports share templates. They share the `gui.results` data layer and render separately, because
they must agree on numbers while only one may hold external references ("Single source of truth").
