from __future__ import annotations


class CommentOnRun:
    def __init__(self, comment_store, identity, clock):
        self.comment_store = comment_store
        self.identity = identity
        self.clock = clock

    def run(self, anchor_type, anchor_id, body, *, parent_id=None):
        return self.comment_store.add(anchor_type, anchor_id, self.identity.current(), body, parent_id=parent_id)

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
