import ast
import pathlib
import re


def test_getpass_appears_only_in_the_identity_adapter():
    root = pathlib.Path("src/smeltery/bench")
    hits = sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*.py") if re.search(r"\bgetpass\b", p.read_text())
    )
    assert hits == ["adapters/identity.py"]


def test_core_modules_do_not_import_the_bench_layer():
    import pathlib
    import re

    core = [p for p in pathlib.Path("src/smeltery").glob("*.py")]
    bad = [p.name for p in core if re.search(r"^\s*(from|import)\s+smeltery\.bench", p.read_text(), re.M)]
    assert bad == []


def test_no_sqlite_or_temporal_outside_the_adapters():
    import pathlib
    import re

    root = pathlib.Path("src/smeltery")
    bad = []
    for p in root.rglob("*.py"):
        # tasks.py and temporal_run.py are the CLI's own Temporal adapters (shell tasks; pipeline runs);
        # backup.py is the CLI's sqlite snapshot for `smeltery backup`
        if "bench/adapters" in p.as_posix() or p.name in ("tasks.py", "temporal_run.py", "backup.py"):
            continue
        if re.search(r"^\s*import\s+sqlite3|^\s*(from|import)\s+temporalio", p.read_text(), re.M):
            bad.append(p.relative_to(root).as_posix())
    assert bad == []


def test_ports_declare_no_implementations():
    import pathlib
    import re

    for p in pathlib.Path("src/smeltery/bench/ports").glob("*.py"):
        src = p.read_text()
        if p.name == "__init__.py":
            continue
        assert "Protocol" in src, p.name
        assert not re.search(r"^\s*import\s+sqlite3", src, re.M), p.name


def test_decisions_does_not_import_project():
    import pathlib
    import re

    src = pathlib.Path("src/smeltery/decisions.py").read_text()
    assert not re.search(r"^\s*(from|import)\s+smeltery\.project", src, re.M)


# ---------------------------------------------------------------------------------------------------- the onion
#
# Imports are read with `ast`, so a function-local import counts as much as a module-level one, a relative import is
# resolved to the module it names, and a name only mentioned in a docstring does not.

SRC = pathlib.Path("src")
BENCH = SRC / "smeltery" / "bench"


def _module(p: pathlib.Path) -> str:
    parts = list(p.relative_to(SRC).with_suffix("").parts)
    return ".".join(parts[:-1] if parts[-1] == "__init__" else parts)


def _nodes(tree: ast.Module, module_level: bool):
    """Every node; with `module_level`, none inside a function body (what runs on import, class bodies included)."""
    stack: list[ast.AST] = list(tree.body)
    while stack:
        n = stack.pop()
        yield n
        if module_level and isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        stack.extend(ast.iter_child_nodes(n))


def _imports(p: pathlib.Path, module_level: bool = False):
    """(module, names, line) per import in `p`. `from a import b` gives module `a` and names `["b"]`; a relative
    import is resolved against `p`'s package."""
    own = _module(p)
    package = own if p.name == "__init__.py" else own.rpartition(".")[0]
    for n in _nodes(ast.parse(p.read_text()), module_level):
        if isinstance(n, ast.Import):
            for a in n.names:
                yield a.name, [], n.lineno
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                base = package.split(".")[: len(package.split(".")) - (n.level - 1)]
                name = ".".join(base + ([n.module] if n.module else []))
            else:
                name = n.module or ""
            yield name, [a.name for a in n.names], n.lineno


def _reaches(module: str, names: list[str], banned: tuple[str, ...]) -> bool:
    """The import is of a banned package or one of its modules (`from smeltery.bench import adapters` too)."""
    targets = [module, *(f"{module}.{n}" for n in names)]
    return any(t == b or t.startswith(b + ".") for t in targets for b in banned)


def _violations(layer: str, banned: tuple[str, ...]) -> list[str]:
    return sorted(
        f"{p.relative_to(BENCH).as_posix()}:{line} imports {module}"
        for p in (BENCH / layer).rglob("*.py")
        for module, names, line in _imports(p)
        if _reaches(module, names, banned)
    )


WEB = ("fastapi", "starlette")


def test_use_cases_import_no_adapter_router_or_web_framework():
    assert _violations("use_cases", ("smeltery.bench.adapters", "smeltery.bench.api", *WEB)) == []


def test_ports_import_no_adapter_router_or_use_case():
    assert _violations("ports", ("smeltery.bench.adapters", "smeltery.bench.api", "smeltery.bench.use_cases")) == []


def test_adapters_import_no_router_use_case_or_web_framework():
    assert _violations("adapters", ("smeltery.bench.api", "smeltery.bench.use_cases", *WEB)) == []


def test_routers_reach_adapters_only_through_the_ports_on_the_app():
    """`api/` reads `app.state.<port>`; only `app.py` (the composition root) picks adapters."""
    assert _violations("api", ("smeltery.bench.adapters",)) == []


def test_no_router_imports_another_routers_private_names():
    """What two routers share lives in `api/common.py` under a public name."""
    bad = sorted(
        f"{p.name}:{line} imports {n} from {module}"
        for p in (BENCH / "api").glob("*.py")
        for module, names, line in _imports(p)
        if module.startswith("smeltery.bench.api") and module != _module(p)
        for n in names
        if n.startswith("_")
    )
    assert bad == []


def _calls_path_home(p: pathlib.Path) -> bool:
    for n in ast.walk(ast.parse(p.read_text())):
        if isinstance(n, ast.Attribute) and n.attr == "home":
            v = n.value
            if (isinstance(v, ast.Name) and v.id == "Path") or (isinstance(v, ast.Attribute) and v.attr == "Path"):
                return True
    return False


def test_path_home_is_read_only_by_the_adapters():
    """Where Bench keeps its work when given no ROOT is `adapters/workspace_root.py`'s decision alone."""
    bad = sorted(
        p.relative_to(BENCH).as_posix()
        for p in BENCH.rglob("*.py")
        if not p.relative_to(BENCH).as_posix().startswith(("adapters/", "web/")) and _calls_path_home(p)
    )
    assert bad == []


#: Core modules that still import the delivery layers on import, each with why. A new one fails the test below.
CORE_IMPORTS_DELIVERY = {
    ("__main__.py", "smeltery.cli"): "`python -m smeltery` is the CLI's entry point",
}


def test_core_modules_import_neither_the_gui_nor_the_cli_on_import():
    """Everything under src/smeltery/ outside bench/ and gui/ is core: the CLI and the GUI are built on it, so it
    must not need either to be imported. A function-local import (a CLI hook reached at run time) is not counted."""
    root = SRC / "smeltery"
    found = {
        (p.relative_to(root).as_posix(), banned)
        for p in root.rglob("*.py")
        if p.relative_to(root).parts[0] not in ("bench", "gui")
        for module, names, _ in _imports(p, module_level=True)
        for banned in ("smeltery.cli", "smeltery.gui")
        if _reaches(module, names, (banned,))
    }
    assert found == set(CORE_IMPORTS_DELIVERY), sorted(found ^ set(CORE_IMPORTS_DELIVERY))


#: Modules that may import `smeltery.cli` at all, each with why. The plan/run use case is `smeltery.planning` and
#: `smeltery.run_outputs`; a module that executes a run imports those, never the command line.
IMPORTS_CLI = {
    "__main__.py": "`python -m smeltery` is the CLI's entry point",
}


def test_only_the_entry_point_imports_the_cli():
    """Function-local imports count: a Temporal worker or a FastAPI handler that reached the CLI module at run time
    would put argparse and `print` back on the path they execute."""
    root = SRC / "smeltery"
    found = {
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        if p.name != "cli.py" or p.parent != root
        for module, names, _ in _imports(p)
        if _reaches(module, names, ("smeltery.cli",))
    }
    assert found == set(IMPORTS_CLI), sorted(found ^ set(IMPORTS_CLI))
