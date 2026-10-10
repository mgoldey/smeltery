"""The tier registry: every class that satisfies the `smeltery.tiers.Tier` protocol, by name.

A tier is found by INTROSPECTION of the modules in `TIER_MODULES`, not by a hand-kept
list of classes: a tier added to one of those modules is registered the moment it
exists, so it cannot be forgotten. A class is a tier when it is defined in the module
(not imported into it), is not itself a Protocol, has a string `name`, and has callable
`run`, `estimate_cost`, `settings`, `produces` and `systematic_floor`. Gates are not
tiers: they have none of those.

What introspection cannot see is a tier in a module this tuple does not list.
`tests/test_tier_registry.py` closes that hole by scanning every source file under
`src/smeltery` for a class with the whole tier surface and failing, naming the module,
if it is not covered here. Adding a module is a one-line edit to `TIER_MODULES`.

Importing a listed module must not need an optional extra: a tier whose module imports
one at the top level belongs behind a lazy import, as `smeltery.docking` does for vina.
"""

from __future__ import annotations

import importlib
import inspect

#: Modules searched for tiers. Order is the order of `registered_tiers()`.
TIER_MODULES: tuple[str, ...] = (
    "smeltery.tiers",
    "smeltery.docking.tier",
    "smeltery.scoring",
)

#: The methods and the attribute that make a class a tier (the `Tier` protocol).
TIER_METHODS: tuple[str, ...] = ("run", "estimate_cost", "settings", "produces", "systematic_floor")


def satisfies_tier_protocol(cls: type) -> bool:
    """True if `cls` has a string `name` and every method in `TIER_METHODS`."""
    if getattr(cls, "_is_protocol", False):
        return False
    return isinstance(getattr(cls, "name", None), str) and all(callable(getattr(cls, m, None)) for m in TIER_METHODS)


def registered_tiers() -> dict[str, type]:
    """{tier name: class} for every tier in `TIER_MODULES`. Duplicate names are an error."""
    found: dict[str, type] = {}
    for module_name in TIER_MODULES:
        module = importlib.import_module(module_name)
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_name or not satisfies_tier_protocol(cls):
                continue
            if cls.name in found:
                raise ValueError(
                    f"two tiers are named {cls.name!r}: {found[cls.name].__qualname__} and {cls.__qualname__}"
                )
            found[cls.name] = cls
    return found
