"""The prebuilt pipelines shipped in the wheel (`smeltery.prebuilt`): a working config, its inputs and a finished run.

They are read through `importlib.resources`, so an installed package and a checkout behave the same. Starting from one
copies it into the workspace (see `pipelines.create_pipeline`); the packaged folder is never written to.
"""

from __future__ import annotations

import json
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from smeltery.bench.verdict import verdict_of

from . import models as M

router = APIRouter(prefix="/api", tags=["prebuilt"])

#: Files of a prebuilt folder that are not pipeline inputs: the config (stored as config.toml), the blurb, the run.
NOT_INPUTS = {"pipeline.toml", "about.md", "run"}
RUN_DIR = "run"


class Prebuilt(BaseModel):
    name: str
    about: str  # what it shows
    needs: str  # what re-running it needs
    verdict: M.Verdict  # of the example run


def root() -> Traversable:
    return resources.files("smeltery.prebuilt")


def names() -> list[str]:
    return sorted(p.name for p in root().iterdir() if p.is_dir() and p.joinpath("pipeline.toml").is_file())


def folder(name: str) -> Traversable | None:
    return root().joinpath(name) if name in names() else None


def _walk(node: Traversable, prefix: str = "") -> list[tuple[str, Traversable]]:
    out: list[tuple[str, Traversable]] = []
    for child in sorted(node.iterdir(), key=lambda c: c.name):
        rel = f"{prefix}{child.name}"
        if child.is_dir():
            out += _walk(child, rel + "/")
        else:
            out.append((rel, child))
    return out


def config_text(name: str) -> str:
    """pipeline.toml byte for byte (decoded without newline translation)."""
    f = folder(name)
    assert f is not None
    return f.joinpath("pipeline.toml").read_bytes().decode("utf-8")


def copy_plan(name: str) -> dict[str, Traversable]:
    """{relative path inside the new pipeline folder: packaged file}: the inputs where pipeline.toml reads them, and
    the example run under `run/`."""
    f = folder(name)
    assert f is not None
    return {rel: node for rel, node in _walk(f) if rel.split("/")[0] not in NOT_INPUTS or rel.startswith(RUN_DIR + "/")}


def _example_verdict(f: Traversable) -> M.Verdict:
    run = f.joinpath(RUN_DIR)
    records = [c for c in run.iterdir() if c.name.startswith("run-") and c.name.endswith(".json")]
    v = verdict_of(json.loads(records[0].read_text())) if records else None
    if v is None:
        return M.Verdict(state="INCOMPLETE", winner=None, floor=None, missing=["run record"])
    return M.Verdict(state=v.state, winner=v.winner, floor=v.floor, missing=list(v.missing))


@router.get("/prebuilt", response_model=list[Prebuilt])
def list_prebuilt() -> list[Prebuilt]:
    out = []
    for n in names():
        f = root().joinpath(n)
        lines = [ln.strip() for ln in f.joinpath("about.md").read_text().strip().splitlines() if ln.strip()]
        out.append(
            Prebuilt(
                name=n,
                about=lines[0] if lines else "",
                needs=lines[1] if len(lines) > 1 else "",
                verdict=_example_verdict(f),
            )
        )
    return out


def materialise(files: dict[str, Traversable], into: Path) -> dict[str, Path]:
    """Real paths for the store to copy: packaged files may live in a zip, so each is extracted under `into`."""
    out: dict[str, Path] = {}
    for rel, node in files.items():
        dest = into / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(node.read_bytes())
        out[rel] = dest
    return out
