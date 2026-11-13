"""The prebuilt pipelines shipped in the wheel (`smeltery.prebuilt`): a working config, its inputs and a finished run.

They are read through `importlib.resources`, so an installed package and a checkout behave the same. Starting from one
copies it into the workspace (see `pipelines.create_pipeline`); the packaged folder is never written to.
"""

from __future__ import annotations

import json
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel

from smeltery.bench.verdict import verdict_of

from . import models as M

router = APIRouter(prefix="/api", tags=["prebuilt"])

#: Files of a prebuilt folder that are not pipeline inputs: the config (stored as config.toml), the blurb, the run.
NOT_INPUTS = {"pipeline.toml", "about.md", "run", "imported"}
RUN_DIR = "run"
IMPORTED_DIR = "imported"
#: What a --temporal run keeps under its OUT while it runs (temporal_run.WORK_DIR, temporal_run.MARKER): its scratch
#: and its live marker, not part of the example run. A copy carrying the marker would claim a workflow in the copy.
NOT_RUN_FILES = ("temporal", "running.json")


Tier = Literal["start_here", "teaches_unranked", "real_campaign", "variant"]

#: The one example to start from, then the ones that teach something else, then the campaigns — and under each, the
#: variants of it. The list route returns this order, not alphabetical: `benzoic-pbe` sorts above `benzoic-rhf`, and
#: the landing page's first card must be the teaching example. `variant_of` names the prebuilt a variant belongs
#: under, so the page can fold it there instead of showing it as a peer.
ORDER: tuple[str, ...] = ("benzoic-rhf", "benzoic-pbe", "docking", "structure", "danuglipron-halogen")
TIERS: dict[str, tuple[Tier, str | None]] = {
    "benzoic-rhf": ("start_here", None),
    "benzoic-pbe": ("variant", "benzoic-rhf"),
    "docking": ("teaches_unranked", None),
    "structure": ("variant", "docking"),
    "danuglipron-halogen": ("real_campaign", None),
}


class Prebuilt(BaseModel):
    name: str
    about: str  # what it shows
    needs: str  # what re-running it needs
    teaches: str  # the outcome of the example run and what reading it demonstrates
    tier: Tier  # where the landing page files it: one card per non-variant tier
    variant_of: str | None = None  # the prebuilt this is a variant of, folded under its card
    verdict: M.Verdict  # of the example run


def root() -> Traversable:
    return resources.files("smeltery.prebuilt")


def names() -> list[str]:
    return sorted(p.name for p in root().iterdir() if p.is_dir() and p.joinpath("pipeline.toml").is_file())


def ordered_names() -> list[str]:
    """`names()` in the curated order. A prebuilt that nobody ranked is a mistake, not a tail entry: ranking it is
    how its place on the landing page gets decided, so the guard below refuses to guess."""
    found, order, tiers = set(names()), set(ORDER), set(TIERS)
    if found != order or found != tiers:
        raise RuntimeError(
            "smeltery.bench.api.prebuilt.ORDER/TIERS do not match the shipped prebuilts: "
            f"unranked {sorted(found - order)}, untiered {sorted(found - tiers)}, "
            f"ranked but absent {sorted((order | tiers) - found)}. Give each one a place in both."
        )
    for n, (tier, under) in TIERS.items():
        if (tier == "variant") != (under is not None):
            raise RuntimeError(f"{n}: a variant names what it varies, and only a variant names one (tier {tier!r})")
        if under is not None and TIERS.get(under, ("variant", None))[0] == "variant":
            raise RuntimeError(f"{n}: variant_of {under!r} is not a card of its own, so nothing would show {n}")
    return list(ORDER)


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


def _copied(rel: str) -> bool:
    parts = rel.split("/")
    if parts[0] == RUN_DIR:
        return len(parts) > 1 and not any(p in NOT_RUN_FILES for p in parts[1:])
    return parts[0] not in NOT_INPUTS


def copy_plan(name: str) -> dict[str, Traversable]:
    """{relative path inside the new pipeline folder: packaged file}: the inputs where pipeline.toml reads them, and
    the example run under `run/` without a --temporal run's scratch (`temporal/`) or live marker (`running.json`)."""
    f = folder(name)
    assert f is not None
    return {rel: node for rel, node in _walk(f) if _copied(rel)}


def _example_verdict(f: Traversable) -> M.Verdict:
    records: list[Traversable] = []
    for sub in (RUN_DIR, IMPORTED_DIR):  # a folder with no run of its own shows its imported record's verdict
        d = f.joinpath(sub)
        if d.is_dir():
            records = [c for c in d.iterdir() if c.name.startswith("run-") and c.name.endswith(".json")]
            if records:
                break
    v = verdict_of(json.loads(records[0].read_text())) if records else None
    if v is None:
        return M.Verdict(state="INCOMPLETE", winner=None, floor=None, missing=["run record"])
    return M.Verdict(state=v.state, winner=v.winner, floor=v.floor, missing=list(v.missing))


@router.get("/prebuilt", response_model=list[Prebuilt])
def list_prebuilt() -> list[Prebuilt]:
    out = []
    for n in ordered_names():
        f = root().joinpath(n)
        lines = [ln.strip() for ln in f.joinpath("about.md").read_text().strip().splitlines() if ln.strip()]
        tier, variant_of = TIERS[n]
        out.append(
            Prebuilt(
                name=n,
                about=lines[0] if lines else "",
                needs=lines[1] if len(lines) > 1 else "",
                teaches=lines[2] if len(lines) > 2 else "",
                tier=tier,
                variant_of=variant_of,
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
