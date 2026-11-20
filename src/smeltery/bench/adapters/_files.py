"""Filesystem helpers shared by the adapters that write files."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: Every temp file `atomic_write` leaves while a write is in flight ends in this.
TMP_SUFFIX = ".tmp"


def tmp_pattern(prefix: str) -> str:
    """The glob that matches `atomic_write(..., prefix=prefix)`'s in-flight temp files (for copy ignore lists)."""
    return f"{prefix}*{TMP_SUFFIX}"


def atomic_write(path: Path, data: str | bytes, *, prefix: str) -> None:
    """Write `data` to `path` by writing a sibling `<prefix>*.tmp` and renaming it over `path`, so a reader sees the
    old file or the new one, never half of either. Text is written UTF-8 with no newline translation. The temp file
    is removed if anything fails."""
    path = Path(path)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=prefix, suffix=TMP_SUFFIX)
    try:
        if isinstance(data, str):
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
                f.write(data)
        else:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
