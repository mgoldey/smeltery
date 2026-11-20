"""compat/stack.toml, the pinned compatible set (roadmap 46), against what this repository actually declares and locks.

The smeltery-environment pins must equal uv.lock's versions, so every `uv sync` CI job tests exactly the pinned set,
and must satisfy the specifier pyproject.toml declares for them; compat/constraints.txt must be the generated form;
attestation is pinned by full commit and is never a dependency of smeltery (only attestation-schema is).
"""

import importlib.util
import re
import tomllib
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]
STACK = tomllib.loads((ROOT / "compat" / "stack.toml").read_text())
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
LOCK = tomllib.loads((ROOT / "uv.lock").read_text())
spec = importlib.util.spec_from_file_location("compat_matrix", ROOT / "scripts" / "compat_matrix.py")
assert spec is not None and spec.loader is not None
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)


def _declared() -> dict[str, list[Requirement]]:
    project = PYPROJECT["project"]
    reqs = [Requirement(r) for r in project["dependencies"]]
    reqs += [Requirement(r) for deps in project["optional-dependencies"].values() for r in deps]
    out: dict[str, list[Requirement]] = {}
    for r in reqs:
        out.setdefault(r.name, []).append(r)
    return out


def test_every_smeltery_env_pin_is_what_uv_lock_pins():
    locked: dict[str, set[str]] = {}
    for p in LOCK["package"]:
        locked.setdefault(p["name"], set()).add(p.get("version"))
    for name, pin in STACK["smeltery-env"]["pins"].items():
        assert locked.get(name) == {pin["version"]}, (
            f"compat/stack.toml pins {name}=={pin['version']} but uv.lock has {locked.get(name)}: move both together "
            f"(`uv lock -P {name}=={pin['version']}`)"
        )


def test_every_smeltery_env_pin_satisfies_what_pyproject_declares():
    declared = _declared()
    for name, pin in STACK["smeltery-env"]["pins"].items():
        assert name in declared, f"{name} is pinned but smeltery declares no dependency on it"
        for req in declared[name]:
            assert req.specifier.contains(pin["version"], prereleases=True), f"{pin['version']} violates {req}"


def test_the_constraints_file_is_generated_from_the_stack():
    text = (ROOT / "compat" / "constraints.txt").read_text()
    assert text == cm.render_constraints(STACK), "run `uv run python scripts/compat_matrix.py constraints --write`"
    pins = [ln for ln in text.splitlines() if ln and not ln.startswith("#")]
    assert pins == sorted(f"{n}=={p['version']}" for n, p in STACK["smeltery-env"]["pins"].items())
    assert not any(re.match(r"(smeltery|attestation)==", p) for p in pins), "only the dependencies are constrained"


def test_attestation_is_pinned_by_full_commit_and_is_never_a_smeltery_dependency():
    att = STACK["attestation-env"]["attestation"]
    assert att["source"] == "git" and re.fullmatch(r"[0-9a-f]{40}", att["rev"]), att
    assert "attestation" not in _declared(), "smeltery must never depend on attestation itself"
    assert "attestation-schema" in _declared()


def test_the_stack_ci_job_installs_the_pins_and_the_pinned_attestation():
    wf = (ROOT / ".github" / "workflows" / "test.yml").read_text()
    job = wf[wf.index("\n  stack:") :]
    assert "-c compat/constraints.txt" in job and "constraints --check" in job
    assert "['attestation-env']['attestation']['rev']" in job and "uv tool install" in job
    assert "tests/cross_server" in job and 'SMELTERY_REQUIRE_CROSS_SERVER: "1"' in job


def test_readme_and_the_mcp_install_section_link_the_compatibility_page():
    readme = (ROOT / "README.md").read_text()
    mcp = (ROOT / "docs" / "mcp.md").read_text()
    assert "docs/compatibility.md" in readme
    section = mcp[mcp.index("## Install with uvx or pipx") : mcp.index("## The tools")]
    assert "compatibility.md" in section
