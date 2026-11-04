from __future__ import annotations


class PublishReport:
    def __init__(self, run_store, artifact_store):
        self.run_store = run_store
        self.artifact_store = artifact_store

    def run(self, run_path, *, out_path=None):
        run = self.run_store.read_run(run_path)
        # frozen snapshot via gui.report if available
        frozen = None
        try:
            from smeltery.gui import report as report_mod

            if hasattr(report_mod, "render"):
                frozen = report_mod.render(run)
        except Exception:
            frozen = None
        if frozen is not None:
            digest = self.artifact_store.put(frozen.encode() if isinstance(frozen, str) else frozen)
        else:
            digest = None
        return {"run": run, "frozen_digest": digest, "out_path": str(out_path) if out_path else None}

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
