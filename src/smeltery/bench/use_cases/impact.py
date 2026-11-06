"""What an edit costs, without running it.

Poses decide every cost, and only three honest sources of them exist: a config with no `[docking]`/`[structure]`
places them with RDKit (`build_plan`, no SCF, cheap); a config whose sections up to the poses equal a recorded run's
reuses that run's poses (or a finished preview's); anything else cannot say. Docking, structure prep and SCFs are
never run here (spec, settled item 4): `poses_from == "none"` and every pose-dependent value is None.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
from rdkit import Chem

from smeltery import cli
from smeltery.gui.pipeline import Pipeline
from smeltery.gui.steps import steps
from smeltery.model import Candidate, Pose

CANNOT_SAY = "cannot say until poses exist"


@dataclass
class SourceRun:
    """A recorded run whose poses may be reused: its directory and its record."""

    out_dir: Path
    record: dict


@dataclass
class ImpactResult:
    seconds: dict
    memory: dict
    changes: list[str]
    new_digest: bool
    poses_from: str
    issues: list[dict] = field(default_factory=list)
    preview_task: str | None = None


def _pipeline(cfg: dict) -> Pipeline | None:
    try:
        return Pipeline.from_config(cfg)
    except (KeyError, ValueError, TypeError):
        return None


def _read_sdfs(directory: Path, entries: dict[str, dict], smiles: dict[str, str]) -> list[Candidate] | None:
    """Candidates with poses read from `<directory>/<entry file>`; None if any file or candidate is missing."""
    out: list[Candidate] = []
    for name, entry in entries.items():
        if name not in smiles:
            return None
        path = directory / entry["file"]
        if not path.is_file():
            return None
        cand = Candidate(name, smiles[name])
        for mol in Chem.SDMolSupplier(str(path), removeHs=False):
            if mol is None:
                return None
            xyz = np.array(mol.GetConformer().GetPositions(), dtype=float)
            cand.poses.append(Pose(tuple(a.GetSymbol() for a in mol.GetAtoms()), xyz))
        out.append(cand)
    return out or None


def _recorded_poses(src: SourceRun, key: str, base: Path, smiles: dict[str, str]) -> list[Candidate] | None:
    res = src.record.get("results", {})
    cfg_info = res.get("config")
    poses = res.get("poses")
    if not isinstance(cfg_info, dict) or not cfg_info.get("file") or not isinstance(poses, dict):
        return None
    cfg_file = src.out_dir / cfg_info["file"]
    try:
        recorded = tomllib.loads(cfg_file.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if cli.poses_key(recorded, cfg_file.parent) != key:
        return None
    return _read_sdfs(src.out_dir, poses, smiles)


def _preview_poses(preview_dir: Path, key: str, smiles: dict[str, str]) -> list[Candidate] | None:
    try:
        plan = json.loads((preview_dir / "plan.json").read_text())
    except (OSError, ValueError):
        return None
    if plan.get("poses_key") != key or not isinstance(plan.get("poses"), dict):
        return None
    return _read_sdfs(preview_dir, plan["poses"], smiles)


def impact(
    toml: str,
    baseline_toml: str,
    base_dir: Path,
    *,
    source: SourceRun | None = None,
    preview_dir: Path | None = None,
    preview_task: str | None = None,
) -> ImpactResult:
    """Seconds and memory the edited `toml` would cost, which steps differ from `baseline_toml`, and where the
    poses behind those numbers came from. TOML that does not parse is the caller's to refuse."""
    raw = tomllib.loads(toml)
    base_raw = tomllib.loads(baseline_toml)
    new, old = _pipeline(raw), _pipeline(base_raw)
    issues = [{"level": i.level, "stage": i.stage, "message": i.message} for i in new.check()] if new else []
    if new is None:
        issues.append({"level": "error", "stage": None, "message": "this TOML is not a pipeline config"})
    changes = [s.key for s in steps(new, old) if s.state == "changed"] if new and old else []
    result = ImpactResult(
        seconds={"value": None, "basis": CANNOT_SAY},
        memory=cli.memory_cannot_say(None, CANNOT_SAY),
        changes=changes,
        new_digest=raw != base_raw,
        poses_from="none",
        issues=issues,
        preview_task=preview_task,
    )
    tier = None
    try:
        cfg = cli.validate_config(dict(raw))
        cfg["_dir"] = str(base_dir)
        tier = cli.measurement_tier(cfg)
        cands: list[Candidate] | None = None
        if "docking" not in cfg and "structure" not in cfg:
            plan = cli.build_plan(cfg)  # RDKit placement and the gates; no SCF
            cands, poses_from = plan.candidates, "computed"
        else:
            smiles = dict(cfg["candidates"])
            key = cli.poses_key(raw, base_dir)
            poses_from = "source run"
            if source is not None:
                cands = _recorded_poses(source, key, base_dir, smiles)
            if cands is None and preview_dir is not None:
                cands, poses_from = _preview_poses(preview_dir, key, smiles), "computed"
        if cands is None:
            result.memory = cli.memory_cannot_say(tier, CANNOT_SAY)
            return result
        cost = tier.estimate_cost(cands)
        result.seconds = {"value": cost.get("predicted"), "basis": cost.get("basis", CANNOT_SAY)}
        result.memory = cli.memory_estimate(SimpleNamespace(tier=tier, candidates=cands))
        result.poses_from = poses_from
    except Exception as e:  # noqa: BLE001 - a live-typing endpoint answers "cannot say" with the reason, never a 500
        issues.append({"level": "error", "stage": None, "message": f"cannot estimate: {type(e).__name__}: {e}"})
        result.seconds = {"value": None, "basis": CANNOT_SAY}
        result.memory = cli.memory_cannot_say(tier, CANNOT_SAY)
        result.poses_from = "none"
    return result


def as_dict(r: ImpactResult) -> dict[str, Any]:
    return {
        k: getattr(r, k) for k in ("seconds", "memory", "changes", "new_digest", "poses_from", "issues", "preview_task")
    }
