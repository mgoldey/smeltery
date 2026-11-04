"""Port: fetch a structure by path, PDB id or UniProt accession."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class StructureSource(Protocol):
    def fetch(self, ref: str, *, allow_network: bool = False) -> Path:
        """Resolve `ref` (local path, PDB id, or UniProt accession) to a local file path."""
        ...
