"""Port: fetch a structure by path, PDB id or UniProt accession."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class StructureSource(Protocol):
    def fetch(self, ref: str, *, allow_network: bool = False) -> Path:
        """Resolve `ref` (local path, PDB id, or UniProt accession) to a local file path."""
        ...

    def fetch_result(self, ref: str, *, allow_network: bool | None = None) -> Any:
        """Like `fetch`, but returns the provider's `StructureResult` (text and provenance) instead of a path.
        None when no provider could answer; `PermissionError` when `ref` needs the network and it is off."""
        ...
