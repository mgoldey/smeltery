# Bench specs

What Bench must do, one topic per file. Each spec says what must be true and how it is checked,
never how to build it. The reasons behind these choices are in
`docs/superpowers/specs/2026-11-03-bench-gui-design.md`. **For implementation, this directory wins;**
if the two disagree, record it in `../NOTES.md`.

## Rules every spec inherits

1. **The display makes exactly the decisions `funnel.cut` made, and no more.** No surface ranks,
   scores or sorts in a way that implies an order the floor does not support.
2. **Single source of truth.** When two places could hold the same fact, one of them is wrong.

   | Fact | The one owner |
   |---|---|
   | what a run was | `OUT/run-<digest12>.json` |
   | what a pipeline is | the `smeltery.gui.pipeline.Pipeline` object, persisted as TOML |
   | whether two candidates are distinguishable | `smeltery.funnel.resolved` |
   | config legality | `smeltery.cli.validate_config` |
   | human judgement | `decisions-<digest12>.json` via `smeltery.decisions.DecisionSet` |
   | a task's state | the `TaskQueue` adapter's backing store |
   | who is acting | `Identity.current()` |
   | an artefact's bytes | its sha256 |
   | the numbers in any report | `smeltery.gui.results` tables |

3. **Method belongs to the computational chemist.** A bench chemist comments and proposes; they
   never change a tier, the floor or the config, and never cause compute to run.
4. **Every acceptance check names its data source.** A test that computes its expected value the
   same way the code under test does is circular and does not count. If a step says "read from X",
   the test reads X.
5. **One home per thing.** Before adding a module, search for an existing one. Two diverging copies
   of one use case are a defect even when both pass their tests.

## Index

| Spec | In one sentence |
|---|---|
| `ports.md` | Each port is a Protocol, and each adapter passes its port's contract suite. |
| `task-queue.md` | A task's reported status and exit code are always consistent with what the process did. |
| `verdict.md` | Every run record maps to exactly one of RESOLVED, UNRANKED, INCOMPLETE. |
| `click-to-place.md` | Clicking two atoms adds one `[[pocket.site]]` that places charges on the atoms the run will use. |
| `run-split.md` | One campaign runs as one process per candidate and produces the same record as one process. |
| `app-shell.md` | The web app is built from injected ports and degrades per surface, not as a whole. |
| `layering.md` | Dependencies point inward and the gates enforce it. |
| `frontend.md` | The served app works offline and looks like an instrument. |
| `work-panel.md` | The computational chemist sees and cancels running work. |
| `campaign-panel.md` | A study's DAG and per-cell cache state are visible. |
| `structure-tab.md` | Opening a structure shows what its preparation kept and dropped. |
| `pipeline-editor.md` | A pipeline is edited visually while its TOML stays the only state. |
| `pose-viewer.md` | A candidate's pose is shown in the site beside its paired parent pose. |
| `results-view.md` | The ΔΔE result and its resolution verdict are shown honestly. |
| `decisions-grid.md` | Keep/reject/hold decisions are recorded through `DecisionSet` alone. |
| `report.md` | A bench chemist reads a served report and comments on it; a frozen copy archives it. |
| `proposals.md` | A bench chemist proposes a candidate without causing any compute. |
| `notebook.md` | Notebook entries are append-only and reference artefacts by hash. |
