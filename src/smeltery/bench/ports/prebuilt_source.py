"""Port: the prebuilt example pipelines a workspace can start from (each a config, its inputs and a finished run)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, runtime_checkable

Tier = Literal["start_here", "teaches_unranked", "real_campaign", "variant"]


@dataclass(frozen=True)
class Place:
    """Where one prebuilt sits on the landing page, as its own folder declares it."""

    rank: int
    tier: Tier
    variant_of: str | None


@runtime_checkable
class PrebuiltSource(Protocol):
    def names(self) -> list[str]:
        """Every prebuilt, sorted by name."""
        ...

    def ordered_names(self) -> list[str]:
        """`names()` in the landing page's order. RuntimeError when the placements are inconsistent."""
        ...

    def place(self, name: str) -> Place: ...

    def about(self, name: str) -> list[str]:
        """The non-blank lines of the prebuilt's blurb: what it shows, what re-running it needs, what it teaches."""
        ...

    def example_record(self, name: str) -> dict[str, Any] | None:
        """The example run's record (else an imported one's), or None when the prebuilt ships neither."""
        ...

    def config_text(self, name: str) -> str:
        """The prebuilt's config, byte for byte."""
        ...

    def stage(self, name: str, into: Path) -> dict[str, Path]:
        """Every file a copy of `name` needs, extracted under `into`: {relative path in the new folder: real file}."""
        ...
