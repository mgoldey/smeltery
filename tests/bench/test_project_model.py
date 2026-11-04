import pathlib
import re

import pytest

from smeltery.project import Proposal


def test_decisions_does_not_import_the_eln_layer():
    src = pathlib.Path("src/smeltery/decisions.py").read_text()
    assert not re.search(r"from\s+\.project\s+import", src)


def test_a_proposal_carries_who_why_and_which_run():
    p = Proposal(
        id="p1",
        run="abc123",
        smiles="Cc1ccccc1",
        author="bench-chemist",
        reason="synthesis",
        note="try the 3-F",
        created_at="2026-11-03T00:00:00Z",
    )
    assert p.validate() is p


def test_a_proposal_with_an_unknown_reason_is_refused():
    with pytest.raises(ValueError):
        Proposal(
            id="p1", run="r", smiles="C", author="a", reason="because", note="", created_at="2026-11-03T00:00:00Z"
        ).validate()


def test_a_proposal_has_no_way_to_launch_compute():
    forbidden = ("submit", "launch", "run", "execute", "spawn")
    assert [n for n in dir(Proposal) if n.lower().startswith(forbidden)] == []
