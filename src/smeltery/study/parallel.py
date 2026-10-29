"""Running a study's conditions in parallel, with exclusive resources like GPUs.

A study's conditions are independent of each other and its steps are not: within one condition the
DAG is a chain, between conditions there is nothing to order. So the unit of parallelism is the
CONDITION, and `run_parallel` is `Study.run` with a worker per lane.

**Resources, not thread counts.** The thing that actually constrains this work is a device: two GPUs
means two conditions sampling at once, and a third worker would not be faster, it would be an
out-of-memory error. So a worker leases a resource from a pool for the whole condition and returns
it afterwards, and `ctx["resource"]` tells the step which one it got. A study with no resources
declared gets plain lanes.

**Threads, not processes.** The work here is a subprocess (`pxm_adapt.py`), a GPU kernel or a BLAS
call, all of which release the GIL; the Python in a step is bookkeeping. Threads also keep the cache
in one process, so two workers reaching the same key cannot both decide to compute it.

**The cache is the coordination.** Each condition writes entries under its own keys, and a key
collision between workers means the same condition was scheduled twice, which the lock below turns
into one computation rather than a race. `Cache.put` still raises on a differing value, so a step
that is not a function of its inputs is caught here exactly as it is in the sequential path.

Ordering of RESULTS is by condition, not by completion, so a parallel run and a sequential one
return the same list and a study is reproducible however many lanes it had.
"""

from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass
from typing import Any

from .cache import Cache, step_key
from .condition import Condition
from .step import StepResult, Study, StudyError


@dataclass
class ResourcePool:
    """Exclusive leases on named resources, e.g. `["cuda:0", "cuda:1"]`.

    A worker holds one for a whole condition rather than per step, because a condition's steps are a
    chain and handing the device back between them would only let another worker take it and stall.
    """

    names: list[str]

    def __post_init__(self) -> None:
        if not self.names:
            raise ValueError("a resource pool needs at least one resource")
        if len(set(self.names)) != len(self.names):
            raise ValueError(f"resource names must be unique, got {self.names}")
        self._q: queue.Queue[str] = queue.Queue()
        for n in self.names:
            self._q.put(n)

    def acquire(self) -> str:
        return self._q.get()

    def release(self, name: str) -> None:
        self._q.put(name)

    @property
    def size(self) -> int:
        return len(self.names)


class _Failures:
    """Exceptions from workers, kept with the condition that raised them."""

    def __init__(self) -> None:
        self.items: list[tuple[Condition, str, BaseException]] = []
        self._lock = threading.Lock()

    def add(self, cond: Condition, step: str, exc: BaseException) -> None:
        with self._lock:
            self.items.append((cond, step, exc))


def run_parallel(
    study: Study,
    cache: Cache,
    *,
    resources: ResourcePool | None = None,
    lanes: int | None = None,
    only: list[Condition] | None = None,
    verbose: bool = True,
    stop_on_error: bool = False,
) -> list[StepResult]:
    """`Study.run` with conditions in parallel.

    `resources` gives each worker an exclusive lease, visible to steps as `ctx["resource"]`, and sets
    the number of lanes. Without it, `lanes` sets them (default 1, i.e. the sequential path).

    A step that raises does not stop the other conditions unless `stop_on_error`; every failure is
    collected and re-raised together at the end, because killing 179 good conditions over one bad
    one wastes the compute that makes a study worth caching.
    """
    problems = study.check()
    if problems:
        raise StudyError(f"study {study.name!r} cannot run:\n  " + "\n  ".join(problems))

    conditions = list(only if only is not None else study.conditions)
    n_lanes = resources.size if resources is not None else max(1, lanes or 1)
    order = study.order()
    results: dict[Condition, list[StepResult]] = {}
    failures = _Failures()
    # One lock per key: two workers that reach the same key compute it once rather than racing.
    key_locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()
    print_lock = threading.Lock()
    stop = threading.Event()

    def lock_for(key: str) -> threading.Lock:
        with locks_guard:
            return key_locks.setdefault(key, threading.Lock())

    def one_condition(cond: Condition, resource: str | None) -> list[StepResult]:
        keys: dict[str, str] = {}
        values: dict[str, Any] = {}
        out: list[StepResult] = []
        ctx = dict(study.ctx)
        if resource is not None:
            ctx["resource"] = resource
        for s in order:
            if stop.is_set():
                break
            in_keys = {n: keys[n] for n in s.needs()}
            key = step_key(s.name, s.version, s.params(cond), in_keys)
            with lock_for(key):
                entry = cache.get(key)
                if entry is not None:
                    value, was_cached = entry.value, True
                else:
                    value = s.run({n: values[n] for n in s.needs()}, cond, ctx)
                    cache.put(key, step=s.name, version=s.version, params=s.params(cond), inputs=in_keys, value=value)
                    was_cached = False
            keys[s.provides()] = key
            values[s.provides()] = value
            out.append(StepResult(s.name, cond, key, value, was_cached))
            if verbose:
                with print_lock:
                    mark = "cached" if was_cached else "ran   "
                    where = f" @{resource}" if resource else ""
                    print(f"  [{mark}] {s.name:<20} {cond.label()}{where}  {key[:12]}", flush=True)
        return out

    work: queue.Queue[Condition] = queue.Queue()
    for c in conditions:
        work.put(c)
    results_lock = threading.Lock()

    def worker() -> None:
        while not stop.is_set():
            try:
                cond = work.get_nowait()
            except queue.Empty:
                return
            resource = resources.acquire() if resources is not None else None
            try:
                got = one_condition(cond, resource)
                with results_lock:
                    results[cond] = got
            except Exception as exc:  # noqa: BLE001 - one bad condition must not kill the rest
                failures.add(cond, "?", exc)
                if verbose:
                    with print_lock:
                        print(f"  [FAILED] {cond.label()}: {type(exc).__name__}: {exc}", flush=True)
                if stop_on_error:
                    stop.set()
            finally:
                if resource is not None and resources is not None:
                    resources.release(resource)
                work.task_done()

    threads = [threading.Thread(target=worker, name=f"study-{i}", daemon=True) for i in range(n_lanes)]
    if verbose:
        where = f" on {resources.names}" if resources is not None else ""
        print(f"running {len(conditions)} condition(s) across {n_lanes} lane(s){where}")
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    if failures.items:
        detail = "\n".join(
            f"  {c.label()}: {type(e).__name__}: {e}\n"
            + "".join("    " + ln for ln in traceback.format_exception(type(e), e, e.__traceback__)[-2:])
            for c, _, e in failures.items
        )
        raise StudyError(f"{len(failures.items)} of {len(conditions)} condition(s) failed:\n{detail}")

    # By condition, never by completion: a parallel run returns what a sequential one would.
    return [r for c in conditions if c in results for r in results[c]]
