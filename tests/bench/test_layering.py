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
