"""The no-ferric mode in conftest is strictly opt-in: with the env var unset it does nothing."""

import os
import sys

import conftest


def test_unset_env_installs_no_stub_and_changes_nothing(monkeypatch):
    monkeypatch.delenv("SMELTERY_NO_FERRIC", raising=False)
    monkeypatch.delitem(sys.modules, "ferric", raising=False)
    before = dict(sys.modules)
    assert conftest.no_ferric_requested() is False
    assert conftest.install_ferric_stub() is False
    assert "ferric" not in sys.modules
    assert dict(sys.modules) == before


def test_only_the_exact_value_1_enables_the_stub(monkeypatch):
    monkeypatch.delitem(sys.modules, "ferric", raising=False)
    for value in ("", "0", "true", "yes"):
        assert conftest.install_ferric_stub({"SMELTERY_NO_FERRIC": value}) is False
        assert "ferric" not in sys.modules


def test_stub_is_not_active_in_a_run_without_the_env_var():
    # If this were True without the env var, needs_ferric tests would skip silently in CI.
    if os.environ.get("SMELTERY_NO_FERRIC") != "1":
        assert conftest.STUB_ACTIVE is False
