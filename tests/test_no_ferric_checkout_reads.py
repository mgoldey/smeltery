"""No file under smeltery reads a path inside a ferric checkout (issue #13).

smeltery depends on ferric as an installed package, never on its source tree: a test
or script that opens `$FERRIC_SRC/crates/...` makes the suite depend on a checkout
smeltery does not carry, and passes or skips depending on what is on the machine.

What is checked, in Python files under src/, tests/, scripts/, examples/ and experiments/: string
literals in CODE (docstrings and comments are prose and may cite ferric paths) that
look like a ferric repo path, or an environment variable naming a ferric checkout.
Shell and CI files are scanned for the same on non-comment lines. This file is
excluded from its own scan because it must spell the patterns out.
"""

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELF = Path(__file__).resolve()

# A path into ferric's repo layout, or an env var that points at a ferric checkout.
FERRIC_PATH = re.compile(r"(?:^|/)(?:crates/ferric|tools/(?:active_site|pipeline|docking|viz)|site/src|wiki/)")
FERRIC_ENV = re.compile(r"FERRIC_(?:SRC|SOURCE|DIR|ROOT|PATH|CHECKOUT|REPO)\b")
CHECKOUT_DIR = re.compile(r"qc/ferric\b")


def _is_docstring(node: ast.AST, parent: ast.AST | None) -> bool:
    return (
        isinstance(parent, ast.Expr)
        and isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and parent.value is node
    )


def _code_strings(path: Path):
    tree = ast.parse(path.read_text(), filename=str(path))
    for parent in ast.walk(tree):
        for node in ast.iter_child_nodes(parent):
            if isinstance(node, ast.Expr) and _is_docstring(node.value, node):
                continue  # a bare string expression statement: a docstring or a no-op, never a path
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if isinstance(parent, ast.Expr):
                    continue
                yield node.lineno, node.value


def _offences() -> list[str]:
    found = []
    for sub in ("src", "tests", "scripts", "examples", "experiments"):
        for path in sorted((ROOT / sub).rglob("*.py")):
            if path.resolve() == SELF or ".venv" in path.parts:
                continue
            for lineno, value in _code_strings(path):
                if FERRIC_PATH.search(value) or FERRIC_ENV.search(value) or CHECKOUT_DIR.search(value):
                    found.append(f"{path.relative_to(ROOT)}:{lineno}: {value[:80]!r}")
    shell = [*(ROOT / "scripts").glob("*.sh"), *(ROOT / ".github").rglob("*.y*ml")]
    for path in sorted(shell):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            code = line.split("#", 1)[0]
            if FERRIC_PATH.search(code) or FERRIC_ENV.search(code) or CHECKOUT_DIR.search(code):
                found.append(f"{path.relative_to(ROOT)}:{lineno}: {line.strip()[:80]!r}")
    return found


def test_nothing_in_smeltery_reads_a_path_inside_a_ferric_checkout():
    assert _offences() == []


def test_the_scan_sees_what_it_claims_to(tmp_path):
    """A scan nobody has seen flag something is an assumption: it flags code strings, not docstrings."""
    bad = tmp_path / "bad.py"
    bad.write_text(
        '"""Mentions crates/ferric-cli/src/config.rs in prose: fine."""\n'
        "import os\n"
        'root = os.environ.get("FERRIC_SRC")\n'
        'p = root + "/crates/ferric-cli/src/config.rs"\n'
        "def f():\n"
        '    """Also prose: tools/active_site."""\n'
    )
    flagged = [(n, v) for n, v in _code_strings(bad) if FERRIC_PATH.search(v) or FERRIC_ENV.search(v)]
    assert [v for _, v in flagged] == ["FERRIC_SRC", "/crates/ferric-cli/src/config.rs"]
