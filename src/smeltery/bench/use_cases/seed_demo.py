"""Seed a Bench workspace from the prebuilt pipelines, so a first run opens on finished work and runs no SCF.

Each prebuilt becomes `ROOT/pipelines/<name>/` exactly as "start from a working pipeline" makes it: its config, its
inputs and its example run, copied through the helpers in `api/prebuilt.py`. A pipeline already in ROOT is left alone,
so seeding twice changes nothing (and never overwrites what the user edited).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from smeltery.bench.adapters.pipeline_store import FilesystemPipelineStore

#: The prebuilt whose finished run the demo opens on: a fully RESOLVED one, fast to read.
LANDING = "benzoic-rhf"


def seed_demo(root: str | Path) -> list[str]:
    """Copy every prebuilt pipeline not yet in `root` into it; return the names added (empty on a second call)."""
    from smeltery.bench.api import prebuilt

    store = FilesystemPipelineStore(root)
    have = set(store.list())
    added: list[str] = []
    for name in prebuilt.names():
        if name in have:
            continue
        with tempfile.TemporaryDirectory(prefix="smeltery-demo-") as staging:
            files = prebuilt.materialise(prebuilt.copy_plan(name), Path(staging))
            store.create(name, prebuilt.config_text(name), files)
        added.append(name)
    return added


def landing_path(root: str | Path) -> str:
    """The Bench URL path of the demo's finished run (the first run listed under `LANDING`, else the first run)."""
    from smeltery.bench.adapters.run_store import FilesystemRunStore

    runs = FilesystemRunStore(root).list_runs()
    for i, r in enumerate(runs):
        if f"/pipelines/{LANDING}/" in str(r["path"]).replace("\\", "/"):
            return f"/run/{i}/results"
    return "/run/0/results" if runs else "/"
