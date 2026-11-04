"""Port: bytes by content hash."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ArtifactStore(Protocol):
    def put(self, data: bytes) -> str:
        """Store `data`; return its sha256 hex digest."""
        ...

    def get(self, digest: str) -> bytes:
        """Return bytes for `digest`, verifying the hash."""
        ...

    def exists(self, digest: str) -> bool: ...

    def verify(self, digest: str, data: bytes) -> bool: ...
