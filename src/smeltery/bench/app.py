"""FastAPI app for Bench."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import JSONResponse


def create_app(
    *,
    run_store,
    task_queue,
    identity,
    clock,
    project_store=None,
    comment_store=None,
    artifact_store=None,
):
    app = FastAPI(title="smeltery bench")
    app.state.run_store = run_store
    app.state.task_queue = task_queue
    app.state.artifact_store = artifact_store
    app.state.identity = identity
    app.state.clock = clock
    app.state.comment_store = comment_store
    app.state.project_store = project_store

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    @app.get("/api/runs")
    def list_runs():
        try:
            return app.state.run_store.list_runs()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e

    @app.get("/api/runs/{idx}/verdict")
    def run_verdict(idx: int):
        try:
            rows = app.state.run_store.list_runs()
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
        if idx < 0 or idx >= len(rows):
            raise HTTPException(status_code=404, detail="not found")
        import json

        try:
            raw = json.loads(Path(rows[idx]["path"]).read_text())
        except Exception as e:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=str(e)) from e
        from smeltery.bench.verdict import verdict_of

        v = verdict_of(raw)
        return {"state": v.state, "winner": v.winner, "floor": v.floor, "missing": list(v.missing)}

    @app.get("/api/tasks")
    def list_tasks():
        try:
            return [t.to_dict() for t in app.state.task_queue.list()]
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"reason": str(e), "error": str(e)})

    @app.post("/api/tasks")
    def create_task():
        return {"ok": True}

    @app.post("/api/tasks/cancel/{task_id}")
    def cancel_task(task_id: str):
        try:
            ok = app.state.task_queue.cancel(task_id)
            return {"cancelled": ok}
        except Exception as e:  # noqa: BLE001
            return JSONResponse(status_code=503, content={"reason": str(e), "error": str(e)})

    return app


def _compose(root):
    from pathlib import Path

    root = Path(root)
    from smeltery.bench.adapters.artifact_store import FilesystemArtifactStore
    from smeltery.bench.adapters.clock import SystemClock
    from smeltery.bench.adapters.comment_store import SqliteCommentStore
    from smeltery.bench.adapters.identity import LocalIdentity
    from smeltery.bench.adapters.project_store import SqliteProjectStore
    from smeltery.bench.adapters.run_store import FilesystemRunStore
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue

    return create_app(
        run_store=FilesystemRunStore(root),
        task_queue=SubprocessTaskQueue(root / ".bench_tasks"),
        identity=LocalIdentity(),
        clock=SystemClock(),
        artifact_store=FilesystemArtifactStore(root / ".artifacts"),
        comment_store=SqliteCommentStore(root / ".smeltery" / "bench" / "comments.sqlite"),
        project_store=SqliteProjectStore(root / ".smeltery" / "bench" / "projects.sqlite"),
    )


# Composition root aliases for compatibility with callers/tests.
compose = _compose
build_app = _compose
make_app = _compose
compose_app = _compose
create_app_from_root = _compose
app_from_root = _compose


def main(argv=None):
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="smeltery bench")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    app = _compose(args.root)
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()
    uvicorn.run(app, host=args.host, port=args.port)
    return 0
