"""What smeltery cannot tell you: `docs/limits.md` read as data, and the floor stated as a sentence.

The limits page is the single source. A source checkout has it; an installed wheel does not, and then `load_limits`
returns None and a surface leaves its section out rather than inventing one.
"""

from __future__ import annotations

import math
from pathlib import Path

# src/smeltery/gui/limits.py -> parents[3] is the repo root (a source checkout; a wheel has no docs/)
LIMITS_DOC = Path(__file__).resolve().parents[3] / "docs" / "limits.md"

MEASURED_COLUMNS = ("id", "limit", "magnitude", "measured_on", "source", "forbids")
UNMEASURED_COLUMNS = ("id", "limit", "status", "tracked_in", "forbids")


def table_rows(text: str, heading: str) -> list[list[str]]:
    """The body rows of the first table under `## heading`, as lists of stripped cells. Raises IndexError when the
    heading is absent."""
    section = text.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("|")][2:]
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in rows]


def parse_limits(text: str) -> dict[str, list[dict[str, str]]]:
    """`{"measured": [...], "unmeasured": [...]}`, each row a dict keyed by column. A malformed row (wrong cell
    count) is skipped: the doc's own test is what fails on it."""
    out: dict[str, list[dict[str, str]]] = {}
    for key, heading, cols in (
        ("measured", "Measured limits", MEASURED_COLUMNS),
        ("unmeasured", "Not yet measured", UNMEASURED_COLUMNS),
    ):
        try:
            rows = table_rows(text, heading)
        except IndexError:
            rows = []
        out[key] = [dict(zip(cols, r, strict=True)) for r in rows if len(r) == len(cols)]
    return out


def load_limits(path: Path | None = None) -> dict[str, list[dict[str, str]]] | None:
    """The parsed limits page, or None when it is missing, unreadable or has no rows."""
    try:
        parsed = parse_limits((path or LIMITS_DOC).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return None
    return parsed if parsed["measured"] or parsed["unmeasured"] else None


def floor_statement(floor: float | None, unit: str, source: str) -> str:
    """The floor, its source, and what it forbids, in one place for the report page and the archived snapshot."""
    if floor is None or not math.isfinite(floor):
        return (
            "The floor is unmeasured for this run, so no systematic bound was applied: only the statistical "
            "interval separates candidates, and a difference the pose noise cannot see is not ruled out."
        )
    src = f" ({source})" if source else ""
    if floor <= 0:
        return f"The floor is 0 {unit}{src}: no systematic bound was applied, only the statistical interval."
    return (
        f"The floor is {floor:g} {unit}{src}. Differences smaller than {floor:g} {unit} are reported as ties, "
        "not rankings."
    )
