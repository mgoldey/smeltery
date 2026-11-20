"""docs/mcp.md's install section: the commands and the JSON name real entry points.

The section is where a user copies a client config from, so it is checked against
`pyproject.toml`: the console script is registered, the `mcp` and `attestation`
extras exist, the subcommand is the one `smeltery.mcp.server` serves, and the JSON parses.
attestation's half of the same example is pinned in that repo (tests/test_claude_guide.py).
"""

import json
import re
import shlex
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = (ROOT / "docs" / "mcp.md").read_text()
README = (ROOT / "README.md").read_text()
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


def _servers() -> dict:
    blocks = [json.loads(b) for b in re.findall(r"```json\n(.*?)```", DOC, re.S)]
    both = [b["mcpServers"] for b in blocks if {"smeltery", "attestation"} <= set(b["mcpServers"])]
    assert len(both) == 1, "docs/mcp.md shows one config wiring both servers"
    return both[0]


def test_the_config_spawns_the_registered_script_with_the_real_extras():
    entry = _servers()["smeltery"]
    assert entry["command"] == "uvx"
    assert entry["args"][-2:] == ["smeltery", "mcp"]
    assert "smeltery" in PYPROJECT["project"]["scripts"]
    extras = PYPROJECT["project"]["optional-dependencies"]
    requirement = next(a for a in entry["args"] if a.startswith("smeltery["))
    for extra in re.search(r"\[(.*?)\]", requirement).group(1).split(","):
        assert extra in extras, f"docs name the extra {extra!r}, which pyproject does not define"
    # smeltery's own Python window: the docs must say which interpreter, not leave it to uv.
    requires = PYPROJECT["project"]["requires-python"]
    version = entry["args"][entry["args"].index("--python") + 1]
    assert version in ("3.11", "3.12") and "<3.13" in requires


def test_the_mcp_subcommand_the_docs_run_is_the_one_served():
    cli = (ROOT / "src" / "smeltery" / "cli.py").read_text()
    assert '"mcp"' in cli
    assert "--list-tools" in (ROOT / "src" / "smeltery" / "mcp" / "server.py").read_text()


def test_the_attestation_half_names_attestations_console_script_and_shared_root():
    servers = _servers()
    att = servers["attestation"]
    assert att["args"][-1] == "attest-mcp"
    root = servers["smeltery"]["env"]["SMELTERY_ROOT"]
    assert att["env"]["RESEARCH_ROOT"] == f"{root}/.smeltery/screens"
    # the screens directory is where screen_candidates writes with no out_dir
    assert 'Path(".smeltery") / "screens"' in (ROOT / "src" / "smeltery" / "mcp" / "screening.py").read_text()


def test_every_claude_mcp_add_line_has_the_shape_the_cli_accepts():
    found = 0
    for text in (DOC, README):
        for block in re.findall(r"```(?:sh|bash)\n(.*?)```", text, re.S):
            for line in block.replace("\\\n", " ").splitlines():
                if not line.startswith("claude mcp add"):
                    continue
                found += 1
                argv = shlex.split(line)
                assert argv[3] == "smeltery", "the name comes first: -e would swallow it"
                before = argv[: argv.index("--")]
                envs = [before[i + 1] for i, a in enumerate(before) if a == "-e"]
                assert envs and all(re.fullmatch(r"[A-Z_]+=\S+", e) for e in envs)
                assert argv[argv.index("--") + 1] == "uvx"
    assert found == 2
