# Campaign panel

**A study's DAG and per-cell cache state are visible.**

## Requirements

1. Studies are named by Python module path in settings and imported. There is no study file format
   and nothing is discovered by scanning (`Study` has no serialisation; inventing one is out of scope).
2. `Study.plan_cells(cache) -> list[dict]`, one row per (condition, step) with `condition`, `step`,
   `state` (`"cached"` or `"to_run"`) and `key`. It uses the same key derivation as `Study.plan`, and
   its counts equal `plan()`'s totals.
3. `GET /api/studies/{module_path}/dag` returns `Study.dag()`; `.../cells` returns `plan_cells`.
4. An unimportable module path is 404 with the path in the message, never a traceback.
5. The page draws the DAG from the `upstream` edges and the condition-by-step grid.
