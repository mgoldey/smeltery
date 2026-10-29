"""Repeatable studies: a DAG of composable steps, run per condition, cached by content.

A `Tier` measures a quantity on candidates; a `Study` is the layer above, where a comparison lives.
Three types carry it:

* `Condition` -- the point in a factor space a measurement was taken at, as a value rather than a
  concatenated string. Pooling is `project(())`, an operation a caller has to ask for.
* `Step` -- a unit with a name, a version and declared `needs`/`provides`. The DAG is derived from
  those edges, never declared twice.
* `Study` -- steps plus conditions, with `check()` refusing a missing input, a cycle or a duplicated
  output before anything runs, and `plan()` saying what would execute.

`Cache` keys each step on `digest(step, version, params, input keys)` -- the same digest the run
record uses -- so resume, provenance and incrementality are one mechanism and need no daemon.
"""

from .cache import Cache, CacheMismatchError, Entry, step_key
from .condition import Condition, common_fields, grid
from .parallel import ResourcePool, run_parallel
from .reduction import (
    Estimate,
    Group,
    Reduction,
    ReductionError,
    paired_difference,
    rate,
)
from .step import FunctionStep, Step, StepResult, Study, StudyError

__all__ = [
    "Cache",
    "CacheMismatchError",
    "Condition",
    "Entry",
    "Estimate",
    "Group",
    "Reduction",
    "ReductionError",
    "ResourcePool",
    "FunctionStep",
    "Step",
    "StepResult",
    "Study",
    "StudyError",
    "common_fields",
    "grid",
    "paired_difference",
    "rate",
    "run_parallel",
    "step_key",
]
