"""`Traversable.joinpath` takes one component per call, because Python 3.11 allowed no more.

The defect this pins: `src/smeltery/bench/api/prebuilt.py` called `root().joinpath(name, GALLERY_FILE)`.
`importlib.resources.files("smeltery.prebuilt")` returns a `MultiplexedPath`, and `MultiplexedPath.joinpath`
accepted a single argument until Python 3.12. So on 3.11 every prebuilt route raised

    TypeError: MultiplexedPath.joinpath() takes 2 positional arguments but 3 were given

and answered 500. The landing page's gallery came back empty, which also timed out four browser tests that
start from an example. Nine Python tests and three CI jobs, from one line.

No runtime test can catch this on a 3.12 machine -- the two-argument form works there, and the project's own
venv is 3.12 -- so this reads the source instead. `.joinpath(a, b)` is rejected wherever it is reached from an
`importlib.resources` path, whatever Python is running the test.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"


def _multi_arg_joinpaths(tree: ast.AST) -> list[int]:
    """Lines calling `.joinpath(...)` with more than one positional component."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "joinpath"
        and len(node.args) > 1
    ]


def test_no_resource_path_is_joined_with_several_components_at_once() -> None:
    # Only files that reach for `importlib.resources` can hold a Traversable, so only they are at risk. A
    # `pathlib.Path` accepts many components on every version, and this must not forbid that.
    offenders: list[str] = []
    for py in sorted(SRC.rglob("*.py")):
        text = py.read_text(encoding="utf-8")
        if "importlib" not in text or "resources" not in text:
            continue
        for line in _multi_arg_joinpaths(ast.parse(text, filename=str(py))):
            offenders.append(f"{py.relative_to(SRC.parent)}:{line}")
    assert not offenders, (
        "joinpath() takes one component per call on Python 3.11's MultiplexedPath; "
        "chain the calls instead (.joinpath(a).joinpath(b)): " + ", ".join(offenders)
    )
