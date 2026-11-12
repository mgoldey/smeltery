"""Checkpointing a plain (in-process, non-Temporal) `smeltery run CONFIG --out OUT`.

A plain run writes calcs.jsonl once, at the end, so a crash, an OOM kill or a cancel hours in used to lose every SCF.
Now each SCF of the measurement tier is also appended to OUT/calcs.partial.jsonl the moment it finishes (fsync'd,
through `temporal_run.append_row`), stamped with the run's input digest and staged-config sha256 (12 hex each). A rerun
into the same OUT reads it back with the `--temporal` resume's own machinery (`temporal_run.read_rows`, `dedupe`, the
leg identity `_ident` plus the rung) and reuses every line whose (candidate, pose, leg, field model, rung) the tier asks
for, instead of recomputing that SCF.

* A line of another input digest or staged config is refused (exit 2): resuming would mix two measurements. `--force`
  discards the checkpoint and starts from scratch.
* A reused line is the very line the tier logged (only the two stamps are dropped), so the record, its numbers, its
  digest and calcs.jsonl are the same whether or not the run resumed; only the wall-clock stage times differ.
* OUT/progress.json, written at the start of the measurement, names the checkpoint file and the planned SCF count:
  `{"partial": "calcs.partial.jsonl", "total_scfs": N, "reused": k, "digest": ..., "config_sha256": ...}`. N is one
  SCF per leg (vacuum and field) per pose at rung 0; a remedy ladder can add lines. Done-so-far is the checkpoint's
  line count. Both files are removed once the run record is written: a finished OUT holds today's outputs only.
* Only the primary measurement is checkpointed; an analysis that re-measures under extra fields
  (`[charge_sensitivity]`) recomputes its SCFs on a resumed run.
"""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path

PARTIAL = "calcs.partial.jsonl"
PROGRESS = "progress.json"
#: The stamps a checkpoint line carries beyond the tier's own calc_log line.
STAMPS = ("digest", "config_sha256")


class CheckpointRefused(ValueError):
    """OUT holds a checkpoint of other inputs or another staged config."""


class ScfCheckpoint:
    """The SCF lines a plain run has finished, read back from OUT/calcs.partial.jsonl, and the appender for new ones.

    A tier calls `get(candidate, pose, leg, rung)` before each SCF (a line to reuse, or None) and `put(row)` after
    each one it ran."""

    def __init__(self, out: str | Path, digest: str, config_sha256: str, primary: str) -> None:
        from .temporal_run import _ident, dedupe, read_rows

        self.out = Path(out)
        self.digest = digest[:12]
        self.config_sha256 = config_sha256[:12]
        self.primary = primary
        try:
            raw, _ = read_rows(self.out, digest, PARTIAL)
        except ValueError as e:
            raise CheckpointRefused(f"{e}; or rerun with --force to discard it and start over") from e
        other = sorted({str(r.get("config_sha256")) for r in raw if r.get("config_sha256") != self.config_sha256})
        if other:
            raise CheckpointRefused(
                f"{self.out / PARTIAL} was written under another staged config (sha256 {other}, this run "
                f"{self.config_sha256}); use a fresh --out, or rerun with --force to discard it and start over"
            )
        rows, _ = dedupe([r for r in raw if "outcome" not in r])
        self._done = {(*_ident(r), int(r["rung"])): {k: v for k, v in r.items() if k not in STAMPS} for r in rows}

    @property
    def n_reusable(self) -> int:
        return len(self._done)

    def get(self, candidate: str, pose: int, leg: str, rung: int = 0) -> dict | None:
        """The finished line of this SCF (the tier's own line, field model and rung included), or None."""
        row = self._done.get((candidate, int(pose), leg, self.primary, int(rung)))
        return None if row is None else dict(row)

    def put(self, row: dict) -> None:
        """Append one finished SCF line (a tier's calc_log line), tagged with the primary field model and its rung."""
        from .cli import _tag
        from .temporal_run import append_row

        append_row(
            self.out, {**_tag(row, self.primary), "digest": self.digest, "config_sha256": self.config_sha256}, PARTIAL
        )

    def write_progress(self, total_scfs: int) -> None:
        """OUT/progress.json: where the checkpoint is and how many SCFs the run plans (written whole, then renamed)."""
        doc = {
            "partial": PARTIAL,
            "total_scfs": int(total_scfs),
            "reused": self.n_reusable,
            "digest": self.digest,
            "config_sha256": self.config_sha256,
        }
        tmp = self.out / (PROGRESS + ".tmp")
        tmp.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, self.out / PROGRESS)


def discard(out: str | Path) -> None:
    """Remove OUT's checkpoint and progress file (a finished run, or `--force`)."""
    for name in (PARTIAL, PROGRESS):
        with contextlib.suppress(FileNotFoundError):
            (Path(out) / name).unlink()


def planned_scfs(candidates) -> int:
    """The measurement's SCF count at rung 0: a vacuum and a field leg per pose."""
    return 2 * sum(len(c.poses) for c in candidates)
