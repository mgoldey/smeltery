# Layering and gates

**Dependencies point inward and the gates enforce it.**

## Requirements

1. No module directly under `src/smeltery/` imports `smeltery.bench`.
2. `sqlite3` is imported only in `src/smeltery/bench/adapters/`. `temporalio` is imported only in
   `src/smeltery/bench/adapters/` and `src/smeltery/tasks.py` (a core module that predates Bench and
   is the one named exception).
3. `getpass` appears only in `src/smeltery/bench/adapters/identity.py`.
4. `smeltery.decisions` imports nothing from `smeltery.project`: `smeltery run` reads decisions and
   must not depend on the notebook layer.
5. `tests/bench/test_layering.py` asserts 1-4 by reading source files.
6. Gates (exact commands in `AGENTS.md`): ruff check, ruff format, `ty`, the bench tests. CI runs
   `uvx ty@0.0.96 check` as a required job, plus a non-blocking repo-wide job.
7. `docs/bench.md` states only true things: it names `tasks.py` as the Temporal exception, and
   says a task runs at most once and a dead worker's task is failed, not retried.
8. `.gitignore` covers `.ralph/`, `.opencode/` and `.smeltery/`.
9. In `pyproject.toml`, each optional extra sits directly under its own comment naming who imports
   it. The `bench` extra is not wedged between another extra and that extra's comment.
