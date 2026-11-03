# App shell

**The web app is built from injected ports and degrades per surface, not as a whole.**

## Requirements

1. `smeltery.bench.app.create_app(*, run_store, task_queue, identity, clock, project_store=None,
   comment_store=None, artifact_store=None) -> FastAPI`. No module-level store. Tests pass fakes
   from `tests/bench/fakes.py`.
2. `smeltery bench [ROOT] [--port N] [--open]` starts it locally. Adapters are wired only here, in
   the composition root.
3. Use cases live in `src/smeltery/bench/use_cases/` and **only there**. No use case imports
   fastapi, starlette or uvicorn, and use cases take their ports as constructor arguments.
4. Independent failure: with the task queue raising, `/api/tasks` answers 503 with a JSON `error`
   naming the reason within 2 s, and `/api/runs` still answers 200. Run routes never touch the task
   queue.
5. `GET /healthz` 200; unknown paths 404; `GET /api/runs/{idx}/verdict` returns `verdict.md`'s
   `Verdict` as JSON.
6. The acting user always comes from `Identity.current()`. A `user` or `author` field in a request
   body is ignored.
