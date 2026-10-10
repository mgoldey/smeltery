"""docs/tiers/: one page per registered tier, and every number on a page has a source that this test reads.

What is enforced, and where a page is wrong it fails with the page and the line:
* every registered tier has docs/tiers/<name>.md, and every page names a registered tier;
* each page has the required sections;
* the Cost line is exactly UNMEASURED, or a number with a system, a basis and a source whose file contains the number;
* each Floor line is UNMEASURED exactly when the code's `systematic_floor` is None, otherwise the code's number
  with a source;
* constants that are also in code (4.07, 0.825) cannot drift from it;
* settings keys equal `settings()` keys, and pinned defaults equal the code's;
* gates the tier's own source applies are listed on its page, and every gate id exists in the index catalogue;
* the Evidence table in the index equals what the pages and the code say;
* no page narrates revision history.
"""

import ast
import json
import re
from pathlib import Path

import numpy as np
import pytest

from smeltery import Candidate, Pose
from smeltery.cost import rhf_calibration
from smeltery.pocket import DDE_NOISE_FLOOR_KCAL_MOL
from smeltery.scoring import VinaScoreProvider
from smeltery.tier_registry import registered_tiers
from smeltery.tiers import XTB_VS_DFT_MAE_KCAL

ROOT = Path(__file__).resolve().parent.parent
TIERS_DIR = ROOT / "docs" / "tiers"
INDEX = TIERS_DIR / "index.md"
MEASUREMENTS = json.loads((TIERS_DIR / "measurements.json").read_text())

SECTIONS = ["Computes", "Native settings", "Cost", "Gates", "Systematic floor", "Not licensed to claim", "Evidence"]

#: Settings whose value depends on the machine or on a run, so a page cannot pin them.
UNPINNED_SETTINGS = {"rdkit", "xtb_path", "xtb_version", "field", "provider", "provider_settings", "score_unit"}

#: Source token in a tier's own code -> the gate id its page must list. Derived from the code, not from the page.
GATE_TOKENS = {"require_passing_poses(": "G1", "NoCommonCoreError": "G2"}

# Wording that narrates change over time. A page states current facts; none of these has a use in one.
# Each entry is a regex over lower-cased text, with the reason it is on the list.
REVISION_PATTERNS = {
    r"\bpreviously\b": "says there was an earlier state",
    r"\bformerly\b": "says there was an earlier state",
    r"\bused to\b": "says there was an earlier state",
    r"\bno longer\b": "says something stopped being true",
    r"\bnow\b": "marks a change ('now supports'); a current fact needs no 'now'",
    r"\b(was|were|has been|have been|had been) (changed|added|removed|fixed|replaced|renamed)\b": "passive history",
    r"\bin (version|release|v)\s*\d": "ties a statement to a release",
    r"\bsince (version|release|v)\s*\d": "ties a statement to a release",
    r"\b(newly|recently|originally)\b": "time-relative adverb",
    r"\b(older|earlier|former|prior) (version|release|behaviou?r|implementation)\b": "refers to a superseded state",
    r"\bchangelog\b": "revision history by name",
    r"\bdeprecated\b": "a lifecycle statement, not a fact about the tier",
}


# ---------------------------------------------------------------- helpers


def pages() -> dict[str, Path]:
    return {p.stem: p for p in sorted(TIERS_DIR.glob("*.md")) if p.stem != "index"}


def sections(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    current = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            out[current] = []
        elif current is not None:
            out[current].append(line)
    return out


def page_sections(name: str) -> dict[str, list[str]]:
    return sections(pages()[name].read_text())


class _FakeDockingProvider:
    name = "fake-docking"

    def dock(self, mol, receptor, box, seed, exhaustiveness=4): ...

    def settings(self) -> dict:
        return {"engine": "fake"}

    def score_unit(self) -> str:
        return "kcal/mol"


def _rescoring():
    from smeltery.scoring import Rescoring

    tier = Rescoring(VinaScoreProvider([-7.0]))
    cand = Candidate("c", "[H][H]", poses=[Pose(("H", "H"), np.zeros((2, 3)))])
    tier.run([cand], {})  # produces() is unknown until a run
    return tier


#: How to build each tier for inspection. A tier that needs arguments gets an entry here; the rest use their defaults.
FACTORIES = {
    "docking": lambda cls: cls(_FakeDockingProvider()),
    "rescoring": lambda cls: _rescoring(),
}


def make(name: str):
    cls = registered_tiers()[name]
    if name in FACTORIES:
        return FACTORIES[name](cls)
    try:
        return cls()
    except TypeError as e:
        raise AssertionError(f"tier {name!r} needs constructor arguments ({e}); add a factory to FACTORIES") from e


def one_line(lines: list[str], prefix: str, where: str) -> str:
    hits = [ln for ln in lines if ln.startswith(prefix)]
    assert len(hits) == 1, f"{where}: expected exactly one line starting {prefix!r}, found {len(hits)}"
    return hits[0]


def repo_paths(text: str) -> list[str]:
    """Backticked repo-relative file references in `text`, without any ::test or :function suffix."""
    out = []
    for span in re.findall(r"`([^`]+)`", text):
        m = re.fullmatch(
            r"((?:src|tests|docs|experiments|scripts|examples)/[\w./-]+\.(?:py|md|json|toml)|README\.md)(?:::?\w+)?",
            span,
        )
        if m:
            out.append(m.group(1))
    return out


def json_floats(obj) -> list[float]:
    if isinstance(obj, float):
        return [obj]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in json_floats(v)]
    if isinstance(obj, list):
        return [x for v in obj for x in json_floats(v)]
    return []


def number_is_in_source(number: str, unit: str, path: Path) -> bool:
    """The number as written occurs in the source: a float leaf at 3 significant figures (json) or the text."""
    if path.suffix == ".json":
        return any(f"{v:.3g}" == number for v in json_floats(json.loads(path.read_text())))
    return f"{number} {unit}" in path.read_text()


def revision_language(text: str) -> list[str]:
    low = text.lower()
    return [f"{pat} ({why})" for pat, why in REVISION_PATTERNS.items() if re.search(pat, low)]


def defined_names(path: Path) -> set[str]:
    return {
        n.name for n in ast.walk(ast.parse(path.read_text())) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


# ---------------------------------------------------------------- registry <-> pages


def test_every_registered_tier_has_a_page():
    missing = sorted(set(registered_tiers()) - set(pages()))
    assert not missing, (
        f"registered tier(s) {missing} have no docs/tiers/<name>.md. Write the page "
        "(copy the sections of an existing one, state every number with its source, or write UNMEASURED) "
        "and add the tier to the Evidence table in docs/tiers/index.md."
    )


def test_every_page_names_a_registered_tier():
    orphans = sorted(set(pages()) - set(registered_tiers()))
    assert not orphans, (
        f"page(s) {orphans} in docs/tiers/ name no registered tier. Register the tier "
        "(smeltery.tier_registry.TIER_MODULES) or delete the page."
    )


@pytest.mark.parametrize("name", sorted(pages()))
def test_page_heading_is_the_tier_name_and_sections_are_present(name):
    text = pages()[name].read_text()
    assert text.splitlines()[0] == f"# {name}", f"{name}.md must start with '# {name}'"
    have = sections(text)
    assert [s for s in SECTIONS if s not in have] == [], (
        f"{name}.md lacks section(s) {[s for s in SECTIONS if s not in have]}"
    )
    for s in SECTIONS:
        assert any(ln.strip() for ln in have[s]), f"{name}.md: section {s!r} is empty"


# ---------------------------------------------------------------- computes and settings


@pytest.mark.parametrize("name", sorted(registered_tiers()))
def test_computes_lists_the_tiers_quantities_and_units(name):
    tier = make(name)
    body = page_sections(name)["Computes"]
    quantities = tier.produces()
    if not quantities:
        one_line(body, "- **Quantity:** none", f"{name}.md Computes")
        return
    listed = {
        m.group(1): m.group(2) for ln in body if (m := re.match(r"- \*\*Quantity:\*\* `(\w+)`, unit `([^`]+)`", ln))
    }
    assert listed == quantities, f"{name}.md Computes lists {listed}, produces() says {quantities}"


@pytest.mark.parametrize("name", sorted(registered_tiers()))
def test_settings_table_has_the_tiers_keys_and_pinned_defaults(name):
    tier = make(name)
    rows = {}
    for ln in page_sections(name)["Native settings"]:
        m = re.match(r"\| `(\w+)` \| (.+) \|$", ln)
        if m:
            rows[m.group(1)] = m.group(2).strip()
    actual = tier.settings()
    assert set(rows) == set(actual), f"{name}.md settings table keys {sorted(rows)} != settings() keys {sorted(actual)}"
    for key, cell in rows.items():
        pinned = re.fullmatch(r"`([^`]*)`", cell)
        if pinned and key not in UNPINNED_SETTINGS:
            assert pinned.group(1) == str(actual[key]), (
                f"{name}.md: default of {key!r} is {cell}, code says {actual[key]!r}"
            )


# ---------------------------------------------------------------- cost


COST_MEASURED = re.compile(
    r"^- \*\*Cost:\*\* (?P<num>\d+(?:\.\d+)?) (?P<unit>ms|s); \*\*Unit of work:\*\* (?P<work>.+?); "
    r"\*\*System:\*\* (?P<system>.+?); \*\*Basis:\*\* (?P<basis>.+?); \*\*Source:\*\* (?P<source>.+)$"
)


def cost_line(name: str) -> str:
    return one_line(page_sections(name)["Cost"], "- **Cost:**", f"{name}.md Cost")


def cost_is_measured(line: str) -> bool:
    if line == "- **Cost:** UNMEASURED":
        return False
    assert COST_MEASURED.match(line), (
        "the Cost line must be exactly '- **Cost:** UNMEASURED' or "
        "'- **Cost:** <number> <s|ms>; **Unit of work:** ...; **System:** ...; **Basis:** ...; **Source:** ...', "
        f"got: {line}"
    )
    return True


@pytest.mark.parametrize("name", sorted(registered_tiers()))
def test_cost_is_a_number_with_system_basis_and_source_or_exactly_unmeasured(name):
    line = cost_line(name)
    if not cost_is_measured(line):
        return
    m = COST_MEASURED.match(line)
    sources = repo_paths(m["source"])
    assert sources, f"{name}.md: the cost has no backticked source file"
    for src in sources:
        assert (ROOT / src).exists(), f"{name}.md: cost source {src} does not exist"
    assert any(number_is_in_source(m["num"], m["unit"], ROOT / s) for s in sources), (
        f"{name}.md: the cost {m['num']} {m['unit']} is not in any of its sources {sources}"
    )
    basis_setting = make(name).settings().get("basis")
    if basis_setting:
        assert str(basis_setting).lower() in m["basis"].lower(), (
            f"{name}.md: the tier has basis setting {basis_setting!r}; the Basis field must name it, got {m['basis']!r}"
        )


def test_measured_costs_equal_the_recorded_measurements():
    paired = MEASUREMENTS["tiers"]["paired_poses"]["seconds"]
    gfn2 = MEASUREMENTS["tiers"]["gfn2"]["two_point_charges"]["seconds"]
    assert COST_MEASURED.match(cost_line("paired_poses"))["num"] == f"{paired:.3g}"
    assert COST_MEASURED.match(cost_line("gfn2"))["num"] == f"{gfn2:.3g}"
    cal = rhf_calibration()
    fi = COST_MEASURED.match(cost_line("field_interaction"))
    assert fi["num"] == f"{cal.reference_seconds:.3g}" and fi["unit"] == "s"
    assert f"{cal.reference_nbf} basis functions" in fi["system"] and cal.reference_name in fi["system"]


def test_forcefield_cost_is_the_environments_record():
    m = COST_MEASURED.match(cost_line("forcefield"))
    assert (
        m
        and "docs/environments.md" in m["source"]
        and f"{m['num']} {m['unit']}" in (ROOT / "docs/environments.md").read_text()
    )


def test_measurements_record_states_its_machine_and_load():
    assert MEASUREMENTS["machine"] and MEASUREMENTS["load_average_1m_at_start"] > 0 and MEASUREMENTS["repeats"] >= 3
    for tier, rec in MEASUREMENTS["tiers"].items():
        assert rec["system"] and rec["command"] and rec["unit_of_work"], tier


# ---------------------------------------------------------------- floor


def floor_lines(name: str) -> list[str]:
    return [ln for ln in page_sections(name)["Systematic floor"] if ln.startswith("- **Floor")]


def floor_status(name: str) -> str:
    tier = make(name)
    quantities = tier.produces()
    if not quantities:
        return "n/a"
    return "measured" if any(tier.systematic_floor(q) is not None for q in quantities) else "UNMEASURED"


@pytest.mark.parametrize("name", sorted(registered_tiers()))
def test_floor_lines_match_the_codes_floors_and_cite_a_source(name):
    tier = make(name)
    quantities = tier.produces()
    lines = floor_lines(name)
    if not quantities:
        assert lines == [
            "- **Floor:** NONE. The tier produces no quantity, so `systematic_floor()` raises `KeyError` for any name."
        ], lines
        return
    assert len(lines) == len(quantities), (
        f"{name}.md needs one '- **Floor (`q`):**' line per quantity {sorted(quantities)}"
    )
    for q in quantities:
        line = one_line(lines, f"- **Floor (`{q}`):**", f"{name}.md Systematic floor")
        floor = tier.systematic_floor(q)
        if floor is None:
            assert line == f"- **Floor (`{q}`):** UNMEASURED", (
                f"{name}.md: code has no floor for {q}; the line must be UNMEASURED: {line}"
            )
            continue
        m = re.match(rf"- \*\*Floor \(`{q}`\):\*\* (\d+(?:\.\d+)?) (\S+); \*\*Source:\*\* (.+)$", line)
        assert m, f"{name}.md: a measured floor line is '- **Floor (`{q}`):** <number> <unit>; **Source:** ...': {line}"
        assert float(m[1]) == floor, f"{name}.md: floor {m[1]} but the code's floor is {floor}"
        assert m[2] == tier.produces()[q], (
            f"{name}.md: floor unit {m[2]} but the quantity's unit is {tier.produces()[q]}"
        )
        sources = repo_paths(m[3])
        assert sources and all((ROOT / s).exists() for s in sources), f"{name}.md: floor source missing: {m[3]}"
        assert any(number_is_in_source(m[1], m[2], ROOT / s) for s in sources), f"{name}.md: {m[1]} is not in {sources}"


def check_constant_lines(text: str, dde: float, xtb_mae: float) -> list[str]:
    """Lines that discuss a code constant must carry the code's current value."""
    bad = []
    for ln in text.splitlines():
        low = ln.lower()
        if "noise floor" in low and str(dde) not in ln:
            bad.append(f"noise floor line without {dde}: {ln[:80]}")
        if ("xtb_vs_dft_mae_kcal" in low or "mae of xtb" in low) and str(xtb_mae) not in ln:
            bad.append(f"xtb MAE line without {xtb_mae}: {ln[:80]}")
    return bad


@pytest.mark.parametrize("name", sorted(pages()) + ["index"])
def test_constants_on_pages_equal_the_code(name):
    path = INDEX if name == "index" else pages()[name]
    assert check_constant_lines(path.read_text(), DDE_NOISE_FLOOR_KCAL_MOL, XTB_VS_DFT_MAE_KCAL) == []


def test_constant_check_can_fail():
    text = "- **ddE noise floor:** 4.07 kcal/mol\n- **Floor:** 0.825 kcal/mol; constant `XTB_VS_DFT_MAE_KCAL`\n"
    assert check_constant_lines(text, 4.07, 0.825) == []
    assert len(check_constant_lines(text, 4.5, 0.825)) == 1
    assert len(check_constant_lines(text, 4.07, 0.9)) == 1


def test_the_page_values_of_the_two_constants_are_present_and_equal_the_code():
    assert "**ddE noise floor:** 4.07 kcal/mol" in pages()["field_interaction"].read_text()
    assert DDE_NOISE_FLOOR_KCAL_MOL == 4.07
    assert XTB_VS_DFT_MAE_KCAL == 0.825 == make("gfn2").systematic_floor("E_gfn2")


# ---------------------------------------------------------------- gates


def gate_catalogue() -> dict[str, str]:
    """{id: 'path:function'} from the index's Gates table."""
    out = {}
    for ln in sections(INDEX.read_text())["Gates"]:
        m = re.match(r"\| (G\d+) \| [^|]+ \| `([^`]+)` \|", ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def test_every_gate_in_the_catalogue_points_at_a_function_that_exists():
    catalogue = gate_catalogue()
    assert len(catalogue) >= 6, catalogue
    for gid, ref in catalogue.items():
        path, _, func = ref.partition(":")
        assert (ROOT / path).exists(), f"{gid}: {path} does not exist"
        assert func in defined_names(ROOT / path), f"{gid}: no function {func} in {path}"


@pytest.mark.parametrize("name", sorted(registered_tiers()))
def test_gate_ids_on_a_page_exist_and_the_gates_the_tier_applies_are_listed(name):
    import inspect

    body = "\n".join(page_sections(name)["Gates"])
    catalogue = gate_catalogue()
    used = set(re.findall(r"\*\*(G\d+)[,.]", body))
    assert used <= set(catalogue), (
        f"{name}.md names gate(s) {sorted(used - set(catalogue))} that the index does not define"
    )
    source = inspect.getsource(registered_tiers()[name])
    for token, gid in GATE_TOKENS.items():
        if token in source:
            assert re.search(rf"\*\*{gid}, [^*]+\(applied by this tier\)", body), (
                f"{name}'s code uses {token.rstrip('(')} but {name}.md does not list {gid} as applied by this tier"
            )


# ---------------------------------------------------------------- links and references


@pytest.mark.parametrize("path", sorted(TIERS_DIR.glob("*.md")), ids=lambda p: p.name)
def test_relative_links_and_backticked_repo_references_resolve(path):
    text = path.read_text()
    for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
        if "://" in target:
            continue
        assert (path.parent / target).exists(), f"{path.name}: broken link {target}"
    for span in re.findall(r"`([^`]+)`", text):
        m = re.fullmatch(
            r"((?:src|tests|docs|experiments|scripts|examples)/[\w./-]+\.(?:py|md|json|toml)|README\.md)(?:(::?)(\w+))?",
            span,
        )
        if not m:
            continue
        assert (ROOT / m.group(1)).exists(), f"{path.name}: `{span}` does not exist"
        if m.group(3) and m.group(1).endswith(".py"):
            assert m.group(3) in defined_names(ROOT / m.group(1)), f"{path.name}: `{span}`: no such function"


# ---------------------------------------------------------------- evidence table


def evidence_table() -> dict[str, tuple[str, str, str]]:
    out = {}
    for ln in sections(INDEX.read_text())["Evidence"]:
        m = re.match(r"\| \[(\w+)\]\(\1\.md\) \| (\w+) \| (\w+) \| ([\w/]+) \|$", ln)
        if m:
            out[m.group(1)] = (m.group(2), m.group(3), m.group(4))
    return out


def derived_evidence(name: str) -> tuple[str, str, str]:
    body = page_sections(name)["Evidence"]
    anchor = one_line(body, "- **Anchor:**", f"{name}.md Evidence")
    tests = re.findall(r"`(tests/[\w./-]+\.py)::(\w+)`", anchor)
    if tests:
        for path, func in tests:
            assert func in defined_names(ROOT / path), f"{name}.md: anchor test {path}::{func} does not exist"
        a = "yes"
    else:
        assert anchor.startswith("- **Anchor:** none"), (
            f"{name}.md: the Anchor line names no test and does not say 'none'"
        )
        a = "none"
    return a, "measured" if cost_is_measured(cost_line(name)) else "UNMEASURED", floor_status(name)


def test_the_evidence_table_equals_what_the_pages_and_the_code_say():
    table = evidence_table()
    derived = {name: derived_evidence(name) for name in registered_tiers() if name in pages()}
    assert table == derived, (
        "docs/tiers/index.md Evidence table is out of step with the pages/code. It should read:\n"
        + "\n".join(f"| [{n}]({n}.md) | {a} | {c} | {f} |" for n, (a, c, f) in derived.items())
    )


# ---------------------------------------------------------------- no revision history


@pytest.mark.parametrize("path", sorted(TIERS_DIR.glob("*.md")), ids=lambda p: p.name)
def test_no_page_narrates_revision_history(path):
    found = revision_language(path.read_text())
    assert not found, f"{path.name} contains revision-history wording: {found}. State the current fact only."


@pytest.mark.parametrize(
    "sentence",
    [
        "It previously returned None.",
        "This was formerly a ranker.",
        "It used to accept a charge.",
        "The tier no longer writes coordinates.",
        "The tier now supports OpenMM.",
        "The floor was changed to 0.825.",
        "Added in version 0.2.",
        "A recently added path.",
        "The default has been changed.",
    ],
)
def test_the_revision_language_check_can_fail(sentence):
    assert revision_language(sentence), sentence


@pytest.mark.parametrize(
    "sentence",
    [
        "The tier never writes coordinates back.",
        "Only the mmff path exists.",
        "The floor is 0.825 kcal/mol, measured on 20 conformers.",
        "Unknown quantities raise KeyError.",
    ],
)
def test_the_revision_language_check_passes_current_facts(sentence):
    assert revision_language(sentence) == []
