from __future__ import annotations


class ListWork:
    def __init__(self, task_queue):
        self.task_queue = task_queue

    def run(self, status=None):
        return self.task_queue.list(status=status)

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
