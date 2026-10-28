"""Helpers for a GUI that builds `smeltery run` pipelines from the method hierarchy. No GUI toolkit is imported.

* `catalog`: every method (stage or tier) by rung, with typed knobs, artefacts in and out, and what each tier
  declares about itself (`catalog_json()` for a GUI not written in Python).
* `pipeline`: `Pipeline`, an ordered list of stages that `check()`s dataflow and knobs, and compiles to the config
  `smeltery run` reads (`to_config`, `to_toml`; `from_config`, `from_toml` for the way back).
"""

from .catalog import RUNGS, Method, Param, catalog, catalog_json, floor_status, hierarchy, method
from .pipeline import EXECUTION_ORDER, Issue, Pipeline, PipelineError, Stage

__all__ = [
    "EXECUTION_ORDER",
    "RUNGS",
    "Issue",
    "Method",
    "Param",
    "Pipeline",
    "PipelineError",
    "Stage",
    "catalog",
    "catalog_json",
    "floor_status",
    "hierarchy",
    "method",
]
