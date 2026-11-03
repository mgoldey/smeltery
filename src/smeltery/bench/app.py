"""FastAPI app for Bench."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from starlette.responses import JSONResponse


def create_app(
    run_store=None,
    task_queue=None,
    artifact_store=None,
    identity=None,
    clock=None,
    comment_store=None,
    project_store=None,
    structure_source=None,
    root: str | Path | None = None,
):
    from smeltery.bench.adapters.artifact_store import FilesystemArtifactStore
    from smeltery.bench.adapters.comment_store import SqliteCommentStore
    from smeltery.bench.adapters.identity import LocalIdentity
    from smeltery.bench.adapters.project_store import SqliteProjectStore
    from smeltery.bench.adapters.run_store import FilesystemRunStore
    from smeltery.bench.adapters.structure_source import LocalStructureSource
    from smeltery.bench.adapters.task_queue import SubprocessTaskQueue
    from smeltery.bench.ports.clock import SystemClock
    from smeltery.bench.verdict import verdict_of

    root = Path(root) if root else Path.cwd()
    if run_store is None:
        run_store = FilesystemRunStore(root)
    if artifact_store is None:
        artifact_store = FilesystemArtifactStore(root / ".artifacts")
    if identity is None:
        identity = LocalIdentity()
    if clock is None:
        clock = SystemClock()
    if comment_store is None:
        comment_store = SqliteCommentStore(root / ".smeltery" / "bench" / "comments.sqlite")
    if project_store is None:
        project_store = SqliteProjectStore(root / ".smeltery" / "bench" / "projects.sqlite")
    if structure_source is None:
        structure_source = LocalStructureSource(root / ".structures", allow_network=False)
    if task_queue is None:
        task_queue = SubprocessTaskQueue(root / ".bench_tasks")

    app = FastAPI(title="smeltery bench")
    app.state.run_store = run_store
    app.state.task_queue = task_queue
    app.state.artifact_store = artifact_store
    app.state.identity = identity
    app.state.clock = clock
    app.state.comment_store = comment_store
    app.state.project_store = project_store
    app.state.structure_source = structure_source

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


def main(argv=None):
    import argparse

    import uvicorn

    ap = argparse.ArgumentParser(prog="smeltery bench")
    ap.add_argument("root", nargs="?", default=".")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args(argv)
    app = create_app(root=args.root)
    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(f"http://{args.host}:{args.port}/")).start()
    uvicorn.run(app, host=args.host, port=args.port)
