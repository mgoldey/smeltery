"""Seed a Bench workspace from the prebuilt pipelines, so a first run opens on finished work and runs no SCF.

Each prebuilt becomes `ROOT/pipelines/<name>/` exactly as copying it from "Examples you can copy" makes it: its
config, its inputs and its example run, as the prebuilt source stages them. A pipeline already in the store is left
alone, so seeding twice changes nothing (and never overwrites what the user edited).

The stores and the prebuilt source are passed in (`app.main` builds them), so this module touches no filesystem
adapter and no API module itself.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from smeltery.bench.ports.pipeline_store import PipelineStore
from smeltery.bench.ports.prebuilt_source import PrebuiltSource
from smeltery.bench.ports.run_store import RunStore

#: The prebuilt whose finished run the demo opens on: a fully RESOLVED one, fast to read.
LANDING = "benzoic-rhf"


def seed_demo(pipeline_store: PipelineStore, prebuilt: PrebuiltSource) -> list[str]:
    """Copy every prebuilt pipeline not yet in `pipeline_store` into it; return the names added (empty on a second
    call)."""
    have = set(pipeline_store.list())
    added: list[str] = []
    for name in prebuilt.names():
        if name in have:
            continue
        with tempfile.TemporaryDirectory(prefix="smeltery-demo-") as staging:
            pipeline_store.create(name, prebuilt.config_text(name), prebuilt.stage(name, Path(staging)))
        added.append(name)
    return added


def landing_path(run_store: RunStore) -> str:
    """The Bench URL path of the demo's finished run (the first run listed under `LANDING`, else the first run).

    Addressed by the run's stable id, never its position: this URL is printed once and then lives in a terminal
    scrollback, where a later run that sorts earlier would otherwise have moved it to a different run."""
    runs = run_store.list_runs()
    for r in runs:
        if f"/pipelines/{LANDING}/" in str(r["path"]).replace("\\", "/"):
            return f"/run/{r['id']}/results"
    return f"/run/{runs[0]['id']}/results" if runs else "/"
