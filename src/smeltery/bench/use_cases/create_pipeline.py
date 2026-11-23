"""The rules for a new pipeline that do not depend on where it comes from: its name, and the blank pipeline.

A pipeline built from a run is `next_round.from_run` (a plain copy) or `next_round.plan` (the next round); one from a
prebuilt is the prebuilt source's copy. Nothing here touches a store or HTTP.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from smeltery.gui.pipeline import Pipeline
from smeltery.toml_write import dumps


class NoFreeName(ValueError):
    """Every `<base>-<n>` up to 999 is taken (a 409 over HTTP)."""


def unique_name(taken: Iterable[str], base: str) -> str:
    """`base`, or `base-2`, `base-3`, ... (cut so the whole stays within 64 characters), whichever is not taken."""
    taken = set(taken)
    if base not in taken:
        return base
    for i in range(2, 1000):
        cand = f"{base[: 64 - len(str(i)) - 1]}-{i}"
        if cand not in taken:
            return cand
    raise NoFreeName(f"no free name starting with {base}")


def blank_text(campaign: str, parent: str, candidates: dict[str, str], defaults: dict[str, dict[str, Any]]) -> str:
    """The config text of `Pipeline.minimal`, with the workspace `defaults` copied into each method it adds (a blank
    pipeline ADDS its methods; a copy of a run or a prebuilt gets none). Raises what `Pipeline` raises."""
    return dumps(Pipeline.minimal(campaign, parent, dict(candidates), defaults=defaults).to_config())
