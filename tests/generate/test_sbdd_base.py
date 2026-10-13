"""The generator protocol's result types."""

import pytest

from smeltery import Candidate
from smeltery.generate.base import REASONS, GenerationResult, GeneratorError, Rejection


def test_a_rejection_reason_must_come_from_the_closed_set():
    with pytest.raises(ValueError, match="unknown rejection reason"):
        Rejection(index=0, reason="because")
    assert Rejection(index=3, reason="duplicate").detail == ""
    assert all(isinstance(r, str) for r in REASONS)


def test_the_result_accounts_for_every_raw_output():
    result = GenerationResult(
        candidates=[Candidate("a", "C")],
        rejected=[Rejection(0, "duplicate"), Rejection(1, "duplicate"), Rejection(2, "disconnected", "2 fragments")],
        provenance={},
    )
    assert result.n_raw == 4
    assert result.rejection_counts() == {"duplicate": 2, "disconnected": 1}


def test_a_failed_run_is_a_runtime_error():
    assert issubclass(GeneratorError, RuntimeError)
