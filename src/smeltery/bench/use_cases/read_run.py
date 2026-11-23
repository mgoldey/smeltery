"""What a run directory says beyond the numbers `smeltery.run_view` derives: its gates as one row per pose, the
SCF log lines of one pose, and the config it ran from. Plain dicts; a refusal is a ValueError saying why.

Every path here comes out of the run record, which is a file on disk: it is not trusted to stay inside the run
directory, and nothing here reads a file from elsewhere on the machine (`inside`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


class CalcsUnavailable(ValueError):
    """The run's SCF log cannot be read: it kept none, it names a file outside the run directory, or it is gone."""


class PoseOutOfRange(ValueError):
    """The pose asked for is not one the record kept for that candidate."""


def inside(base: Path, path: Path) -> bool:
    """`path` is strictly inside the directory `base`, both already resolved (so `..` and symlinks cannot leave it)."""
    return path != base and path.is_relative_to(base)


def gate_rows(record: dict) -> dict[str, Any]:
    """`results.gates` as one row per (candidate, pose): {ran, max_strain_kcal, rows, raw}. A row's `over_strain`
    is None without a strain limit; `checked` is False when neither gate judged the pose (not a pass); `ok` is
    False only when a gate failed it."""
    results = record.get("results", {})
    gates: dict[str, Any] = results.get("gates") or {}
    limit = (record.get("inputs", {}).get("gates") or {}).get("max_strain_kcal")
    pb: dict[str, Any] = gates.get("posebusters") or {}
    strain: dict[str, list[float]] = gates.get("strain_kcal") or {}
    order = list(results.get("candidate_order") or [*pb, *(c for c in strain if c not in pb)])
    rows = []
    for name in order:
        verdicts = (pb.get(name) or {}).get("poses")
        n = max(len(verdicts or []), len(strain.get(name, [])))
        for i in range(n):
            v = verdicts[i] if verdicts is not None and i < len(verdicts) else None
            s = float(strain[name][i]) if name in strain and i < len(strain[name]) else None
            over = None if limit is None or s is None else s > float(limit)
            passed = None if v is None else bool(v["passed"])
            rows.append(
                {
                    "candidate": name,
                    "pose": i,
                    "posebusters": passed,
                    "failed_checks": list(v["failed_checks"]) if v else [],
                    "strain_kcal": s,
                    "over_strain": over,
                    "checked": passed is not None or over is not None,
                    "ok": passed is not False and over is not True,
                }
            )
    return {
        "ran": sorted(gates),
        "max_strain_kcal": None if limit is None else float(limit),
        "rows": rows,
        "raw": gates,
    }


def original_pose(record: dict, candidate: str, pose: int) -> int:
    """The original pose index of record `pose` of `candidate`: `results.pose_index[candidate][pose]` after a pairwise
    drop, else `pose` itself. The calc log is written before any drop, so it is keyed by the original index."""
    index = ((record.get("results") or {}).get("pose_index") or {}).get(candidate)
    if index is None:
        return pose
    if not 0 <= pose < len(index):
        raise PoseOutOfRange(f"pose {pose} out of range: {candidate} kept {len(index)} poses")
    return int(index[pose])


def calc_lines(run_dir: Path, record: dict, candidate: str, pose: int, field_model: str | None) -> dict[str, Any]:
    """The lines of the file `results.calcs` names (calcs.jsonl, or calcs-<candidate>.jsonl for a partial run), read
    as written, of one candidate's pose under one field model: `field_model`, else the run's primary one
    (`results.calcs.primary`). A line naming no model (a run from before models were recorded) is the primary's, and a
    line whose model is None (a `--temporal` vacuum leg) is shared by all. `pose` is the record's place in the
    candidate's poses; it is mapped through `results.pose_index` to the log's original index.
    Returns {file, pose (the original index), lines}."""
    calcs = (record.get("results") or {}).get("calcs")
    if not calcs:
        raise CalcsUnavailable("this run kept no SCF log")
    pose = original_pose(record, candidate, pose)
    out_dir = Path(run_dir).resolve()
    path = (out_dir / str(calcs["file"])).resolve()
    if not inside(out_dir, path):
        raise CalcsUnavailable(f"the SCF log {calcs['file']!r} is outside the run directory")
    try:
        text = path.read_text()
    except OSError as e:
        raise CalcsUnavailable(f"cannot read the SCF log {path}: {e}") from e
    primary = calcs.get("primary")
    want = field_model if field_model is not None else primary
    lines = []
    for x in text.splitlines():
        if not x.strip():
            continue
        try:
            d = json.loads(x)
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("candidate") == candidate and d.get("pose") == pose:
            model = d.get("field_model", primary)
            if want is None or model is None or model == want:
                lines.append(d)
    return {"file": str(calcs["file"]), "pose": pose, "lines": lines}


def recorded_config(run) -> dict[str, Any]:
    """The config file `run` was executed from, as text, comments and all: {file, sha256, text, reason, changed}.

    The record names the file it read (`results.config`) and keeps its sha256, so what is returned is the input itself
    rather than a config rebuilt from the record: a rebuild drops the comments, and in this project the comments are
    the documentation. A run whose config was not kept beside it gets `text` None and a `reason` instead of a
    reconstruction, because a plausible-looking config that is not the one that ran is worse than none. A file that
    changed since the run read it is still shown -- it is what is on disk -- but never silently (`changed`)."""
    cfg = run.record.get("results", {}).get("config")
    if not isinstance(cfg, dict) or not cfg.get("file"):
        return {"file": None, "sha256": None, "text": None, "reason": "this run's record does not name a config file"}
    out = Path(run.out_dir).resolve()
    path = (out / cfg["file"]).resolve()
    if not inside(out, path):
        return {
            "file": cfg["file"],
            "sha256": cfg.get("sha256"),
            "text": None,
            "reason": f"the record names {cfg['file']}, which is outside the run directory",
        }
    if not path.is_file():
        return {
            "file": cfg["file"],
            "sha256": cfg.get("sha256"),
            "text": None,
            "reason": f"the record names {cfg['file']}, but it is no longer beside the run",
        }
    text = path.read_text()
    digest = hashlib.sha256(text.encode()).hexdigest()
    recorded = cfg.get("sha256")
    changed = bool(recorded) and recorded != digest
    return {
        "file": cfg["file"],
        "sha256": digest,
        "text": text,
        "reason": "this file has changed since the run read it" if changed else None,
        "changed": changed,
    }
