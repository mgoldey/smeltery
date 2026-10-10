"""Provider registry: discover providers through Python entry points, importing nothing.

Naming scheme
-------------
One entry-point group, `smeltery.providers`. The entry NAME is `<kind>:<name>`, where
`kind` is one of `KINDS` (structure, scoring, docking, property, potential) and `name`
is the provider's own name. The entry VALUE is the usual `module:attribute`, and it
must point at a FACTORY: a class or any callable that returns a provider instance.

    [project.entry-points."smeltery.providers"]
    "property:ring-count" = "my_pkg.providers:RingCountProvider"

The registry hands out factories, not instances. A factory can need arguments
(`VinaScoreProvider(scores)`) or be expensive (an ML provider loading weights); the
caller decides when and how to build one.

No third-party import
---------------------
`discover()` reads installed-distribution metadata only (`importlib.metadata`); it
never imports the module an entry point names. Third-party code is imported only by
`ProviderEntry.load()`, through the entry-point mechanism (`EntryPoint.load`), on an
explicit request. Nothing in this file names a third-party package.

Broken providers are reported, not raised
-----------------------------------------
Discovery and `load_all()` collect `Problem`s (malformed name, unknown kind, ambiguous
duplicate, import failure, not callable, wrong kind) and carry on with the rest.
Only the single-provider calls, `ProviderEntry.load()` and `.create()`, raise
`ProviderLoadError`, because there the caller asked for exactly that provider.

This module imports only the standard library at module level, so it can be loaded
on its own (the pip-install test does exactly that).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from importlib import metadata
from typing import Any

GROUP = "smeltery.providers"
KINDS = ("structure", "scoring", "docking", "property", "potential")


class ProviderLoadError(RuntimeError):
    """A registered provider could not be loaded or built (import error, not callable, wrong kind).

    `stage` is "import", "factory" (not callable, or the factory raised) or "kind" (wrong kind).
    """

    def __init__(self, message: str, stage: str = "import") -> None:
        super().__init__(message)
        self.stage = stage


@dataclass(frozen=True)
class Problem:
    """Something wrong with a registration. `entry` is the raw entry-point name."""

    entry: str
    dist: str | None
    stage: str  # "name" | "duplicate" | "import" | "factory" | "kind"
    reason: str


def _protocol(kind: str) -> type:
    """The runtime-checkable Protocol for `kind`. Imported lazily: it pulls in numpy, rdkit-free but heavier."""
    if kind == "structure":
        from .structure import StructureProvider as p
    elif kind == "scoring":
        from ..scoring import ScoringProvider as p
    elif kind == "docking":
        from ..docking.base import DockingProvider as p
    elif kind == "property":
        from ..properties import PropertyProvider as p
    elif kind == "potential":
        from .potential import PotentialProvider as p
    else:
        raise ValueError(f"unknown provider kind {kind!r}; kinds are {KINDS}")
    return p


def split_entry_name(entry_name: str) -> tuple[str, str]:
    """`"<kind>:<name>"` -> (kind, name). Raises ValueError for anything else."""
    kind, sep, name = entry_name.partition(":")
    if not sep or not name.strip() or name != name.strip():
        raise ValueError(f"entry name {entry_name!r} is not '<kind>:<name>'")
    if kind not in KINDS:
        raise ValueError(f"entry name {entry_name!r} has unknown kind {kind!r}; kinds are {KINDS}")
    return kind, name


@dataclass(frozen=True)
class ProviderEntry:
    """One registered provider. Holding it imports nothing; `load()` does."""

    kind: str
    name: str
    value: str  # the entry point's "module:attribute"
    dist: str | None  # distribution that registered it
    _ep: metadata.EntryPoint = field(repr=False, compare=False)

    def load(self) -> Callable[..., Any]:
        """Import the provider's module and return its factory. Raises `ProviderLoadError`."""
        try:
            factory = self._ep.load()
        except Exception as e:  # noqa: BLE001 - third-party import can fail with anything
            raise ProviderLoadError(f"{self.kind}:{self.name} ({self.value}): {type(e).__name__}: {e}", "import") from e
        if not callable(factory):
            raise ProviderLoadError(
                f"{self.kind}:{self.name} ({self.value}) is a {type(factory).__name__}, not a callable factory",
                "factory",
            )
        return factory

    def create(self, *args: Any, **kwargs: Any) -> Any:
        """Build a provider and check it is of the registered kind (structurally: the kind's Protocol)."""
        factory = self.load()
        try:
            obj = factory(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            raise ProviderLoadError(
                f"{self.kind}:{self.name}: factory raised {type(e).__name__}: {e}", "factory"
            ) from e
        if not isinstance(obj, _protocol(self.kind)):
            raise ProviderLoadError(
                f"{self.kind}:{self.name} built a {type(obj).__name__}, which is not a {self.kind} provider "
                f"(it does not satisfy the {self.kind} Protocol)",
                "kind",
            )
        return obj


@dataclass(frozen=True)
class Discovery:
    entries: tuple[ProviderEntry, ...]
    problems: tuple[Problem, ...]

    def get(self, kind: str, name: str) -> ProviderEntry:
        for e in self.entries:
            if (e.kind, e.name) == (kind, name):
                return e
        raise KeyError(f"no provider {kind}:{name}; registered: {[f'{e.kind}:{e.name}' for e in self.entries]}")

    def of_kind(self, kind: str) -> tuple[ProviderEntry, ...]:
        return tuple(e for e in self.entries if e.kind == kind)


def discover(kind: str | None = None) -> Discovery:
    """List registered providers from installed metadata alone; no provider module is imported.

    A malformed name or unknown kind becomes a `Problem`. A `<kind>:<name>` registered by two
    distributions is ambiguous: BOTH are dropped and a `Problem` says so, rather than letting
    install order pick a winner silently.
    """
    if kind is not None and kind not in KINDS:
        raise ValueError(f"unknown provider kind {kind!r}; kinds are {KINDS}")
    found: dict[tuple[str, str], list[ProviderEntry]] = {}
    problems: list[Problem] = []
    for ep in metadata.entry_points(group=GROUP):
        dist = getattr(getattr(ep, "dist", None), "name", None)
        try:
            k, n = split_entry_name(ep.name)
        except ValueError as e:
            problems.append(Problem(ep.name, dist, "name", str(e)))
            continue
        if kind is None or k == kind:
            found.setdefault((k, n), []).append(ProviderEntry(k, n, ep.value, dist, ep))
    entries = []
    for (k, n), group in sorted(found.items()):
        unique = {(e.value, e.dist) for e in group}
        if len(unique) == 1:  # the same distribution listed twice on sys.path is one registration
            entries.append(group[0])
        else:
            who = sorted(f"{e.dist}: {e.value}" for e in group)
            problems.append(Problem(f"{k}:{n}", None, "duplicate", f"registered more than once ({who}); none used"))
    return Discovery(tuple(entries), tuple(problems))


@dataclass(frozen=True)
class LoadReport:
    factories: dict[tuple[str, str], Callable[..., Any]]
    problems: tuple[Problem, ...]


def load_all(kind: str | None = None, *, instantiate: bool = False) -> LoadReport:
    """Import every discovered provider (of `kind`), collecting failures instead of raising.

    By default only the factory is loaded and checked callable. `instantiate=True` also calls each
    factory with no arguments and checks the result against the kind's Protocol; use it only for
    providers whose factories take no required arguments (a factory that needs arguments is reported).
    """
    disc = discover(kind)
    problems = list(disc.problems)
    factories: dict[tuple[str, str], Callable[..., Any]] = {}
    for e in disc.entries:
        try:
            factory = e.load()
            if instantiate:
                e.create()
        except ProviderLoadError as err:
            problems.append(Problem(f"{e.kind}:{e.name}", e.dist, err.stage, str(err)))
            continue
        factories[(e.kind, e.name)] = factory
    return LoadReport(factories, tuple(problems))
