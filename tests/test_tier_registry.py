"""The tier registry finds every tier, and a tier in an unlisted module fails a test instead of going missing."""

import ast
import sys
import types
from pathlib import Path

import pytest

from smeltery import tier_registry
from smeltery.tier_registry import TIER_METHODS, registered_tiers, satisfies_tier_protocol

SRC = Path(__file__).resolve().parent.parent / "src" / "smeltery"

EXPECTED = {
    "paired_poses": "smeltery.tiers",
    "field_interaction": "smeltery.tiers",
    "surface_esp": "smeltery.tiers",
    "gfn2": "smeltery.tiers",
    "forcefield": "smeltery.tiers",
    "docking": "smeltery.docking.tier",
    "rescoring": "smeltery.scoring",
}


def tier_like_classes_in_source() -> set[tuple[str, str]]:
    """(module, class) for every class under src/smeltery that has the whole tier surface, found without importing."""
    found = set()
    for path in sorted(SRC.rglob("*.py")):
        module = ".".join(("smeltery", *path.relative_to(SRC).with_suffix("").parts)).removesuffix(".__init__")
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            has_name = any(
                (isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "name" and n.value is not None)
                or (isinstance(n, ast.Assign) and any(getattr(t, "id", None) == "name" for t in n.targets))
                for n in node.body
            )
            if has_name and set(TIER_METHODS) <= methods:
                found.add((module, node.name))
    return found


def unregistered(modules) -> list[tuple[str, str]]:
    return sorted((m, c) for m, c in tier_like_classes_in_source() if m not in modules)


def test_the_registry_holds_the_known_tiers():
    """A subset check: a tier added later is registered by introspection and must not break this test."""
    tiers = registered_tiers()
    assert EXPECTED.items() <= {name: cls.__module__ for name, cls in tiers.items()}.items()
    assert all(cls.name == name for name, cls in tiers.items())


def test_non_tiers_are_not_registered():
    from smeltery.gates import PoseReport
    from smeltery.model import Candidate
    from smeltery.scoring import ScoringProvider, VinaScoreProvider
    from smeltery.tiers import Tier

    for cls in (Tier, Candidate, PoseReport, ScoringProvider, VinaScoreProvider):
        assert not satisfies_tier_protocol(cls), cls
        assert cls not in registered_tiers().values()


def test_every_tier_like_class_in_the_source_is_registered():
    missing = unregistered(tier_registry.TIER_MODULES)
    assert not missing, (
        f"{missing} satisfy the tier protocol but live in a module that smeltery.tier_registry.TIER_MODULES "
        "does not list, so they are not registered and have no docs page. Add the module to TIER_MODULES."
    )


def test_the_source_scan_can_fail():
    """Drop a module from the list and the scan names it: the guard above is not vacuous."""
    without_docking = tuple(m for m in tier_registry.TIER_MODULES if m != "smeltery.docking.tier")
    assert unregistered(without_docking) == [("smeltery.docking.tier", "Docking")]


def test_a_tier_added_to_a_listed_module_is_registered_without_any_edit_to_the_registry(monkeypatch):
    mod = types.ModuleType("smeltery_fake_tier_module")

    class Newcomer:
        name = "newcomer"

        def run(self, candidates, ctx): ...
        def estimate_cost(self, candidates): ...
        def settings(self): ...
        def produces(self): ...
        def systematic_floor(self, quantity): ...

    Newcomer.__module__ = mod.__name__
    mod.Newcomer = Newcomer
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.setattr(tier_registry, "TIER_MODULES", (*tier_registry.TIER_MODULES, mod.__name__))
    assert registered_tiers()["newcomer"] is Newcomer


def test_two_tiers_with_one_name_are_an_error(monkeypatch):
    mod = types.ModuleType("smeltery_fake_dup_module")

    class Twin:
        name = "gfn2"

        def run(self, candidates, ctx): ...
        def estimate_cost(self, candidates): ...
        def settings(self): ...
        def produces(self): ...
        def systematic_floor(self, quantity): ...

    Twin.__module__ = mod.__name__
    mod.Twin = Twin
    monkeypatch.setitem(sys.modules, mod.__name__, mod)
    monkeypatch.setattr(tier_registry, "TIER_MODULES", (*tier_registry.TIER_MODULES, mod.__name__))
    with pytest.raises(ValueError, match="two tiers are named 'gfn2'"):
        registered_tiers()
