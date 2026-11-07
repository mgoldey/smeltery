"""Filesystem artifact store, sha256-verified."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

_DIGEST = re.compile(r"[0-9a-f]{64}")


class FilesystemArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise ValueError(f"not a sha256 hex digest: {digest!r}")
        return self.root / digest[:2] / digest

    def path_for(self, digest: str) -> Path:
        """Where the artifact lives. Does not verify its content; only `get` re-hashes."""
        return self._path(digest)

    def put(self, data: bytes | bytearray | str | Path) -> str:
        if isinstance(data, (bytes, bytearray)):
            b = bytes(data)
        elif isinstance(data, (str, Path)):
            b = Path(data).read_bytes()
        else:
            raise TypeError(f"put expects bytes or a path, got {type(data).__name__}")
        h = hashlib.sha256(b).hexdigest()
        p = self._path(h)
        p.parent.mkdir(parents=True, exist_ok=True)
        if not p.exists():
            p.write_bytes(b)
        return h

    def get(self, digest: str) -> bytes:
        p = self._path(digest)
        if not p.exists():
            raise FileNotFoundError(f"artifact {digest} not found")
        data = p.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"artifact {digest} failed sha256 verification")
        return data

    def exists(self, digest: str) -> bool:
        return self._path(digest).exists()

    def verify(self, digest: str, data: bytes) -> bool:
        b = data if isinstance(data, (bytes, bytearray)) else Path(data).read_bytes()
        return hashlib.sha256(b).hexdigest() == digest
