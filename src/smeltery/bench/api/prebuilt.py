"""`GET /api/prebuilt`: the prebuilt pipelines shipped in the wheel, in the landing page's order.

Reading the packaged folders (their configs, inputs, example runs and gallery places) is the prebuilt source's job
(the `PrebuiltSource` port, `adapters/prebuilt_source.py` locally); this module only turns that into the route's
answer.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from pydantic import BaseModel

from smeltery.bench.ports.prebuilt_source import Tier
from smeltery.bench.verdict import verdict_of

from . import models as M
from .common import ports

router = APIRouter(prefix="/api", tags=["prebuilt"])


class Prebuilt(BaseModel):
    name: str
    about: str  # what it shows
    needs: str  # what re-running it needs
    teaches: str  # the outcome of the example run and what reading it demonstrates
    tier: Tier  # where the landing page files it: one card per non-variant tier
    variant_of: str | None = None  # the prebuilt this is a variant of, folded under its card
    verdict: M.Verdict  # of the example run


def _example_verdict(record: dict[str, Any] | None) -> M.Verdict:
    v = verdict_of(record) if record is not None else None
    if v is None:
        return M.Verdict(state="INCOMPLETE", winner=None, floor=None, missing=["run record"])
    return M.Verdict(state=v.state, winner=v.winner, floor=v.floor, missing=list(v.missing))


@router.get("/prebuilt", response_model=list[Prebuilt])
def list_prebuilt(request: Request) -> list[Prebuilt]:
    source = ports(request).prebuilt_source
    out = []
    for n in source.ordered_names():
        lines = source.about(n)
        place = source.place(n)
        out.append(
            Prebuilt(
                name=n,
                about=lines[0] if lines else "",
                needs=lines[1] if len(lines) > 1 else "",
                teaches=lines[2] if len(lines) > 2 else "",
                tier=place.tier,
                variant_of=place.variant_of,
                verdict=_example_verdict(source.example_record(n)),
            )
        )
    return out
