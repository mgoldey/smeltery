from __future__ import annotations


class OpenStructure:
    def __init__(self, structure_source, artifact_store):
        self.structure_source = structure_source
        self.artifact_store = artifact_store

    def run(self, ref: str, *, allow_network: bool = False):
        path = self.structure_source.fetch(ref, allow_network=allow_network)
        data = path.read_bytes()
        digest = self.artifact_store.put(data)
        return {"path": str(path), "digest": digest, "ref": ref}

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
