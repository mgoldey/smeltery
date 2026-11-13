"""The prebuilt pipelines shipped in the wheel (`smeltery.prebuilt`): a working config, its inputs and a finished run.

They are read through `importlib.resources`, so an installed package and a checkout behave the same. Starting from one
copies it into the workspace (see `pipelines.create_pipeline`); the packaged folder is never written to.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Literal, get_args

from fastapi import APIRouter
from pydantic import BaseModel

from smeltery.bench.verdict import verdict_of

from . import models as M

router = APIRouter(prefix="/api", tags=["prebuilt"])

#: Files of a prebuilt folder that are not pipeline inputs: the config (stored as config.toml), the blurb,
#: where it sits on the landing page, and the run.
NOT_INPUTS = {"pipeline.toml", "about.md", "gallery.toml", "run", "imported"}
RUN_DIR = "run"
IMPORTED_DIR = "imported"
#: What a --temporal run keeps under its OUT while it runs (temporal_run.WORK_DIR, temporal_run.MARKER): its scratch
#: and its live marker, not part of the example run. A copy carrying the marker would claim a workflow in the copy.
NOT_RUN_FILES = ("temporal", "running.json")


Tier = Literal["start_here", "teaches_unranked", "real_campaign", "variant"]

#: Each prebuilt says where it sits in its own `gallery.toml` (`rank`, `tier`, optional `variant_of`), so the one
#: example to start from comes first, then the ones that teach something else, then the campaigns — with the
#: variants folded under the card they vary. The list route returns that order, not alphabetical: `benzoic-pbe`
#: sorts above `benzoic-rhf`, and the landing page's first card must be the teaching example.
#:
#: The ranking lives beside each example rather than in a tuple here on purpose. A list in library code has to name
#: every campaign it ships, and library code must not name a campaign target (tests/test_migration_rules.py, issue
#: #8): the library would then be coupled to one campaign, and adding a campaign would mean editing it.
GALLERY_FILE = "gallery.toml"


@dataclass(frozen=True)
class Place:
    """Where one prebuilt sits on the landing page, as its own folder declares it."""

    rank: int
    tier: Tier
    variant_of: str | None


def _place(name: str) -> Place:
    f = root().joinpath(name, GALLERY_FILE)
    if not f.is_file():
        raise RuntimeError(f"{name} ships no {GALLERY_FILE}: an example nobody placed has no place on the page")
    data = tomllib.loads(f.read_text())
    rank, tier = data.get("rank"), data.get("tier")
    if not isinstance(rank, int) or isinstance(rank, bool):
        raise RuntimeError(f"{name}/{GALLERY_FILE}: rank must be a whole number, got {rank!r}")
    # Matched against the Literal's own members, so adding a tier to `Tier` is all it takes for one to be
    # accepted here. The walrus narrows it to that Literal for the type checker; `in get_args(...)` does not.
    allowed: tuple[Tier, ...] = get_args(Tier)
    if (found := next((t for t in allowed if t == tier), None)) is None:
        raise RuntimeError(f"{name}/{GALLERY_FILE}: tier must be one of {list(allowed)}, got {tier!r}")
    under = data.get("variant_of")
    if under is not None and not isinstance(under, str):
        raise RuntimeError(f"{name}/{GALLERY_FILE}: variant_of must be a name, got {under!r}")
    return Place(rank=rank, tier=found, variant_of=under)


def places() -> dict[str, Place]:
    return {n: _place(n) for n in names()}


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
    """`names()` in the curated order. A prebuilt that nobody placed is a mistake, not a tail entry: placing it is
    how its spot on the landing page gets decided, so `_place` refuses to guess."""
    placed = places()
    ranks: dict[int, str] = {}
    for n, p in sorted(placed.items()):
        if p.rank in ranks:
            raise RuntimeError(f"{n} and {ranks[p.rank]} both claim rank {p.rank}: the order would be arbitrary")
        ranks[p.rank] = n
    for n, p in placed.items():
        if (p.tier == "variant") != (p.variant_of is not None):
            raise RuntimeError(f"{n}: a variant names what it varies, and only a variant names one (tier {p.tier!r})")
        if p.variant_of is None:
            continue
        under = placed.get(p.variant_of)
        if under is None:
            raise RuntimeError(f"{n}: variant_of {p.variant_of!r} is not a shipped example, so nothing would show {n}")
        if under.tier == "variant":
            raise RuntimeError(f"{n}: variant_of {p.variant_of!r} is not a card of its own, so nothing would show {n}")
    return [n for _, n in sorted((p.rank, n) for n, p in placed.items())]


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
        place = _place(n)
        out.append(
            Prebuilt(
                name=n,
                about=lines[0] if lines else "",
                needs=lines[1] if len(lines) > 1 else "",
                teaches=lines[2] if len(lines) > 2 else "",
                tier=place.tier,
                variant_of=place.variant_of,
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
