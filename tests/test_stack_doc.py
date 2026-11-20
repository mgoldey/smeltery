"""docs/stack.md is the stack-overview page mirrored in ferric, smeltery and attestation.

CI sees one checkout, so it cannot compare the three copies. It pins the version line
instead: the page says it must be edited in all three repositories together, and a change
made here alone fails this test until the constant (and the other repos' constants) move.
It must also be reachable from the README.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STACK_HEADER = "<!-- stack-overview v1 2026-11-20 -->"


def test_the_stack_overview_carries_the_pinned_version_line():
    page = ROOT / "docs" / "stack.md"
    assert page.is_file(), "docs/stack.md is missing"
    assert page.read_text().splitlines()[0] == STACK_HEADER, (
        "docs/stack.md is mirrored in ferric, smeltery and attestation: update all three "
        "and bump STACK_HEADER in each repository's test"
    )


def test_the_readme_links_the_stack_overview():
    assert "](docs/stack.md)" in (ROOT / "README.md").read_text()
