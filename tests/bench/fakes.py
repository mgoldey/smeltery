from __future__ import annotations

from datetime import datetime, timezone

from smeltery.bench.adapters.artifact_store import FilesystemArtifactStore
from smeltery.bench.adapters.run_store import FilesystemRunStore


class FakeIdentity:
    def __init__(self, user="mg"):
        self.user = user

    def current(self):
        return self.user


class FixedClock:
    def __init__(self, now=None):
        self._now = now or datetime(2026, 11, 3, tzinfo=timezone.utc)

    def now(self):
        return self._now


class FakeTaskQueue:
    def __init__(self, tasks=None):
        self.tasks = list(tasks or [])
        self.submitted = []
        self.executed = []

    def submit(self, name, command, *, cwd=None, env=None, log_dir=None, note=""):
        tid = f"t{len(self.submitted)}"
        self.submitted.append((name, list(command), cwd, note))
        return tid

    def list(self, status=None):
        if status is None:
            return list(self.tasks)
        return [t for t in self.tasks if t.status == status]

    def get(self, tid):
        for t in self.tasks:
            if t.id == tid:
                return t
        return None

    def status(self, tid):
        t = self.get(tid)
        if t is None:
            raise KeyError(tid)
        return t.status

    def cancel(self, tid):
        return False

    def tail_log(self, tid, lines=40):
        return ""


class FakeRunStore:
    def __init__(self, root):
        self._store = FilesystemRunStore(root)

    def list_runs(self, root=None):
        return self._store.list_runs(root)

    def read_run(self, path):
        return self._store.read_run(path)

    def find_runs(self, root=None):
        return self._store.find_runs(root)


class FakeArtifactStore(FilesystemArtifactStore):
    pass


def _task(tid, status):
    from smeltery.tasks import Task

    return Task(id=tid, name=tid, command=["echo"], cwd="", status=status)
