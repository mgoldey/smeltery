"""Helpers for a GUI that builds `smeltery run` pipelines from the method hierarchy. No GUI toolkit is imported.

* `catalog`: every method (stage or tier) by rung, with typed knobs, artefacts in and out, and what each tier
  declares about itself (`catalog_json()` for a GUI not written in Python).
* `pipeline`: `Pipeline`, an ordered list of stages that `check()`s dataflow and knobs, and compiles to the config
  `smeltery run` reads (`to_config`, `to_toml`; `from_config`, `from_toml` for the way back).
* `results`: a finished run read back (`load_run` -> `Run`) as tables: `ddE_table`, `resolved_matrix`,
  `per_pose_table`, `funnel_rows`, and the sha256-checked `pose_sdf` / `receptor_pdb`.
* `scenes`: `pose_scene`, a pose with its parent, receptor and field charges as a MolViewSpec state.
* `compare`: `Comparison`, several runs grouped by a shared key (cell, target, setting) and differenced per key
  rather than pooled, with the systematic floor deciding what counts as resolved.
"""

from .catalog import RUNGS, Method, Param, catalog, catalog_json, floor_status, hierarchy, method
from .compare import Comparison, KeyRow
from .pipeline import EXECUTION_ORDER, Issue, Pipeline, PipelineError, Stage
from .results import (
    Run,
    ddE_table,
    funnel_rows,
    load_run,
    per_pose_table,
    pose_output,
    pose_sdf,
    receptor_pdb,
    resolved_matrix,
)
from .scenes import pose_scene

__all__ = [
    "EXECUTION_ORDER",
    "RUNGS",
    "Comparison",
    "Issue",
    "KeyRow",
    "Method",
    "Param",
    "Pipeline",
    "PipelineError",
    "Run",
    "Stage",
    "catalog",
    "catalog_json",
    "ddE_table",
    "floor_status",
    "funnel_rows",
    "hierarchy",
    "load_run",
    "method",
    "per_pose_table",
    "pose_output",
    "pose_scene",
    "pose_sdf",
    "receptor_pdb",
    "resolved_matrix",
]
