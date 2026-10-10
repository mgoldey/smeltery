"""docs/licensing.md is the licence table, and this test is its enforcement (issue #23).

What is enforced:
* every table row has a status in {VERIFIED, INFERRED, UNVERIFIED}, an https URL, a date read (ISO, not in
  the future), a known decision, and, for VERIFIED rows, a quoted operative sentence;
* the three tools the issue names (AlphaFold 3, original RoseTTAFold, HelixFold3) are marked EXCLUDED in the
  table, and every tool the table marks EXCLUDED has a reviewed name pattern below;
* no excluded tool has an adapter in smeltery: no module, class, function, import, entry point, extra or
  dependency whose name matches that tool (AlphaFold DB, AlphaFold 2, RoseTTAFold2 and RoseTTAFold-All-Atom
  are different tools and do NOT match);
* every registered provider (pyproject entry points and installed metadata) has a row in the registered
  provider table, and none points at an EXCLUDED row.

Each guard has a deliberate-mutation test: it feeds the checker a broken input and asserts it is caught.
"""

from __future__ import annotations

import ast
import datetime as dt
import re
import tomllib
from pathlib import Path

import pytest

from smeltery.providers.registry import discover

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "licensing.md"
SRC = ROOT / "src" / "smeltery"
PYPROJECT = ROOT / "pyproject.toml"

STATUSES = {"VERIFIED", "INFERRED", "UNVERIFIED"}
DECISION_WORDS = ("EXCLUDED", "ELIGIBLE", "NOT-CLEARED", "N-A")
COLUMNS = [
    "ID",
    "Covers",
    "Licence",
    "What matters for smeltery",
    "Decision",
    "Status",
    "Read",
    "URL",
    "Operative sentence",
]
ISSUE_EXCLUDED = {"af3", "rf1", "helixfold3"}  # the tools issue #23 names; the doc may not silently demote them

# Reviewed name patterns, per tool slug (the part of a row ID before the last '-').
# `joined`: regex on the lower-cased name with every non-alphanumeric removed ("AlphaFold-3" -> "alphafold3").
# `tokens`: whole tokens (split on non-alphanumerics and camelCase) that name the tool ("AF3Provider" -> {"af3"}).
# Precision matters: AlphaFold DB ("afdb"), AlphaFold 2 and RoseTTAFold2 / RoseTTAFold-All-Atom are other tools.
PATTERNS: dict[str, tuple[re.Pattern[str], frozenset[str]]] = {
    "af3": (re.compile(r"alphafold3"), frozenset({"af3"})),
    "rf1": (re.compile(r"rosettafold(?!2|allatom|aa)|rosettadl"), frozenset({"rf1"})),
    "helixfold3": (re.compile(r"helixfold"), frozenset({"hf3"})),
}


# ---------------------------------------------------------------- parsing


def _table(text: str, header_start: str) -> list[list[str]]:
    lines = text.splitlines()
    start = next((i for i, ln in enumerate(lines) if ln.startswith(header_start)), None)
    if start is None:
        raise AssertionError(f"no table starting {header_start!r}")
    rows = []
    for ln in lines[start + 2 :]:  # skip the header and the |---| line
        if not ln.startswith("|"):
            break
        rows.append([c.strip() for c in ln.strip().strip("|").split(" | ")])
    return rows


def parse_rows(text: str) -> list[dict[str, str]]:
    out = []
    for cells in _table(text, "| ID | Covers |"):
        if len(cells) != len(COLUMNS):
            raise AssertionError(f"row has {len(cells)} cells, expected {len(COLUMNS)}: {cells[:1]}")
        out.append(dict(zip(COLUMNS, cells, strict=True)))
    return out


def parse_registered(text: str) -> dict[str, list[str]]:
    return {c[0]: [r.strip() for r in c[1].split(",")] for c in _table(text, "| Entry | Rows |")}


def slug(row_id: str) -> str:
    return row_id.rsplit("-", 1)[0]


def row_problems(rows: list[dict[str, str]], today: dt.date | None = None) -> list[str]:
    today = today or dt.date.today()
    problems = []
    seen = set()
    for r in rows:
        rid = r["ID"]
        if rid in seen:
            problems.append(f"{rid}: duplicate id")
        seen.add(rid)
        if r["Status"] not in STATUSES:
            problems.append(f"{rid}: status {r['Status']!r} not in {sorted(STATUSES)}")
        if not re.fullmatch(r"https://\S+", r["URL"]):
            problems.append(f"{rid}: URL {r['URL']!r} is not an https URL")
        try:
            d = dt.date.fromisoformat(r["Read"])
            if d > today:
                problems.append(f"{rid}: date read {d} is in the future")
        except ValueError:
            problems.append(f"{rid}: date read {r['Read']!r} is not an ISO date")
        if not r["Decision"].startswith(DECISION_WORDS):
            problems.append(f"{rid}: decision {r['Decision']!r} does not start with one of {DECISION_WORDS}")
        if r["Status"] == "VERIFIED" and not re.search(r'"[^"]{12,}"', r["Operative sentence"]):
            problems.append(f"{rid}: VERIFIED but no quoted operative sentence")
    return problems


def excluded_slugs(rows: list[dict[str, str]]) -> set[str]:
    return {slug(r["ID"]) for r in rows if r["Decision"].startswith("EXCLUDED")}


def exclusion_problems(rows: list[dict[str, str]]) -> list[str]:
    ex = excluded_slugs(rows)
    problems = [
        f"{s}: the issue names this tool as excluded but the table does not" for s in sorted(ISSUE_EXCLUDED - ex)
    ]
    problems += [
        f"{s}: marked EXCLUDED but has no reviewed name pattern in PATTERNS" for s in sorted(ex - PATTERNS.keys())
    ]
    return problems


# ---------------------------------------------------------------- adapter scan


def _tokens(name: str) -> set[str]:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    toks = set()
    for p in parts:
        toks.update(t.lower() for t in re.split(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", p) if t)
    return toks


def matches(slug_: str, name: str) -> bool:
    joined_re, tokens = PATTERNS[slug_]
    return bool(joined_re.search(re.sub(r"[^a-z0-9]", "", name.lower())) or (_tokens(name) & tokens))


def hits(names: list[tuple[str, str]], slugs: set[str]) -> list[str]:
    return [
        f"{where}: {name!r} names excluded tool {s}" for where, name in names for s in sorted(slugs) if matches(s, name)
    ]


def source_names(src: Path) -> list[tuple[str, str]]:
    """Every adapter-shaped name under `src`: file and directory names, classes, functions, imports."""
    out = []
    for p in sorted(src.rglob("*")):
        rel = str(p.relative_to(src))
        if "__pycache__" in rel or p.suffix == ".pyc":
            continue
        out.append((rel, p.stem if p.is_file() else p.name))
        if p.suffix != ".py":
            continue
        for node in ast.walk(ast.parse(p.read_text(), filename=str(p))):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                out.append((f"{rel}:{node.lineno}", node.name))
            elif isinstance(node, ast.Import):
                out += [(f"{rel}:{node.lineno}", a.name) for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                out.append((f"{rel}:{node.lineno}", node.module or ""))
    return out


def pyproject_names(text: str) -> list[tuple[str, str]]:
    cfg = tomllib.loads(text)
    proj = cfg.get("project", {})
    out = []
    for name, value in proj.get("entry-points", {}).get("smeltery.providers", {}).items():
        out += [("pyproject entry point name", name), ("pyproject entry point value", value)]
    for extra, reqs in proj.get("optional-dependencies", {}).items():
        out.append(("pyproject extra", extra))
        out += [("pyproject extra requirement", re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0]) for r in reqs]
    out += [("pyproject dependency", re.split(r"[<>=!~\[; ]", r, maxsplit=1)[0]) for r in proj.get("dependencies", [])]
    return out


def registered_names(text: str) -> list[str]:
    return list(tomllib.loads(text)["project"]["entry-points"]["smeltery.providers"])


def registry_problems(entries: list[str], table: dict[str, list[str]], rows: list[dict[str, str]]) -> list[str]:
    ids = {r["ID"]: r for r in rows}
    problems = [
        f"{e}: registered provider has no row in the registered-provider table of docs/licensing.md"
        for e in entries
        if e not in table
    ]
    for entry, row_ids in table.items():
        for rid in row_ids:
            if rid not in ids:
                problems.append(f"{entry}: names row {rid!r} which is not in the table")
            elif ids[rid]["Decision"].startswith("EXCLUDED"):
                problems.append(f"{entry}: points at EXCLUDED row {rid!r}")
    return problems


# ---------------------------------------------------------------- the real checks


@pytest.fixture(scope="module")
def text() -> str:
    return DOC.read_text()


@pytest.fixture(scope="module")
def rows(text: str) -> list[dict[str, str]]:
    return parse_rows(text)


def test_table_is_complete(rows):
    assert len(rows) >= 25, "the table lost rows"
    assert not row_problems(rows)


def test_required_tools_have_rows(rows):
    have = {slug(r["ID"]) for r in rows}
    for tool in (
        "boltz1",
        "boltz2",
        "chai1",
        "af3",
        "rf1",
        "rfaa",
        "rf2",
        "helixfold3",
        "openfold3",
        "esmfold",
        "af2",
        "afdb",
        "pdb",
    ):
        assert tool in have, f"no row for {tool}"
    # code and weights are separate rows where the issue says they differ
    ids = {r["ID"] for r in rows}
    assert {"chai1-code", "chai1-weights", "af3-code", "af3-weights", "boltz2-code", "boltz2-weights"} <= ids


def test_chai1_is_listed_with_apache_status(rows):
    by_id = {r["ID"]: r for r in rows}
    for rid in ("chai1-code", "chai1-weights"):
        assert by_id[rid]["Licence"] == "Apache-2.0"
        assert by_id[rid]["Decision"] == "ELIGIBLE"


def test_exclusions_are_marked_and_patterned(rows):
    assert not exclusion_problems(rows)


def test_no_excluded_tool_has_an_adapter(rows):
    slugs = excluded_slugs(rows)
    names = source_names(SRC) + pyproject_names(PYPROJECT.read_text())
    assert len(names) > 200, "the scan found almost nothing: is it looking at the right tree?"
    assert not hits(names, slugs)


def test_existing_providers_are_not_false_positives():
    """The reviewed patterns are precise: other tools that share a stem do not match."""
    for name in (
        "AfdbProvider",
        "structure:afdb",
        "AlphaFold DB",
        "alphafold2",
        "AlphaFold",
        "alphafold_db",
        "RoseTTAFold2",
        "RoseTTAFold-All-Atom",
        "RoseTTAFoldAllAtom",
        "rfaa",
        "rf2",
        "boltz",
        "BoltzProvider",
        "structure:boltz2",
        "buffaf30",
        "Hf30",
    ):
        for s in PATTERNS:
            assert not matches(s, name), (s, name)


@pytest.mark.parametrize(
    ("s", "name"),
    [
        ("af3", "AlphaFold3Provider"),
        ("af3", "alphafold_3"),
        ("af3", "AlphaFold-3"),
        ("af3", "structure:af3"),
        ("af3", "AF3Provider"),
        ("af3", "alphafold3.model"),
        ("rf1", "RoseTTAFold"),
        ("rf1", "rosettafold_provider"),
        ("rf1", "RoseTTAFoldProvider"),
        ("rf1", "rosetta-dl"),
        ("helixfold3", "HelixFold3"),
        ("helixfold3", "helixfold-3"),
        ("helixfold3", "structure:helixfold3"),
    ],
)
def test_patterns_catch_the_excluded_tools(s, name):
    assert matches(s, name)


def test_registered_providers_have_licence_rows(text, rows):
    table = parse_registered(text)
    names = registered_names(PYPROJECT.read_text())
    assert len(names) >= 6
    installed = [f"{e.kind}:{e.name}" for e in discover().entries if e.dist == "smeltery"]
    assert not registry_problems(sorted(set(names) | set(installed)), table, rows)
    # `structure:boltz2` is a known registration: a vacuous registry would pass the line above
    assert "structure:boltz2" in names


# ---------------------------------------------------------------- mutation tests: each guard can fail


def _mutate(text: str, row_id: str, column: str, new: str) -> str:
    out = []
    for ln in text.splitlines():
        if ln.startswith(f"| {row_id} | "):
            cells = ln.strip().strip("|").split(" | ")
            cells = [c.strip() for c in cells]
            cells[COLUMNS.index(column)] = new
            ln = "| " + " | ".join(cells) + " |"
        out.append(ln)
    return "\n".join(out) + "\n"


@pytest.mark.parametrize(
    ("row_id", "column", "new", "expect"),
    [
        ("chai1-weights", "Status", "PROBABLY", "status"),
        ("chai1-weights", "Status", "", "status"),
        ("chai1-weights", "URL", "", "URL"),
        ("chai1-weights", "URL", "http://insecure", "URL"),
        ("chai1-weights", "Read", "", "date"),
        ("chai1-weights", "Read", "10/10/2026", "date"),
        ("chai1-weights", "Read", "2999-01-01", "future"),
        ("chai1-weights", "Operative sentence", "", "quoted"),
        ("chai1-weights", "Operative sentence", "it is Apache", "quoted"),
        ("chai1-weights", "Decision", "MAYBE", "decision"),
    ],
)
def test_mutation_incomplete_row_is_caught(text, row_id, column, new, expect):
    probs = row_problems(parse_rows(_mutate(text, row_id, column, new)))
    assert any(row_id in p and expect in p for p in probs), probs


def test_mutation_duplicate_id_is_caught(rows):
    assert any("duplicate" in p for p in row_problems(rows + [dict(rows[0])]))


def test_mutation_unverified_row_needs_no_quote(text):
    # UNVERIFIED rows legitimately have no quote: the quote guard must not fire for them
    probs = row_problems(parse_rows(_mutate(text, "chai1-weights", "Status", "UNVERIFIED")))
    assert not any("quoted" in p for p in probs)


@pytest.mark.parametrize(
    "tool_row", ["af3-weights", "af3-output", "rf1-weights", "helixfold3-code", "helixfold3-weights"]
)
def test_mutation_demoting_an_excluded_tool_is_caught(text, tool_row):
    mutated = text
    for rid in [r["ID"] for r in parse_rows(text) if slug(r["ID"]) == slug(tool_row)]:
        mutated = _mutate(mutated, rid, "Decision", "ELIGIBLE")
    assert any(slug(tool_row) in p for p in exclusion_problems(parse_rows(mutated)))


def test_mutation_new_excluded_tool_without_patterns_is_caught(text):
    # a table that excludes a tool the test has no reviewed pattern for must be refused
    mutated = _mutate(text, "esmfold-weights", "Decision", "EXCLUDED")
    assert any("esmfold" in p and "PATTERNS" in p for p in exclusion_problems(parse_rows(mutated)))


@pytest.mark.parametrize(
    ("relpath", "content"),
    [
        ("providers/alphafold3.py", "class P:\n    pass\n"),  # module name
        ("providers/ok.py", "class AlphaFold3Provider:\n    pass\n"),  # class name
        ("providers/ok.py", "def make_af3():\n    pass\n"),  # function name (token af3)
        ("providers/ok.py", "import alphafold3\n"),  # import
        ("providers/ok.py", "from alphafold3.model import x\n"),  # from-import
        ("providers/helixfold3/__init__.py", ""),  # package directory
        ("providers/rosettafold.py", ""),  # original RoseTTAFold
        ("providers/ok.py", "class RoseTTAFoldProvider:\n    pass\n"),
        ("providers/ok.py", "import helixfold\n"),
    ],
)
def test_mutation_adapter_in_source_is_caught(tmp_path, rows, relpath, content):
    src = tmp_path / "smeltery"
    (src / "providers").mkdir(parents=True)
    (src / "providers" / "fine.py").write_text("class AfdbProvider:\n    pass\n")
    f = src / relpath
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(content)
    assert hits(source_names(src), excluded_slugs(rows)), relpath


def test_scan_of_a_clean_tree_is_empty(tmp_path, rows):
    src = tmp_path / "smeltery"
    src.mkdir()
    (src / "a.py").write_text(
        "class AfdbProvider:\n    pass\nimport alphafold\nfrom smeltery.providers.structure import X\n"
    )
    (src / "rfaa.py").write_text("class RoseTTAFold2Provider:\n    pass\n")
    assert not hits(source_names(src), excluded_slugs(rows))


def test_mutation_excluded_tool_in_pyproject_is_caught(rows):
    base = PYPROJECT.read_text()
    slugs = excluded_slugs(rows)
    assert not hits(pyproject_names(base), slugs)
    entry = base.replace('"structure:boltz2" =', '"structure:af3" = "smeltery.providers.af:P"\n"structure:boltz2" =')
    assert hits(pyproject_names(entry), slugs)
    value = base.replace("smeltery.providers.boltz:BoltzProvider", "smeltery.providers.helixfold3:H")
    assert hits(pyproject_names(value), slugs)
    extra = base.replace(
        "[project.optional-dependencies]", '[project.optional-dependencies]\nhelixfold3 = ["paddlepaddle"]'
    )
    assert hits(pyproject_names(extra), slugs)
    dep = base.replace('"numpy>=1.26",', '"numpy>=1.26",\n    "alphafold3>=3.0",', 1)
    assert hits(pyproject_names(dep), slugs)


def test_mutation_unlisted_provider_is_caught(text, rows):
    table = parse_registered(text)
    names = registered_names(PYPROJECT.read_text())
    assert not registry_problems(names, table, rows)
    assert any("structure:chai1" in p for p in registry_problems(names + ["structure:chai1"], table, rows))
    gone = {k: v for k, v in table.items() if k != "structure:boltz2"}
    assert any("structure:boltz2" in p for p in registry_problems(names, gone, rows))


def test_mutation_provider_row_must_exist_and_not_be_excluded(text, rows):
    table = parse_registered(text)
    names = list(table)
    assert any(
        "no-such-row" in p for p in registry_problems(names, {**table, "structure:boltz2": ["no-such-row"]}, rows)
    )
    assert any("EXCLUDED" in p for p in registry_problems(names, {**table, "structure:boltz2": ["af3-weights"]}, rows))
