"""How far along a plain `smeltery run` is, read from the checkpoint files it keeps in its OUT folder.

`smeltery.checkpoint` writes OUT/progress.json (the planned SCF count, before the first SCF) and appends one line to
OUT/calcs.partial.jsonl per finished SCF; both are deleted when the run record is written. So progress exists exactly
while a run is in flight: no progress.json (a tier that does not checkpoint, a preview, another kind of task, a run
that has finished) means None, never "0 of N".

The work panel polls every 1.5 s, and the checkpoint grows all run long, so `RunProgress` counts incrementally: it
remembers (size, newline count) per file and reads only the bytes appended since the last poll.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from smeltery.checkpoint import PARTIAL, PROGRESS

#: Bytes read per block when counting newlines.
_BLOCK = 1024 * 1024


def out_dir_of(command: list[str], cwd: str) -> Path | None:
    """The OUT folder of a `smeltery run ... --out OUT` command (relative to the task's cwd), or None for any other
    task. A `--plan` preview runs no SCF and has no checkpoint."""
    if "run" not in command or "--plan" in command:
        return None
    for i, arg in enumerate(command):
        if arg == "--out" and i + 1 < len(command):
            return Path(cwd or ".") / command[i + 1]
        if arg.startswith("--out="):
            return Path(cwd or ".") / arg.partition("=")[2]
    return None


class RunProgress:
    """Done and planned SCF counts of a run, cached per checkpoint file so a poll reads only what was appended."""

    def __init__(self) -> None:
        self._seen: dict[str, tuple[int, int, int]] = {}  # path -> (inode, bytes read, newlines in them)
        self._lock = threading.Lock()

    def lines(self, path: Path) -> int | None:
        """Complete lines in `path` (None if it does not exist yet). A half-written last line is not counted until
        its newline arrives. A shorter or replaced file is counted again from the start."""
        try:
            st = os.stat(path)
        except OSError:
            return None
        key = str(path)
        with self._lock:
            ino, pos, n = self._seen.get(key, (st.st_ino, 0, 0))
            if ino != st.st_ino or st.st_size < pos:
                pos, n = 0, 0
            try:
                with open(path, "rb") as f:
                    f.seek(pos)
                    while pos < st.st_size and (chunk := f.read(min(_BLOCK, st.st_size - pos))):
                        pos += len(chunk)
                        n += chunk.count(b"\n")
            except OSError:
                return None
            self._seen[key] = (st.st_ino, pos, n)
            if len(self._seen) > 256:  # finished runs' entries: keep the cache bounded
                self._seen.pop(next(iter(self._seen)))
            return n

    def of(self, out: Path | None) -> dict | None:
        """{done, total, reused} for the run writing into `out`, or None when it has no progress.json."""
        if out is None:
            return None
        try:
            doc = json.loads((out / PROGRESS).read_text())
            total = int(doc["total_scfs"])
            reused = int(doc.get("reused", 0))
            partial = out / str(doc.get("partial", PARTIAL))
        except (OSError, ValueError, KeyError, TypeError):
            return None
        done = self.lines(partial)
        if done is None:  # progress.json is written before the first SCF: nothing has finished yet
            done = 0
        # reused lines are already in the checkpoint, so `done` counts them; a remedy ladder can add lines beyond
        # the rung-0 plan, and the plan grows with them rather than showing "13 of 12"
        return {"done": done, "total": max(total, done), "reused": reused}
