"""EditPipeline + PlacePocketSite use cases."""

from __future__ import annotations

from smeltery.gui.pipeline import Pipeline


class EditPipeline:
    def __init__(self, *ports):
        self.ports = ports

    def run(self, pipeline: Pipeline):
        return pipeline, pipeline.check()

    def execute(self, pipeline: Pipeline):
        return self.run(pipeline)

    def check(self, pipeline: Pipeline):
        return pipeline.check()

    __call__ = run


class PlacePocketSite:
    def __init__(self, *ports):
        self.ports = ports

    def run(self, pipeline: Pipeline, parent_atom: int, from_atom: int, *, q: float = 1.0, distance: float = 3.0):
        from smeltery.bench.provisional import place_site

        return place_site(pipeline, parent_atom, from_atom, q=q, distance=distance)

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
