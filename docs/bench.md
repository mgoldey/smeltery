# Bench

Bench is the web application over the smeltery funnel. It shows runs, tasks, and verdicts;
it does not schedule work, set priorities, or bound memory. `memory_budget_gb` sizes predicted
arrays and is not a process ceiling.

## Verdict states

- RESOLVED: a cut with survivors; `winner` is the top survivor.
- UNRANKED: the cut could not order the candidates (tie group spans the boundary).
- INCOMPLETE: a required result key is missing (e.g. a run whose task died mid-SCF). Never
  rendered as a blank or as a winner.

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
