"""Bench: the FastAPI app that serves the API under /api and the built React app (ui/) at every other path.

`create_app` takes every port as a keyword argument, so tests pass fakes and a deployed service passes other adapters;
`build_app` is the one composition root that wires the local adapters. `smeltery bench ROOT` serves `build_app(ROOT)`.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .api import active_site, ligands, lineage, methods, notebook, pipelines, prebuilt, runs, work

#: Where `npm run build` (in ui/) writes the app. Build output: never edited or committed.
WEB_DIR = Path(__file__).resolve().parent / "web"
BUILD_HINT = "the Bench UI is not built: run `cd ui && npm ci && npm run build`"


@dataclass
class Settings:
    """What the app needs that is not a port: where it serves from, which studies it shows, the study cache."""

    root: Path = field(default_factory=lambda: Path(tempfile.mkdtemp()))
    studies: tuple[str, ...] = ()
    cache_dir: Path | None = None
    web_dir: Path = WEB_DIR

    def __post_init__(self) -> None:
        self.root = Path(self.root)
        if self.cache_dir is None:
            self.cache_dir = self.root / ".smeltery" / "study-cache"


def create_app(
    *,
    run_store: Any,
    task_queue: Any,
    identity: Any,
    clock: Any,
    project_store: Any = None,
    comment_store: Any = None,
    artifact_store: Any = None,
    structure_source: Any = None,
    pipeline_store: Any = None,
    method_defaults: Any = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings()
    local = settings.root / ".smeltery" / "bench"
    if comment_store is None:
        from .adapters.comment_store import SqliteCommentStore

        comment_store = SqliteCommentStore(local / "comments.sqlite")
    if project_store is None:
        from .adapters.project_store import SqliteProjectStore

        project_store = SqliteProjectStore(local / "projects.sqlite")
    if artifact_store is None:
        from .adapters.artifact_store import FilesystemArtifactStore

        artifact_store = FilesystemArtifactStore(local / "artifacts")
    if structure_source is None:
        from .adapters.structure_source import LocalStructureSource

        structure_source = LocalStructureSource(local / "structures")

    if pipeline_store is None:
        from .adapters.pipeline_store import FilesystemPipelineStore

        pipeline_store = FilesystemPipelineStore(settings.root)

    if method_defaults is None:
        from .adapters.method_defaults import FilesystemMethodDefaults

        method_defaults = FilesystemMethodDefaults(local / "methods.toml")

    app = FastAPI(title="smeltery bench", version="1")
    st = app.state
    st.run_store, st.task_queue, st.identity, st.clock = run_store, task_queue, identity, clock
    st.project_store, st.comment_store, st.artifact_store = project_store, comment_store, artifact_store
    st.structure_source, st.settings = structure_source, settings
    st.pipeline_store = pipeline_store
    st.method_defaults = method_defaults
    st.structures = {}  # sha256 -> PDB text of structures opened this session (a cache, rebuilt by reopening)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/whoami")
    def whoami(request: Request) -> dict[str, str]:
        return {"user": request.app.state.identity.current(), "root": str(request.app.state.settings.root)}

    app.include_router(runs.router)
    app.include_router(lineage.router)
    app.include_router(work.router)
    app.include_router(active_site.router)
    app.include_router(ligands.router)
    app.include_router(pipelines.router)
    app.include_router(prebuilt.router)
    app.include_router(methods.router)
    app.include_router(notebook.router)

    index = settings.web_dir / "index.html"
    if index.is_file():
        assets = settings.web_dir / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str) -> FileResponse:
            if path.startswith("api/") or path == "api":
                raise HTTPException(status_code=404, detail=f"no API route /{path}")
            f = settings.web_dir / path
            if path and f.is_file() and settings.web_dir in f.resolve().parents:
                return FileResponse(f)
            return FileResponse(index)

    return app


def build_app(root: str | Path, *, studies: tuple[str, ...] = (), web_dir: Path = WEB_DIR) -> FastAPI:
    """The composition root: local adapters, everything under ROOT/.smeltery/bench/."""
    from .adapters.identity import LocalIdentity
    from .adapters.run_store import FilesystemRunStore
    from .adapters.task_queue import SubprocessTaskQueue
    from .ports.clock import SystemClock

    root = Path(root).resolve()
    local = root / ".smeltery" / "bench"
    return create_app(
        run_store=FilesystemRunStore(root),
        task_queue=SubprocessTaskQueue(local / "tasks"),
        identity=LocalIdentity(),
        clock=SystemClock(),
        settings=Settings(root=root, studies=tuple(studies), web_dir=web_dir),
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smeltery bench", description="Serve Bench for the runs under ROOT.")
    ap.add_argument("root", nargs="?", default=".", help="directory holding run records (searched recursively)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--study", action="append", default=[], help="a study's Python module path (repeatable)")
    ap.add_argument("--open", action="store_true", help="open a browser")
    args = ap.parse_args(argv)
    if not (WEB_DIR / "index.html").is_file():
        print(BUILD_HINT, file=sys.stderr)
        return 2
    import uvicorn

    app = build_app(args.root, studies=tuple(args.study))
    # uvicorn runs at log_level="warning", so without this line a healthy server prints nothing and looks hung.
    url = f"http://{args.host}:{args.port}/"
    print(f"smeltery bench: serving {Path(args.root).resolve()} at {url} (Ctrl+C stops)", file=sys.stderr, flush=True)
    if args.open:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
