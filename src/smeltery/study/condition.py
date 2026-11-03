"""A `Condition`: the point in a factor space a measurement was taken at.

Every comparison in this project is "the same thing under different conditions", and every comparison
error made in it came from that point being a bare string. A cell id like `s17_k4` is really
`{"scaffold": "s17", "mode": "k4"}` flattened by concatenation, and once flattened you cannot group
by scaffold, cannot marginalise over mode, cannot tell that one arm covered 3 scaffolds x 4 modes
while the other covered 4 x 3 -- and cannot state what is being held fixed.

**Pooling is projection onto the empty condition.** That is the whole trap, stated as an operation:
pooling two arms' molecules discards every factor and compares mixtures rather than arms. On the
gen-compare study that moved the measured difference from -1.21 to -3.24 kcal/mol. When the factors
are a value, `project(())` is a thing a caller can be made to ask for explicitly, instead of a thing
that happens by default when someone calls `median` on everything.

A Condition is a frozen mapping, hashable, so it works as a dict key and a group label. `fields()`
names what varied, which is what a comparison must agree on before it can difference anything.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


class Condition(Mapping[str, str]):
    """An immutable, hashable mapping of factor name -> level.

    `Condition(scaffold="s17", mode="k4")` or `Condition({"scaffold": "s17", "mode": "k4"})`. Values
    are coerced to `str`: a level is a label, not a measurement, and allowing floats here invites
    keying on something that should have been an outcome.
    """

    __slots__ = ("_d", "_hash")

    def __init__(self, _m: Mapping[str, Any] | None = None, **kw: Any) -> None:
        d: dict[str, str] = {}
        for k, v in {**(dict(_m) if _m else {}), **kw}.items():
            if not isinstance(k, str) or not k:
                raise ValueError(f"factor names must be non-empty strings, got {k!r}")
            if v is None:
                raise ValueError(f"factor {k!r} has no level; omit the factor rather than setting None")
            d[k] = str(v)
        object.__setattr__(self, "_d", dict(sorted(d.items())))
        object.__setattr__(self, "_hash", hash(tuple(sorted(d.items()))))

    def __getitem__(self, k: str) -> str:
        return self._d[k]

    def __iter__(self) -> Iterator[str]:
        return iter(self._d)

    def __len__(self) -> int:
        return len(self._d)

    def __hash__(self) -> int:
        return self._hash

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Condition) and self._d == other._d

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v!r}" for k, v in self._d.items())
        return f"Condition({inner})" if inner else "Condition()  # the empty condition: everything pooled"

    def __setattr__(self, *_: Any) -> None:
        raise AttributeError("Condition is immutable")

    @property
    def fields(self) -> tuple[str, ...]:
        """The factors this condition fixes, sorted. Two conditions are comparable when these match."""
        return tuple(self._d)

    def project(self, fields: tuple[str, ...]) -> Condition:
        """Keep only `fields`. `project(())` is POOLING, and has to be written to happen."""
        missing = sorted(set(fields) - set(self._d))
        if missing:
            raise KeyError(f"cannot project onto {missing}: this condition has {list(self._d)}")
        return Condition({k: self._d[k] for k in fields})

    def with_(self, **kw: Any) -> Condition:
        """A copy with factors added or replaced."""
        return Condition({**self._d, **kw})

    def without(self, *fields: str) -> Condition:
        """A copy with `fields` dropped. Dropping every field gives the empty (pooled) condition."""
        return Condition({k: v for k, v in self._d.items() if k not in fields})

    def label(self, sep: str = "/") -> str:
        """A readable one-line label, for axes and tables: `scaffold=s17/mode=k4`."""
        return sep.join(f"{k}={v}" for k, v in self._d.items()) or "(pooled)"

    def as_dict(self) -> dict[str, str]:
        return dict(self._d)


def common_fields(conditions: list[Condition]) -> tuple[str, ...]:
    """Factors every condition fixes. A comparison may only group by these."""
    if not conditions:
        return ()
    out = set(conditions[0].fields)
    for c in conditions[1:]:
        out &= set(c.fields)
    return tuple(sorted(out))


def grid(**factors: list[Any]) -> list[Condition]:
    """Every combination of the given factor levels, in a deterministic order.

    `grid(scaffold=["s17", "s19"], std=[0, 1])` gives the four conditions. This is how a sweep is
    declared: the design is a value, so what was run is a fact rather than a nest of shell loops.
    """
    import itertools

    names = sorted(factors)
    if not names:
        return [Condition()]
    for n in names:
        if not factors[n]:
            raise ValueError(f"factor {n!r} has no levels")
    return [Condition(dict(zip(names, vals, strict=True))) for vals in itertools.product(*(factors[n] for n in names))]
