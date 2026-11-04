# Bench

Bench is the web application over the smeltery funnel. It shows runs, tasks, and verdicts;
it does not schedule work, set priorities, or bound memory. `memory_budget_gb` sizes predicted
arrays and is not a process ceiling.

## Verdict states

- RESOLVED: a cut with survivors; `winner` is the top survivor.
- UNRANKED: the cut could not order the candidates (tie group spans the boundary).
- INCOMPLETE: a required result key is missing (e.g. a run whose task died mid-SCF). Never
  rendered as a blank or as a winner.

## Routes

- `/healthz` - health check.
- `/run` - run page (context bar, verdict).
- `/run/poses` - pose viewer.
- `/run/results` - results view (ddE, resolved, per-pose).
- `/report/{idx}` - served report.
- `/work` - work panel.
- `/api/runs`, `/api/runs/{idx}/verdict`, `/api/runs/{idx}/scene`, `/api/runs/{idx}/ddE`,
  `/api/runs/{idx}/resolved`, `/api/runs/{idx}/per_pose` - run data endpoints.
- `/api/runs/{idx}/decisions`, `/api/runs/{idx}/comments`, `/api/runs/{idx}/snapshot`,
  `/api/runs/{idx}/proposals` - decisions, comments, snapshot, proposals.
- `/api/tasks`, `/api/tasks/cancel/{task_id}`, `/api/tasks/{task_id}/cancel` - work panel.
- `/api/campaigns/{campaign_id}/entries` - notebook entries.
- `/api/studies/{module_path}/dag`, `/api/studies/{module_path}/cells` - campaign panel.
- `/api/structure` - structure tab.
- `/api/catalog`, `/api/pipeline/addable`, `/api/pipeline/check`, `/api/pipeline/site`,
  `/api/pipeline/toml` - pipeline editor.
- `/static` - vendored frontend assets.

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
