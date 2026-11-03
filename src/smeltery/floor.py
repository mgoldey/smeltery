"""A floor, carrying what it applies to -- so the wrong one cannot be used silently.

A systematic floor is the size below which a number is not meaningful. This library has several, and
they are not interchangeable:

* An **absolute** floor bounds the error in one value. Preparing a pocket from a crystal structure
  forces choices no experiment settles -- where OH, NH2 and SH hydrogens point, above all -- and two
  defensible preparations shift one molecule's interaction energy by several kcal/mol.
* A **difference** floor bounds the error in a difference taken under ONE condition. Most of an
  absolute shift is shared by every molecule and cancels; only the part that varies survives.
  Averaged over groups it shrinks again, as `sd(shift) * sqrt(1/n_a + 1/n_b)`.

Quoting the absolute floor on a comparison is too conservative by roughly sqrt(n), and makes a
comparison unresolvable however good the data. That mistake has been made in this project, on a real
result, and it was caught only by re-deriving the arithmetic by hand. A bare `float` cannot say which
kind it is, so the prose warning was the only guard; this type is that warning made structural.

`Floor.check(ABSOLUTE)` raises when handed a difference floor, and vice versa. The decision to use
one anyway is still available -- `Floor.as_kind` relabels explicitly, and records that you did.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

#: Bounds the error in ONE value.
ABSOLUTE = "absolute"
#: Bounds the error in a DIFFERENCE taken under one condition.
DIFFERENCE = "difference"
KINDS = (ABSOLUTE, DIFFERENCE)


class FloorKindError(ValueError):
    """A floor was used where a floor of the other kind belongs."""


@dataclass(frozen=True)
class Floor:
    """`value` in `unit`, what it `applies_to`, and where it came from.

    `source` is free text naming the measurement, not a category: "field_sensitivity: pdb2pqr30 vs
    openmm, n=12" beats "prep". It is quoted in reports, so it should survive being read alone.
    """

    value: float
    unit: str
    applies_to: str
    source: str

    def __post_init__(self) -> None:
        if self.applies_to not in KINDS:
            raise ValueError(f"applies_to must be one of {KINDS}, not {self.applies_to!r}")
        if self.value < 0:
            raise ValueError(f"a floor cannot be negative, got {self.value}")
        if not self.source:
            raise ValueError("a floor without a source cannot be checked by a reader; name its measurement")

    def check(self, kind: str) -> Floor:
        """Return self when it is a `kind` floor, else raise naming both kinds and the source."""
        if kind not in KINDS:
            raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
        if self.applies_to != kind:
            article = "an" if self.applies_to == ABSOLUTE else "a"
            raise FloorKindError(
                f"this is {article} {self.applies_to} floor ({self.value:g} {self.unit}, from {self.source}) "
                f"but a {kind} floor is needed here. An absolute floor bounds one value; a difference "
                f"floor bounds a difference taken under one condition, and is smaller because the "
                f"shared part cancels. Measure the right one, or relabel it with as_kind() and say so."
            )
        return self

    def as_kind(self, kind: str, why: str) -> Floor:
        """Relabel deliberately, recording why in the source. For when no better floor was measured."""
        if not why:
            raise ValueError("relabelling a floor needs a reason; it is the only record that it happened")
        return replace(self, applies_to=kind, source=f"{self.source} [relabelled {self.applies_to}->{kind}: {why}]")

    def resolves(self, difference: float) -> bool:
        """Is `difference` larger than this floor? Checks the kind first."""
        self.check(DIFFERENCE)
        return abs(difference) > self.value

    def __str__(self) -> str:
        return f"{self.value:g} {self.unit} ({self.applies_to}, {self.source})"


def from_sensitivity(report, *, n_a: int | None = None, n_b: int | None = None, unit: str = "kcal/mol") -> Floor:
    """A `Floor` from a `gates.SensitivityReport`.

    Without `n_a`/`n_b` this is the ABSOLUTE floor: `report.floor`, the range of the per-candidate
    shifts, which bounds how much swapping conditions can change the gap between any two candidates.

    With them it is the DIFFERENCE floor for comparing groups of that size, `shift_sd *
    sqrt(1/n_a + 1/n_b)`. That assumes shifts are independent across candidates, which they are not
    when candidates share a scaffold, so it is a lower bound; the source records the assumption.
    """
    if (n_a is None) != (n_b is None):
        raise ValueError("pass both n_a and n_b, or neither")
    if n_a is None:
        return Floor(
            value=report.floor,
            unit=unit,
            applies_to=ABSOLUTE,
            source=f"sensitivity range over n={report.n} candidates, quantity {report.quantity}",
        )
    return Floor(
        value=report.aggregate_difference_floor(n_a, n_b),
        unit=unit,
        applies_to=DIFFERENCE,
        source=(
            f"sensitivity sd {report.shift_sd:.3f} over n={report.n} candidates, scaled to groups of "
            f"{n_a} and {n_b}; assumes shifts independent across candidates, so a LOWER bound"
        ),
    )
