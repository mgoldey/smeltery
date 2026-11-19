"""The target-class table: data in, campaign-config sections out.

The table itself is `src/smeltery/data/target_classes.toml`. Nothing here decides anything about chemistry; it
reads that file, checks the shape of each entry, and maps the entry onto the config sections `smeltery run`
already validates. A new class is a new table in the TOML file, never a new branch here.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

DATA_FILE = "target_classes.toml"

#: The config section each tier is selected by in a campaign config (`smeltery.cli.measurement_tier`).
TIER_SECTIONS = {"field_interaction": "field_interaction", "field_dft_interaction": "field_dft_interaction"}
#: Tier knobs an entry may set, beyond `basis`. Anything else in an entry is a table error, not a silent pass.
TIER_KNOBS = ("functional", "memory_budget_gb", "require_free_gb")
#: The only basis either tier has a cost model for (docs/tiers/*.md); the table may not choose another.
BASIS = "sto-3g"


class TargetClassError(ValueError):
    """The target-class table is unusable, or names a class that is not in it."""


@dataclass(frozen=True)
class TargetClass:
    """One row of the table: what a caller gets by naming it, and why those values."""

    name: str
    tier: str
    summary: str
    n_poses: int
    relax_unmapped: bool
    keep: int
    z: float
    floor: float
    why: str
    limits: str
    knobs: dict  # the tier knobs this class sets beyond `basis` (functional, memory_budget_gb, ...)

    def tier_section(self) -> tuple[str, dict]:
        """The config section that selects this class's measurement tier, and its knobs."""
        return TIER_SECTIONS[self.tier], {"basis": BASIS, **self.knobs}


def _text(table: dict, key: str, cls: str) -> str:
    value = table.get(key)
    if not isinstance(value, str) or not value.strip():
        raise TargetClassError(f"target class {cls!r}: {key} must be a non-empty string")
    return " ".join(value.split())


@lru_cache(maxsize=1)
def load_table() -> tuple[dict[str, TargetClass], str]:
    """Every class in the table, and the name of the default one. Cached: the file ships with the package."""
    # One component per joinpath call (tests/test_resource_joinpath.py), as smeltery.cost does.
    raw = tomllib.loads(resources.files("smeltery").joinpath("data").joinpath(DATA_FILE).read_text())
    classes: dict[str, TargetClass] = {}
    for name, table in (raw.get("classes") or {}).items():
        tier = table.get("tier")
        if tier not in TIER_SECTIONS:
            raise TargetClassError(f"target class {name!r}: tier must be one of {sorted(TIER_SECTIONS)}, got {tier!r}")
        classes[name] = TargetClass(
            name=name,
            tier=tier,
            summary=_text(table, "summary", name),
            n_poses=int(table["n_poses"]),
            relax_unmapped=bool(table["relax_unmapped"]),
            keep=int(table["keep"]),
            z=float(table["z"]),
            floor=float(table["floor"]),
            why=_text(table, "why", name),
            limits=_text(table, "limits", name),
            knobs={k: table[k] for k in TIER_KNOBS if k in table},
        )
    default = raw.get("default")
    if default not in classes:
        raise TargetClassError(f"the table's default {default!r} is not one of its classes {sorted(classes)}")
    return classes, default


def names() -> list[str]:
    return sorted(load_table()[0])


def default_name() -> str:
    return load_table()[1]


def get(name: str | None) -> TargetClass:
    """The named class, or the table's default when `name` is None. An unknown name names the valid ones."""
    classes, default = load_table()
    key = default if name is None else name
    if key not in classes:
        raise TargetClassError(f"unknown target class {name!r}; the classes are: {', '.join(sorted(classes))}")
    return classes[key]


def catalogue() -> list[dict]:
    """Every class as a small dict, for a tool description or a doc table."""
    classes, _ = load_table()
    return [
        {
            "name": c.name,
            "tier": c.tier,
            "summary": c.summary,
            "n_poses": c.n_poses,
            "why": c.why,
            "limits": c.limits,
        }
        for c in (classes[n] for n in sorted(classes))
    ]
