"""Provider registry (#25): entry-point discovery without importing third-party code.

The fixture packages here are REAL on-disk distributions (a module plus a `.dist-info` directory
with `METADATA` and `entry_points.txt`), found by `importlib.metadata` exactly as an installed
package would be. One test additionally pip-installs a built wheel into a throwaway venv.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

from smeltery.providers import registry
from smeltery.providers.registry import (
    GROUP,
    KINDS,
    ProviderLoadError,
    discover,
    load_all,
    split_entry_name,
)

MODULE = textwrap.dedent(
    """
    LOADED = True

    class RingCount:
        name = "ring-count"
        requires_network = False
        def endpoint_notes(self): return {"rings": "ring count; rank-only"}
        def applicability(self, smiles): return True
        def predict(self, smiles): return {"rings": 1.0}
        def settings(self): return {}
    """
)
BROKEN = "raise RuntimeError('this module cannot be imported')\n"
WRONG_KIND = "class NotAStructureProvider:\n    name = 'x'\n"
NOT_CALLABLE = "VALUE = 42\n"


def dist_files(dist: str, version: str, entries: dict[str, str], modules: dict[str, str]) -> dict[str, str]:
    """Relative path -> text of a minimal distribution: the modules plus its .dist-info."""
    info = f"{dist.replace('-', '_')}-{version}.dist-info"
    ep = f"[{GROUP}]\n" + "".join(f"{k} = {v}\n" for k, v in entries.items())
    files = {f"{m}.py": text for m, text in modules.items()}
    files[f"{info}/METADATA"] = f"Metadata-Version: 2.1\nName: {dist}\nVersion: {version}\n"
    files[f"{info}/entry_points.txt"] = ep
    files[f"{info}/WHEEL"] = "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    return files


def put_on_path(monkeypatch, root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
    monkeypatch.syspath_prepend(str(root))


@pytest.fixture
def fixture_pkg(tmp_path, monkeypatch):
    for m in [m for m in sys.modules if m.startswith("fixture_")]:
        monkeypatch.delitem(sys.modules, m)
    files = dist_files(
        "smeltery-fixture-provider",
        "0.1",
        {"property:ring-count": "fixture_ringcount:RingCount"},
        {"fixture_ringcount": MODULE},
    )
    put_on_path(monkeypatch, tmp_path, files)
    return tmp_path


def test_discovery_imports_nothing_from_the_third_party_package(fixture_pkg):
    assert "fixture_ringcount" not in sys.modules
    disc = discover("property")
    entry = disc.get("property", "ring-count")
    assert (entry.kind, entry.name, entry.value) == ("property", "ring-count", "fixture_ringcount:RingCount")
    assert entry.dist == "smeltery-fixture-provider"
    assert "fixture_ringcount" not in sys.modules, "discovery imported the third-party module"
    assert disc.problems == ()


def test_load_imports_on_request_and_returns_a_factory_not_an_instance(fixture_pkg):
    entry = discover().get("property", "ring-count")
    factory = entry.load()
    assert "fixture_ringcount" in sys.modules
    assert isinstance(factory, type) and factory.__name__ == "RingCount"
    provider = entry.create()
    from smeltery.properties import PropertyProvider

    assert isinstance(provider, PropertyProvider) and provider.predict("C") == {"rings": 1.0}


def test_registry_source_names_no_third_party_package():
    """The module is stdlib-only at top level and mentions no fixture/third-party package."""
    tree = ast.parse(Path(registry.__file__).read_text())
    top = [n for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]
    mods = {a.name.split(".")[0] for n in top if isinstance(n, ast.Import) for a in n.names}
    mods |= {(n.module or "").split(".")[0] for n in top if isinstance(n, ast.ImportFrom) and n.level == 0}
    assert mods <= set(sys.stdlib_module_names), mods - set(sys.stdlib_module_names)
    # Lazy imports inside functions are only smeltery's own (relative) modules.
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            assert n.module.split(".")[0] in set(sys.stdlib_module_names), n.module


def test_broken_wrong_kind_and_malformed_registrations_are_reported_not_raised(tmp_path, monkeypatch):
    for m in [m for m in sys.modules if m.startswith("fixture_")]:
        monkeypatch.delitem(sys.modules, m)
    files = dist_files(
        "smeltery-fixture-bad",
        "0.1",
        {
            "property:ok": "fixture_good:RingCount",
            "property:broken": "fixture_broken:Whatever",
            "structure:wrongkind": "fixture_wrong:NotAStructureProvider",
            "structure:notcallable": "fixture_notcallable:VALUE",
            "structure:missingattr": "fixture_good:NoSuchClass",
            "nonsense": "fixture_good:RingCount",
            "banana:x": "fixture_good:RingCount",
        },
        {
            "fixture_good": MODULE,
            "fixture_broken": BROKEN,
            "fixture_wrong": WRONG_KIND,
            "fixture_notcallable": NOT_CALLABLE,
        },
    )
    put_on_path(monkeypatch, tmp_path, files)

    disc = discover()
    by = {p.entry: p for p in disc.problems}
    assert by["nonsense"].stage == "name" and by["banana:x"].stage == "name"
    assert {e.name for e in disc.entries} >= {"ok", "broken", "wrongkind", "notcallable", "missingattr"}
    assert "fixture_broken" not in sys.modules

    rep = load_all(instantiate=True)
    assert ("property", "ok") in rep.factories  # one bad neighbour does not stop discovery
    probs = {p.entry: p for p in rep.problems}
    assert probs["property:broken"].stage == "import" and "cannot be imported" in probs["property:broken"].reason
    assert probs["structure:wrongkind"].stage == "kind"
    assert probs["structure:notcallable"].stage == "factory"
    assert probs["structure:missingattr"].stage == "import"
    for key in ("property:broken", "structure:wrongkind", "structure:notcallable", "structure:missingattr"):
        assert tuple(key.split(":")) not in rep.factories
    with pytest.raises(ProviderLoadError, match="not a structure provider"):
        disc.get("structure", "wrongkind").create()


def test_the_same_name_from_two_distributions_is_ambiguous_and_neither_is_used(tmp_path, monkeypatch):
    a, b = tmp_path / "a", tmp_path / "b"
    put_on_path(
        monkeypatch, a, dist_files("dist-a", "1", {"property:dup": "fixture_good:RingCount"}, {"fixture_good": MODULE})
    )
    put_on_path(monkeypatch, b, dist_files("dist-b", "1", {"property:dup": "fixture_good:RingCount"}, {}))
    disc = discover("property")
    assert not [e for e in disc.entries if e.name == "dup"]
    (p,) = [p for p in disc.problems if p.stage == "duplicate"]
    assert "dist-a" in p.reason and "dist-b" in p.reason


def test_entry_name_scheme():
    assert split_entry_name("scoring:my-model") == ("scoring", "my-model")
    assert KINDS == ("structure", "scoring", "docking", "property", "potential")
    for bad in ("scoring", "scoring:", ":x", "bogus:x", "scoring: x"):
        with pytest.raises(ValueError):
            split_entry_name(bad)
    with pytest.raises(ValueError):
        discover("bogus")


def test_reference_providers_are_registered_through_the_same_mechanism():
    """smeltery's own providers use entry points too (pyproject.toml), not a private list."""
    disc = discover()
    got = {(e.kind, e.name): e.value for e in disc.entries if e.dist == "smeltery"}
    assert got == {
        ("structure", "pdb"): "smeltery.providers.structure:PdbProvider",
        ("structure", "afdb"): "smeltery.providers.structure:AfdbProvider",
        ("structure", "boltz2"): "smeltery.providers.boltz:BoltzProvider",
        ("property", "rdkit-alerts"): "smeltery.properties:RdkitAlertProvider",
        ("scoring", "vina-score"): "smeltery.scoring:VinaScoreProvider",
        ("docking", "vina"): "smeltery.docking.provider:VinaProvider",
    }
    assert not [p for p in disc.problems if p.entry.split(":")[0] in KINDS and p.stage == "duplicate"]
    rep = load_all()
    for key in got:
        assert key in rep.factories and callable(rep.factories[key])


@pytest.mark.skipif(shutil.which("uv") is None, reason="needs the uv executable to make a venv and pip-install a wheel")
def test_a_pip_installed_wheel_is_discovered_in_a_fresh_venv_without_being_imported(tmp_path):
    """A wheel installed with `uv pip install` into a throwaway venv, discovered by the registry file alone.

    The venv holds nothing but the fixture package and the standard library; the registry module is
    executed from its file (it is stdlib-only), so a pass also shows it needs no other package.
    """
    files = dist_files(
        "smeltery-fixture-wheel",
        "0.2",
        {"property:ring-count": "fixture_wheelmod:RingCount"},
        {"fixture_wheelmod": MODULE},
    )
    wheel = tmp_path / "wheels" / "smeltery_fixture_wheel-0.2-py3-none-any.whl"
    wheel.parent.mkdir()
    with zipfile.ZipFile(wheel, "w") as z:
        for rel, text in files.items():
            z.writestr(rel, text)
        z.writestr("smeltery_fixture_wheel-0.2.dist-info/RECORD", "")
    venv = tmp_path / "venv"
    subprocess.run(["uv", "venv", "--python", sys.executable, str(venv)], check=True, capture_output=True, timeout=300)
    py = venv / "bin" / "python"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(py), "--no-index", str(wheel)],
        check=True,
        capture_output=True,
        timeout=300,
    )
    script = tmp_path / "probe" / "probe.py"
    script.parent.mkdir()
    script.write_text(
        textwrap.dedent(
            f"""
            import importlib.util, sys
            spec = importlib.util.spec_from_file_location("reg", {registry.__file__!r})
            reg = importlib.util.module_from_spec(spec)
            sys.modules["reg"] = reg
            spec.loader.exec_module(reg)
            assert "numpy" not in sys.modules and "smeltery" not in sys.modules
            d = reg.discover()
            e = d.get("property", "ring-count")
            assert "fixture_wheelmod" not in sys.modules, "discovery imported the package"
            assert e.dist == "smeltery-fixture-wheel", e.dist
            f = e.load()
            assert "fixture_wheelmod" in sys.modules and f.__name__ == "RingCount"
            print("OK", e.value)
            """
        )
    )
    out = subprocess.run([str(py), "-I", str(script)], capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "OK fixture_wheelmod:RingCount"
    assert importlib.util.find_spec("fixture_wheelmod") is None  # and it is not in THIS interpreter
