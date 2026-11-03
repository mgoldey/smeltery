# Ports and adapters

**Each port is a Protocol, and each adapter passes its port's contract suite.**

## Requirements

1. Ports live in `src/smeltery/bench/ports/` and contain Protocols only: no sqlite, no filesystem
   layout, no Temporal, no implementation.
2. Eight ports, with these local adapters (names as on disk):

   | Port | Local adapter |
   |---|---|
   | `RunStore` | `FilesystemRunStore` |
   | `ArtifactStore` | `FilesystemArtifactStore` |
   | `TaskQueue` | `SubprocessTaskQueue`, `TemporalTaskQueue` (see `task-queue.md`) |
   | `CommentStore` | `SqliteCommentStore` |
   | `ProjectStore` | `SqliteProjectStore` |
   | `StructureSource` | `LocalStructureSource` |
   | `Identity` | `LocalIdentity` |
   | `Clock` | `LocalClock` |

3. One contract suite per port in `tests/bench/contracts/`, parameterised over every registered
   adapter through `tests/bench/contracts/base.py:PortContract`. An adapter is correct exactly
   when it passes its suite. A future adapter (Postgres, S3, OIDC) is added by registering it, not
   by writing new tests.
4. Every adapter constructor and every port method is fully annotated. No `*args, **kwargs`
   constructors: the contract suite and `ty` must be able to read the signature.
5. `RunStore`: a malformed `run-*.json` becomes a row with a non-empty `error` and never hides the
   good runs. A missing root lists nothing. The SQLite index is a cache: delete it and listing is
   still correct, because the records on disk win.
6. `ArtifactStore`: bytes go in and come out by sha256. Bytes that no longer match their hash are
   refused with `ValueError`, never served.
7. `ProjectStore`: entries are append-only (see `notebook.md`). The contract asserts the specific
   exception raised by an attempted edit, never bare `Exception`.
8. `StructureSource`: a local path resolves. A PDB id or UniProt accession without
   `allow_network = true` is refused with a message containing "network".
9. `Identity.current()` is a non-empty string, stable within a process. `Clock.now()` is
   timezone-aware UTC.
10. Local stores live under `<root>/.smeltery/bench/` (the runs root the app serves), never in the
    current working directory.

## Not in scope
Multi-writer conflict handling, auth, and remote adapters are deferred; the ports exist so they are
adapter changes later.
