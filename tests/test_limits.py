"""docs/limits.md: each measured limit has a number and an existing source; each unmeasured one says so (#30)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "limits.md"
SOURCE_PATH = re.compile(r"`([^`]+\.(?:json|md|py))`")
ISSUE = re.compile(r"issue #\d+")


def _table(text: str, heading: str) -> list[list[str]]:
    section = text.split(f"## {heading}", 1)[1].split("\n## ", 1)[0]
    rows = [ln for ln in section.splitlines() if ln.startswith("|")][2:]
    return [[c.strip() for c in ln.strip("|").split("|")] for ln in rows]


def check_measured(rows: list[list[str]], root: Path = ROOT) -> list[str]:
    problems = []
    for r in rows:
        if len(r) != 6:
            problems.append(f"{r[:1]}: expected 6 columns, got {len(r)}")
            continue
        lid, _, magnitude, system, source, claim = r
        if not re.search(r"\d", magnitude):
            problems.append(f"{lid}: magnitude has no number")
        if not system:
            problems.append(f"{lid}: no system")
        if not claim:
            problems.append(f"{lid}: no forbidden claim")
        paths = SOURCE_PATH.findall(source)
        if not paths and not ISSUE.search(source):
            problems.append(f"{lid}: no source")
        for p in paths:
            if not (root / p).exists():
                problems.append(f"{lid}: source {p} does not exist")
    return problems


def check_unmeasured(rows: list[list[str]]) -> list[str]:
    problems = []
    for r in rows:
        if len(r) != 5 or "UNMEASURED" not in r[2] or not ISSUE.search(r[3]):
            problems.append(f"{r[:1]}: needs UNMEASURED and an issue")
    return problems


def test_measured_limits_have_numbers_and_sources():
    rows = _table(DOC.read_text(), "Measured limits")
    assert len(rows) >= 3
    assert check_measured(rows) == []


def test_unmeasured_limits_are_marked():
    rows = _table(DOC.read_text(), "Not yet measured")
    assert rows and check_unmeasured(rows) == []


def test_no_revision_history():
    assert not re.search(r"(?i)\b(previously|used to|was changed|changelog|revised)\b", DOC.read_text())


def test_readme_links_limits():
    assert "docs/limits.md" in (ROOT / "README.md").read_text()


@pytest.mark.parametrize(
    "row",
    [
        ["L9", "x", "no figure here", "sys", "`docs/limits.md`", "claim"],
        ["L9", "x", "1.0 kcal/mol", "sys", "`docs/nonexistent.md`", "claim"],
        ["L9", "x", "1.0 kcal/mol", "sys", "see someone", "claim"],
        ["L9", "x", "1.0 kcal/mol", "", "`docs/limits.md`", "claim"],
    ],
)
def test_checker_catches_broken_measured_rows(row):
    assert check_measured([row]) != []


def test_checker_catches_unmarked_unmeasured_row():
    assert check_unmeasured([["U9", "x", "about 1", "issue #1", "claim"]]) != []
