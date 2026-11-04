from __future__ import annotations

from smeltery.gui.results import ddE_table, funnel_rows, per_pose_table, resolved_matrix


class ReadRun:
    def __init__(self, run_store, artifact_store):
        self.run_store = run_store
        self.artifact_store = artifact_store

    def run(self, path):
        import types

        run = self.run_store.read_run(path)
        tables = {
            "ddE": ddE_table(run),
            "resolved_matrix": resolved_matrix(run),
            "per_pose": per_pose_table(run) if run.has_pose_output else [],
            "funnel": funnel_rows(run),
        }
        out = types.SimpleNamespace(run=run)
        for k, v in tables.items():
            setattr(out, k, v)
        # also expose as dict-like? add attributes for common names
        out.tables = tables
        return out

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
