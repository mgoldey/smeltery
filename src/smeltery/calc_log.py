"""The per-SCF log: OUT/calcs.jsonl and the lines it holds.

One JSON object per line, appended and fsync'd as each SCF finishes. A `--temporal` run writes it from its activities
and resumes from it (`smeltery.temporal_run`); a plain run checkpoints into OUT/calcs.partial.jsonl the same way
(`smeltery.checkpoint`) and writes calcs.jsonl at the end (`smeltery.run_outputs.write_calc_log`). Every line names
its field model and its rung (`_tag`); a leg is identified by (candidate, pose, leg, field model) (`_ident`), plus its
rung.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

CALCS = "calcs.jsonl"

_LOG_LOCK = threading.Lock()


def calcs_path(out: str | Path, name: str = CALCS) -> Path:
    return Path(out) / name


def append_row(out: str | Path, row: dict, name: str = CALCS) -> None:
    """Append one line to OUT/<name> (calcs.jsonl), fsync'd. A torn last line (no newline) is closed first, so the new
    row is never glued onto it. A plain run's checkpoint (`smeltery.checkpoint`) appends to its own file this way."""
    line = json.dumps(row) + "\n"
    p = calcs_path(out, name)
    with _LOG_LOCK:
        fd = os.open(p, os.O_RDWR | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            size = os.fstat(fd).st_size
            if size and os.pread(fd, 1, size - 1) != b"\n":
                line = "\n" + line
            os.write(fd, line.encode())
            os.fsync(fd)
        finally:
            os.close(fd)


def read_rows(out: str | Path, digest: str, name: str = CALCS) -> tuple[list[dict], int]:
    """(the Temporal SCF lines of this input digest, how many unreadable lines were skipped). A torn line (a write
    cut short) is skipped and counted, never a crash. A line of another digest, or without one (a non-Temporal run's),
    is refused: resuming into a log of different inputs would mix two measurements. `name`: the log under OUT (a plain
    run's checkpoint is read back the same way)."""
    p = calcs_path(out, name)
    if not p.is_file():
        return [], 0
    rows, torn = [], 0
    for x in p.read_text().splitlines():
        if not x.strip():
            continue
        try:
            r = json.loads(x)
        except ValueError:
            torn += 1
            continue
        if not isinstance(r, dict):
            torn += 1
            continue
        rows.append(r)
    old = [r for r in rows if "leg" not in r]
    if old:
        raise ValueError(
            f"{p} is the log of a --temporal run from before per-leg scores (7bdaebc): {len(old)} of its outcome "
            "line(s) name no leg, so its scores cannot be read back leg by leg; use a fresh --out"
        )
    foreign = [r for r in rows if r.get("digest") != digest[:12]]
    if foreign:
        got = sorted({str(r.get("digest")) for r in foreign})
        raise ValueError(
            f"{p} holds {len(foreign)} SCF line(s) of another run (digest {got}, this run {digest[:12]}); "
            "use a fresh --out, or remove the log to recompute"
        )
    return rows, torn


def _ident(r: dict) -> tuple:
    return (r["candidate"], int(r["pose"]), r["leg"], r.get("field_model"))


def dedupe(rows: list[dict]) -> tuple[list[dict], int]:
    """SCF lines unique by (candidate, pose, leg, field model, rung), the first kept; outcome lines all kept."""
    seen, out, dup = set(), [], 0
    for r in rows:
        if "outcome" not in r:
            k = (*_ident(r), int(r["rung"]))
            if k in seen:
                dup += 1
                continue
            seen.add(k)
        out.append(r)
    return out, dup


def primary_model(field) -> str:
    """The name of the plan's own field model: the force field pdb2pqr charged it with, else "primary" (sites, a
    .pqr)."""
    prov = getattr(field, "provenance", None) or {}
    return str(prov.get("pdb2pqr_ff") or "primary")


def _tag(row: dict, model: str) -> dict:
    """An SCF line as calcs.jsonl holds it: every line names its field model and its rung (0 without a ladder)."""
    if "outcome" in row:  # a Temporal outcome line: already names its model
        return row
    return {**row, "field_model": row.get("field_model", model), "rung": row.get("rung", 0)}
