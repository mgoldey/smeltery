"""Migration rules (issue #8), enforced. Mirrors ferric's
`tools/tests/test_library_experiment_boundary.py`.

Library code must not import `experiments` or name a campaign target in
EXECUTABLE code. Prose (docstrings, comments) may cite campaign measurements as
provenance; a citation is not a dependency. Each detector is a function over
source text so a reachability test can prove it fires on a violation.
"""
from __future__ import annotations

import ast
import os
import pathlib
import re
import subprocess
import tomllib

REPO = pathlib.Path(__file__).resolve().parents[1]
SRC = REPO / "src" / "smeltery"

CAMPAIGN_TOKENS = ("danuglipron", "DANUGLIPRON", "7LCJ", "GLP1R", "PF-06882961")

TOOLS_PACKAGES = (
    "active_site", "campaign", "docking", "isomers", "morph",
    "pipeline", "structure", "tox", "viz",
)
PINNED_SYMBOLS = (
    "Molecule", "BasisSet", "run_rhf", "run_dft", "run_optimize",
    "run_optimize_qmmm", "QmmmSystem", "MmTopology", "ConformerEnsemble",
    "hirshfeld_charges", "hirshfeld_polarizability", "lowdin_charges",
    "run_saddle", "run_irc",
)


def _modules():
    for f in sorted(SRC.rglob("*.py")):
        if "__pycache__" not in f.parts:
            yield f


def experiments_imports(src: str) -> list[tuple[int, str]]:
    out = []
    for n in ast.walk(ast.parse(src)):
        mods = []
        if isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            mods.append(n.module)
        elif isinstance(n, ast.Import):
            mods += [a.name for a in n.names]
        out += [(n.lineno, m) for m in mods if m.split(".")[0] == "experiments"]
    return out


def campaign_names_in_code(src: str) -> list[tuple[int, str]]:
    tree = ast.parse(src)
    doc_lines: set[int] = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and \
                    isinstance(getattr(b[0], "value", None), ast.Constant) and \
                    isinstance(b[0].value.value, str):
                doc_lines.update(range(b[0].lineno, (b[0].end_lineno or b[0].lineno) + 1))
    out = []
    for i, line in enumerate(src.splitlines(), 1):
        if i in doc_lines or line.strip().startswith("#"):
            continue
        out += [(i, t) for t in CAMPAIGN_TOKENS if t in line]
    return out


def find_symlinks(root: pathlib.Path) -> list[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", ".venv", ".claude")]
        for name in dirnames + filenames:
            p = pathlib.Path(dirpath) / name
            if p.is_symlink():
                found.append(str(p.relative_to(root)))
    return sorted(found)


def ferric_declaration_problems(pyproject: dict) -> list[str]:
    problems = []
    deps = pyproject.get("project", {}).get("dependencies", [])
    reqs = [d for d in deps if re.match(r"\s*ferric\b(?![-_.\w])", d)]
    if not reqs:
        problems.append("ferric is not in [project].dependencies")
    for r in reqs:
        if "@" in r:
            problems.append(f"ferric declared by direct URL/path: {r!r}")
        elif not re.search(r"(==|>=|~=|===|<=|>|<)\s*\d", r):
            problems.append(f"ferric has no version specifier: {r!r}")
    src = pyproject.get("tool", {}).get("uv", {}).get("sources", {}).get("ferric")
    if src is not None:
        if not isinstance(src, dict):
            problems.append("[tool.uv.sources].ferric must be a table")
        elif "git" in src:
            if not re.fullmatch(r"[0-9a-f]{40}", str(src.get("rev", ""))):
                problems.append("git source for ferric needs a full 40-hex rev")
        elif "path" not in src:
            problems.append("[tool.uv.sources].ferric needs git+rev or path")
    return problems


# ---- acceptance 1: no experiments import, no campaign names in code ----------

def test_no_library_module_imports_experiments():
    offenders = [
        f"{f.relative_to(REPO)}:{ln} imports {m}"
        for f in _modules() for ln, m in experiments_imports(f.read_text())
    ]
    assert not offenders, "src/smeltery must not import experiments:\n  " + "\n  ".join(offenders)


def test_no_campaign_names_in_library_executable_code():
    offenders = [
        f"{f.relative_to(REPO)}:{ln}: {tok!r}"
        for f in _modules() for ln, tok in campaign_names_in_code(f.read_text())
    ]
    assert not offenders, "campaign names in src/smeltery code:\n  " + "\n  ".join(offenders)


def test_library_scan_finds_modules():
    assert len(list(_modules())) >= 5, "scan is vacuous: no modules found"


def test_boundary_detectors_can_fail():
    assert experiments_imports("from experiments.x.design import y\n") == [(1, "experiments.x.design")]
    assert experiments_imports("import experiments\n") == [(1, "experiments")]
    assert experiments_imports("import numpy\nfrom smeltery import x\n") == []
    assert campaign_names_in_code('TARGET = "7LCJ"\n') == [(1, "7LCJ")]
    # prose is allowed, code is not
    assert campaign_names_in_code('"""measured on danuglipron"""\n# GLP1R\n') == []
    assert campaign_names_in_code('def f():\n    """danuglipron"""\n    x = "danuglipron"\n') == [(3, "danuglipron")]


# ---- acceptance 2: no symlinks -----------------------------------------------

def test_repo_contains_no_symlinks(tmp_path):
    assert find_symlinks(REPO) == []
    try:
        ls = subprocess.run(["git", "ls-files", "-s"], cwd=REPO, capture_output=True,
                            text=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return  # no git available: the walk above is the check
    tracked = [l.split("\t", 1)[1] for l in ls.splitlines() if l.startswith("120000")]
    assert not tracked, f"tracked symlinks: {tracked}"


def test_symlink_detector_can_fail(tmp_path):
    (tmp_path / "real.txt").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "link.txt").symlink_to(tmp_path / "real.txt")
    (tmp_path / "dirlink").symlink_to(tmp_path / "sub")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "ignored").symlink_to(tmp_path / "real.txt")
    assert find_symlinks(tmp_path) == ["dirlink", "sub/link.txt"]


# ---- acceptance 3: docs/migration.md ------------------------------------------

def test_migration_doc_lists_every_package_and_pinned_symbol():
    doc = (REPO / "docs" / "migration.md").read_text()
    missing = [p for p in TOOLS_PACKAGES if f"`{p}`" not in doc]
    assert not missing, f"packages missing from docs/migration.md: {missing}"
    missing = [s for s in PINNED_SYMBOLS if f"`{s}`" not in doc]
    assert not missing, f"pinned symbols missing from docs/migration.md: {missing}"


def test_migration_doc_has_a_move_order():
    doc = (REPO / "docs" / "migration.md").read_text()
    rows = {p: re.search(rf"^\|\s*(\d+)\s*\|\s*`{p}`", doc, re.M) for p in TOOLS_PACKAGES}
    assert all(rows.values()), f"no numbered order row for: {[p for p, m in rows.items() if not m]}"
    order = {p: int(m.group(1)) for p, m in rows.items()}
    # dependencies stated in issues #9-#12: docking after structure
    assert order["docking"] > order["structure"]


# ---- acceptance 4: pyproject declares ferric explicitly ------------------------

def test_pyproject_declares_ferric_explicitly():
    py = tomllib.loads((REPO / "pyproject.toml").read_text())
    assert ferric_declaration_problems(py) == []


def test_ferric_declaration_check_can_fail():
    ok = {"project": {"dependencies": ["ferric>=0.1.0rc6"]}}
    assert ferric_declaration_problems(ok) == []
    assert ferric_declaration_problems({"project": {"dependencies": ["numpy"]}})
    assert ferric_declaration_problems({"project": {"dependencies": ["ferric"]}})
    assert ferric_declaration_problems({"project": {"dependencies": ["ferric @ file:///x"]}})
    bad_git = {**ok, "tool": {"uv": {"sources": {"ferric": {"git": "u", "rev": "main"}}}}}
    assert ferric_declaration_problems(bad_git)
    assert ferric_declaration_problems(
        {**ok, "tool": {"uv": {"sources": {"ferric": {"workspace": True}}}}})
