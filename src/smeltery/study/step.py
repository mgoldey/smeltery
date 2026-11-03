"""`Step` and `Study`: a DAG of composable units, run once per condition, cached by content.

A `Tier` measures a quantity on candidates. A `Step` is the layer above: a unit of a STUDY -- generate
samples for this cell at this setting, accept them, score them, summarise them -- declared the way a
tier is, so the same things can be asked of it before it runs.

    name / version      identity, and what invalidates the cache when the code changes meaning
    needs / provides    the edges; the DAG is derived from these, never declared twice
    run(inputs, ctx)    the work, a function of its declared inputs and nothing else

A `Study` is a set of steps plus the conditions to run them at. Running one is:

    for each condition, in topological order of the DAG:
        for each step: if its key is cached, take it; else run it and cache it

Which gives, with no scheduler and no server:

* **repeatable** -- the key is `digest(step, version, params, input keys)`, the same digest function
  the run record already uses, so re-running a study re-executes exactly the steps whose inputs
  changed and nothing else;
* **well-defined** -- `check()` refuses a step whose inputs nothing provides, a cycle, or two steps
  claiming the same output, before anything executes;
* **composable** -- a step is a small object with declared edges, so the 20 one-off scripts that
  each re-derive cells, scaffolds and acceptance become units that compose instead of copies.

**What a Step must be.** A function of its declared inputs. If it reads the wall clock, an unseeded
RNG, or a file not named in its params, the cache will serve one of its outputs forever and every
conclusion downstream rests on which run happened to land there. `Cache.put` raises on a differing
re-run precisely to surface that, and a step that genuinely varies must declare a seed as a param.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from .cache import Cache, step_key
from .condition import Condition


class StudyError(ValueError):
    """A study that cannot run as declared: a missing input, a cycle, a duplicated output."""


@runtime_checkable
class Step(Protocol):
    """One unit of a study. Mirrors `Tier`: declare first, run second."""

    name: str
    version: str

    def needs(self) -> tuple[str, ...]:
        """Input names this step consumes. Empty for a source step."""
        ...

    def provides(self) -> str:
        """The single output name this step produces."""
        ...

    def params(self, condition: Condition) -> dict:
        """Everything that changes the result at this condition, and nothing that does not.

        This goes into the cache key verbatim. A seed belongs here; a scratch path does not.
        """
        ...

    def run(self, inputs: dict[str, Any], condition: Condition, ctx: dict) -> Any:
        """Do the work. `inputs` is keyed by the names in `needs()`."""
        ...


@dataclass
class FunctionStep:
    """A `Step` from a plain function, for the common case that needs no state.

    `FunctionStep("accept", "1", ("samples",), "accepted", fn)` where
    `fn(inputs, condition, ctx) -> value`. Keeping the protocol satisfiable by a function is what
    stops a study step from being a ceremony.
    """

    name: str
    version: str
    _needs: tuple[str, ...]
    _provides: str
    fn: Callable[[dict, Condition, dict], Any]
    param_fn: Callable[[Condition], dict] | None = None

    def needs(self) -> tuple[str, ...]:
        return self._needs

    def provides(self) -> str:
        return self._provides

    def params(self, condition: Condition) -> dict:
        return self.param_fn(condition) if self.param_fn else condition.as_dict()

    def run(self, inputs: dict[str, Any], condition: Condition, ctx: dict) -> Any:
        return self.fn(inputs, condition, ctx)


@dataclass(frozen=True)
class StepResult:
    """One step at one condition: its value, its key, and whether it had to run."""

    step: str
    condition: Condition
    key: str
    value: Any
    cached: bool


@dataclass
class Study:
    """Steps, and the conditions to run them at.

    `conditions` is usually `condition.grid(...)`: the design as a value, so what was run is a fact
    rather than a nest of shell loops that has to be read to be known.
    """

    name: str
    steps: list[Step]
    conditions: list[Condition]
    ctx: dict = field(default_factory=dict)

    def by_name(self) -> dict[str, Step]:
        return {s.name: s for s in self.steps}

    def producers(self) -> dict[str, Step]:
        out: dict[str, Step] = {}
        for s in self.steps:
            if s.provides() in out:
                raise StudyError(
                    f"both {out[s.provides()].name!r} and {s.name!r} provide {s.provides()!r}; "
                    "an output has exactly one producer or the DAG is ambiguous"
                )
            out[s.provides()] = s
        return out

    def check(self) -> list[str]:
        """Every problem at once, as messages. Empty means the study can run."""
        problems: list[str] = []
        try:
            prod = self.producers()
        except StudyError as e:
            return [str(e)]
        names = {s.name for s in self.steps}
        if len(names) != len(self.steps):
            problems.append("step names must be unique")
        for s in self.steps:
            for n in s.needs():
                if n not in prod:
                    problems.append(f"step {s.name!r} needs {n!r}, which nothing provides")
        try:
            self.order()
        except StudyError as e:
            if str(e) not in problems:  # the missing-input case is already reported above
                problems.append(str(e))
        if not self.conditions:
            problems.append("a study with no conditions runs nothing")
        return problems

    def order(self) -> list[Step]:
        """Topological order. Raises on a cycle, naming the steps involved."""
        prod = self.producers()
        seen: dict[str, int] = {}  # 0 = visiting, 1 = done
        out: list[Step] = []

        def visit(s: Step, path: tuple[str, ...]) -> None:
            state = seen.get(s.name)
            if state == 1:
                return
            if state == 0:
                cycle = " -> ".join([*path[path.index(s.name) :], s.name]) if s.name in path else s.name
                raise StudyError(f"cycle in the study DAG: {cycle}")
            seen[s.name] = 0
            for n in s.needs():
                if n not in prod:
                    # `check()` reports this as its own message; raising the study's own error here
                    # keeps `order()` from leaking a bare KeyError into a caller collecting problems.
                    raise StudyError(f"step {s.name!r} needs {n!r}, which nothing provides")
                visit(prod[n], (*path, s.name))
            seen[s.name] = 1
            out.append(s)

        for s in self.steps:
            visit(s, ())
        return out

    def run(self, cache: Cache, *, only: list[Condition] | None = None, verbose: bool = True) -> list[StepResult]:
        """Run every step at every condition, taking cached results where the inputs are unchanged."""
        problems = self.check()
        if problems:
            raise StudyError(f"study {self.name!r} cannot run:\n  " + "\n  ".join(problems))
        results: list[StepResult] = []
        for cond in only if only is not None else self.conditions:
            keys: dict[str, str] = {}
            values: dict[str, Any] = {}
            for s in self.order():
                in_keys = {n: keys[n] for n in s.needs()}
                key = step_key(s.name, s.version, s.params(cond), in_keys)
                entry = cache.get(key)
                if entry is not None:
                    value, was_cached = entry.value, True
                else:
                    value = s.run({n: values[n] for n in s.needs()}, cond, self.ctx)
                    cache.put(key, step=s.name, version=s.version, params=s.params(cond), inputs=in_keys, value=value)
                    was_cached = False
                keys[s.provides()] = key
                values[s.provides()] = value
                results.append(StepResult(s.name, cond, key, value, was_cached))
                if verbose:
                    mark = "cached" if was_cached else "ran   "
                    print(f"  [{mark}] {s.name:<20} {cond.label()}  {key[:12]}")
        return results

    def plan(self, cache: Cache) -> dict:
        """What running would do, without doing it: how many steps are cached and how many would run."""
        cached = to_run = 0
        for cond in self.conditions:
            keys: dict[str, str] = {}
            for s in self.order():
                key = step_key(s.name, s.version, s.params(cond), {n: keys[n] for n in s.needs()})
                keys[s.provides()] = key
                if cache.has(key):
                    cached += 1
                else:
                    to_run += 1
        return {
            "study": self.name,
            "conditions": len(self.conditions),
            "steps": len(self.steps),
            "cached": cached,
            "to_run": to_run,
        }

    def dag(self) -> list[dict]:
        """The DAG as plain data, for a viewer: one row per step with its edges and order."""
        order = {s.name: i for i, s in enumerate(self.order())}
        prod = self.producers()
        return [
            {
                "step": s.name,
                "version": s.version,
                "order": order[s.name],
                "needs": list(s.needs()),
                "provides": s.provides(),
                "upstream": sorted({prod[n].name for n in s.needs()}),
            }
            for s in sorted(self.steps, key=lambda s: order[s.name])
        ]
