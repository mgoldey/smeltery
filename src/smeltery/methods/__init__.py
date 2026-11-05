"""The method registry: the one place a method is declared.

A `MethodSpec` says everything the rest of smeltery needs to know about one method: where it sits in the
hierarchy (`rung`, `role`), which step of the pipeline drawing holds it (`step`), when `smeltery run` executes it
(`order`), which execution slot it fills (`slot`), its config table (`section`) and the knobs it takes there
(`knobs`, a dataclass, or typed `params`), which of those knobs name input files (`file_knobs`, so staging copies
them), the tier it runs, whether every pipeline needs it (`required`) or it excludes another (`exclusive`), and a
`hook` of its slot's protocol.

Everything that used to list methods by hand derives from `registry()`: the GUI catalog (`smeltery.gui.catalog`),
the execution order and steps (`smeltery.gui.pipeline`, `smeltery.gui.steps`), the CLI's top-level keys and section
validation (`smeltery.cli`) and input staging (`smeltery.run_inputs`). Today's methods are registrations in
`smeltery.methods.builtin`, loaded the first time `registry()` is called.

Slots with a hook protocol, consulted by `smeltery.cli.build_plan` / `run_plan` when the method's section is in the
config and its `hook` is set (a builtin with `hook=None` keeps its own code path in `smeltery.cli`):

* `pose_source`: `PoseSource.parent_poses(cfg, parent, ctx)` gives the parent's poses; analogues are paired to them.
* `field_source`: `FieldSource.field(cfg, parent, ctx)` gives the point charges.
* `measure`: `Measure.tier(cfg)` gives the dE_int tier.
* `analysis`: `Analysis.run(plan, rec, ctx)` runs after the measurement; its dict is merged into `results[key]`.

Importing this module is cheap: nothing heavier than `dataclasses` is imported until `registry()` is called.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..model import Candidate, PointCharge, Pose

#: Execution slots. `None` (no slot) is a stage whose code lives in `smeltery.cli` (gates, pairing, the cut).
SLOTS = ("pose_source", "field_source", "measure", "analysis")

#: The steps of the pipeline drawing (`smeltery.gui.steps`) a method may be placed in.
STEP_KEYS = ("active_site", "ligands", "poses", "checks", "measure", "cut")


@runtime_checkable
class PoseSource(Protocol):
    """Gives the parent's poses (in the field's frame); the analogues are paired to them."""

    def parent_poses(self, cfg: dict, parent: Candidate, ctx: dict) -> list[Pose]: ...


@runtime_checkable
class FieldSource(Protocol):
    """Gives the pocket's point charges in the poses' frame (a `PocketField` carries its provenance)."""

    def field(self, cfg: dict, parent: Candidate, ctx: dict) -> list[PointCharge]: ...


@runtime_checkable
class Measure(Protocol):
    """Gives the tier that measures dE_int."""

    def tier(self, cfg: dict) -> Any: ...


@runtime_checkable
class Analysis(Protocol):
    """Runs after the measurement; the dict it returns is merged into `results[key]`."""

    def run(self, plan: Any, rec: Any, ctx: dict) -> dict: ...


_PROTOCOL = {"pose_source": PoseSource, "field_source": FieldSource, "measure": Measure, "analysis": Analysis}


@dataclass(frozen=True)
class MethodSpec:
    """One method, declared once. See the module docstring for what derives from each field.

    `knobs` is the dataclass whose init fields are the section's knobs (a tier's own class, so defaults cannot
    drift); `params` are typed knobs for a form (`smeltery.gui.catalog.Param`), derived from `knobs` when empty.
    `validate(cfg)` replaces the generic section check (a table whose keys are the knobs; no `exclusive` method's
    section beside it) for a method whose rules are more than its keys; it is called for every config, present or
    not, and raises `smeltery.cli.ConfigError`. `order` is the method's place in `smeltery run`'s execution order;
    a wired method has one, an unwired method has none.
    """

    key: str
    label: str
    rung: str
    role: str
    step: str | None = None
    slot: str | None = None
    section: str | None = None
    knobs: type | None = None
    file_knobs: tuple[str, ...] = ()
    tier: str | None = None
    required: bool = False
    exclusive: tuple[str, ...] = ()
    wired: bool = True
    hook: object | None = None
    # the catalog fields this replaces
    needs: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    gate_key: str | None = None
    summary: str = ""
    implicit: bool = False
    why_unwired: str = ""
    params: tuple = ()
    extras: tuple[str, ...] = ()
    order: int | None = None
    quantity: str | None = None
    validate: Callable[[dict], None] | None = None
    cost_model: Callable[[], str] | None = None
    memory_model: Callable[[], str] | None = None

    def __post_init__(self) -> None:
        if self.slot is not None and self.slot not in SLOTS:
            raise ValueError(f"method {self.key!r}: unknown slot {self.slot!r}; slots: {SLOTS}")
        if self.step is not None and self.step not in STEP_KEYS:
            raise ValueError(f"method {self.key!r}: unknown step {self.step!r}; steps: {STEP_KEYS}")
        if self.hook is not None:
            if self.slot is None:
                raise ValueError(f"method {self.key!r}: a hook needs a slot")
            if not isinstance(self.hook, _PROTOCOL[self.slot]):
                raise TypeError(f"method {self.key!r}: hook does not implement {_PROTOCOL[self.slot].__name__}")
            if self.section is None:
                raise ValueError(f"method {self.key!r}: a hook runs when its section is in the config; give a section")
        if self.wired != (self.order is not None):
            raise ValueError(f"method {self.key!r}: a wired method has an execution order, an unwired one has none")
        unknown = sorted(set(self.file_knobs) - set(self.knob_names()))
        if unknown:
            raise ValueError(f"method {self.key!r}: file knob(s) {unknown} are not among its knobs")

    def knob_names(self) -> tuple[str, ...]:
        """The keys its config section accepts: the `knobs` dataclass's init fields, else the `params` names."""
        if self.knobs is not None:
            internal = {"name", "field_provenance", "scaffold_maps"}  # state a tier fills in itself, not knobs
            return tuple(f.name for f in dataclasses.fields(self.knobs) if f.init and f.name not in internal)
        return tuple(p.name for p in self.params)


_REGISTRY: dict[str, MethodSpec] = {}
_BUILTINS_LOADED = False
_VERSION = 0  # bumped on every change, so derived views know to rebuild


def _ensure_builtins() -> None:
    global _BUILTINS_LOADED
    if not _BUILTINS_LOADED:
        _BUILTINS_LOADED = True
        from . import builtin

        try:
            builtin.register_builtins()
            _register_extensions()
        except BaseException:  # never leave a half-registered catalog behind: the next call retries from scratch
            _REGISTRY.clear()
            _BUILTINS_LOADED = False
            raise


def _register_extensions() -> None:
    """Every other module of this package that defines `register_methods()` declares its methods: adding a method is
    adding a module here, with no list to edit. Modules are loaded in name order, after the built-ins."""
    import importlib
    import pkgutil

    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name == "builtin" or info.name.startswith("_"):
            continue
        mod = importlib.import_module(f"{__name__}.{info.name}")
        hook = getattr(mod, "register_methods", None)
        if callable(hook):
            hook()


def register(spec: MethodSpec) -> MethodSpec:
    """Declare `spec`. Refuses a key already registered (unregister it first) and a spec the catalog would refuse
    (unknown rung, role or artefact). Returns the spec, so it can decorate a module-level constant."""
    global _VERSION
    _ensure_builtins()
    if spec.key in _REGISTRY:
        raise ValueError(f"method {spec.key!r} is already registered")
    for other in _REGISTRY.values():
        if spec.section is not None and other.section == spec.section:
            raise ValueError(f"method {spec.key!r}: section [{spec.section}] is already {other.key!r}'s")
        if spec.gate_key is not None and other.gate_key == spec.gate_key:
            raise ValueError(f"method {spec.key!r}: gate {spec.gate_key!r} is already {other.key!r}'s")
    from ..gui.catalog import method_from_spec

    method_from_spec(spec)  # the catalog's own checks, now rather than when a GUI first draws it
    _REGISTRY[spec.key] = spec
    _VERSION += 1
    return spec


def unregister(key: str) -> MethodSpec:
    """Remove a registration (a test's dummy method); returns it."""
    global _VERSION
    _ensure_builtins()
    try:
        spec = _REGISTRY.pop(key)
    except KeyError:
        raise KeyError(f"no method {key!r} is registered") from None
    _VERSION += 1
    return spec


def registry() -> dict[str, MethodSpec]:
    """{key: MethodSpec}, in registration order (the catalog's hierarchy order). A copy."""
    _ensure_builtins()
    return dict(_REGISTRY)


def version() -> int:
    """Changes whenever the registry does; derived views cache on it."""
    _ensure_builtins()
    return _VERSION


def by_slot(slot: str) -> list[MethodSpec]:
    """The registered methods that fill `slot`, in registration order."""
    if slot not in SLOTS:
        raise ValueError(f"unknown slot {slot!r}; slots: {SLOTS}")
    return [s for s in registry().values() if s.slot == slot]


def execution_order() -> tuple[str, ...]:
    """The order `smeltery run` executes the wired methods in (`order`, ties by registration)."""
    specs = [s for s in registry().values() if s.order is not None]
    return tuple(s.key for s in sorted(specs, key=lambda s: s.order))  # sorted is stable: ties keep registration


def active(spec: MethodSpec, cfg: dict) -> bool:
    """Whether a config uses this method: its section is present, or its gate is switched on."""
    if spec.section is not None and spec.section in cfg:
        return True
    gates = cfg.get("gates")
    return spec.gate_key is not None and isinstance(gates, dict) and bool(gates.get(spec.gate_key))


def active_hooks(slot: str, cfg: dict) -> list[MethodSpec]:
    """The methods of `slot` that this config uses and that carry a hook (builtins without one keep their own path)."""
    return [s for s in by_slot(slot) if s.hook is not None and active(s, cfg)]


__all__ = [
    "SLOTS",
    "STEP_KEYS",
    "Analysis",
    "FieldSource",
    "Measure",
    "MethodSpec",
    "PoseSource",
    "active",
    "active_hooks",
    "by_slot",
    "execution_order",
    "register",
    "registry",
    "unregister",
    "version",
]
