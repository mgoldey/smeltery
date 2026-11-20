"""An agent transcript as a test: a screen through smeltery's MCP server, ranked and audited through attestation's.

Roadmap task 27. Two MCP servers, two subprocesses, one stdio client each -- the only channel an agent has:

* smeltery: `tests/cross_server/smeltery_fake_mcp.py`, which is `smeltery mcp` with the local job backend's runner
  pointed at the fake engine (`tests/fake_ferric.py`), so CI needs no quantum chemistry;
* attestation: `attest-mcp` from an ISOLATED install (`uv tool install`), never a dependency of smeltery. smeltery
  never imports `attestation` and attestation never imports smeltery; the run directory's core record
  (`attestation/run.json`) is the whole contract between them.

The script, in the order an agent would act: estimate a screen's cost, submit two screens of one campaign (two target
classes, so the family has two arms to rank), poll each to a terminal state, then ask attestation what is still in
flight, scan, list, compare the family, read each run's provenance, and check a draft that quotes the ranked number.
Between the two screens the fake engine's build stamp turns dirty, so the second run's ferric identity is different
from the first's and attestation must say so. Every call is written to a JSON-lines TRANSCRIPT (tool, arguments, a
trimmed result); `SMELTERY_CROSS_SERVER_TRANSCRIPT` names the file (CI uploads it), else it is left in `tmp_path`.
Both servers' stderr is written beside it (`smeltery-mcp.stderr`, `attest-mcp.stderr`).

Assertions are on structure and on what each side decided (states, names, verdicts, provenance findings), never on
a floating value: the energies are the fake engine's.

Skips, with the reason, when the `mcp` extra, `attestation-schema` or `attest-mcp` is missing, or when the
`attest-mcp` found lacks a tool this script calls. `SMELTERY_REQUIRE_CROSS_SERVER=1` turns every such skip into a
failure, so the CI job cannot pass by skipping. `SMELTERY_ATTEST_MCP` overrides the command (shell words), e.g.
`uvx --from /path/to/attestation attest-mcp`. docs/cross-server.md.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import shlex
import shutil
import sys
import time
from contextlib import AsyncExitStack
from pathlib import Path
from typing import NoReturn

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
REQUIRE = os.environ.get("SMELTERY_REQUIRE_CROSS_SERVER") == "1"

#: attestation's tools this transcript calls; an older `attest-mcp` (PyPI 0.5.0 has no runs.status and no core-record
#: reader) is reported as unavailable rather than failing on the first missing tool.
ATTEST_TOOLS = {"runs.status", "runs.scan", "runs.list", "runs.compare", "runs.detail", "runs.claims_check"}
SMELTERY_TOOLS = {"screen_candidates", "run_status", "explain_drop", "estimate_cost"}

CAMPAIGN = "xserver"
PROJECT = "smeltery"  # the project directory under attestation's RESEARCH_ROOT that the screens are written into
METRIC = "paired_ddE.mean"
#: One screen per target class: same ligands, same pocket, different pose counts -- two arms of one family.
CLASSES = ("protein-pocket-rhf", "fast-triage")
LIGANDS = {"ethanol": "CCO", "propanol": "CCCO", "octanol": "CCCCCCCCO"}
TARGET = """
schema_version = 1
[[pocket.site]]
q = 1.0
atom = 2
from_atom = 1
distance = 3.0

[[pocket.site]]
q = -0.5
atom = 0
from_atom = 1
distance = 3.5
"""
POLL_TIMEOUT_S = 600
SESSION_TIMEOUT_S = 1500
#: A clean and a dirty build of the (fake) engine, as `ferric.__build__` reports it.
CLEAN = {"git_sha": "1" * 40, "dirty": False}
DIRTY = {"git_sha": "2" * 40, "dirty": True}


def _unavailable(reason: str) -> NoReturn:
    if REQUIRE:
        pytest.fail(f"SMELTERY_REQUIRE_CROSS_SERVER=1 but {reason}")
    pytest.skip(reason)


def _attest_command() -> list[str] | None:
    override = os.environ.get("SMELTERY_ATTEST_MCP")
    if override:
        return shlex.split(override)
    found = shutil.which("attest-mcp")
    return [found] if found else None


# ---- the transcript --------------------------------------------------------------------------------------------


def _trim(value, work: str):
    """A result as it reads in a transcript: the work directory spelled `$WORK`, long strings and lists cut."""
    if isinstance(value, str):
        value = value.replace(work, "$WORK")
        return value if len(value) <= 400 else value[:397] + "..."
    if isinstance(value, list):
        out = [_trim(v, work) for v in value[:12]]
        return out + [f"... {len(value) - 12} more"] if len(value) > 12 else out
    if isinstance(value, dict):
        return {k: _trim(v, work) for k, v in value.items()}
    return value


class Transcript:
    def __init__(self, path: Path, work: Path) -> None:
        self.path, self.work = path, str(work)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("")
        self.seq = 0

    def write(self, server: str, tool: str, arguments: dict, result, is_error: bool) -> None:
        self.seq += 1
        line = {
            "seq": self.seq,
            "server": server,
            "tool": tool,
            "arguments": _trim(arguments, self.work),
            "is_error": is_error,
            "result": _trim(result, self.work),
        }
        with self.path.open("a") as f:
            f.write(json.dumps(line, sort_keys=True) + "\n")


class Session:
    """One MCP client session; every call goes into the transcript."""

    def __init__(self, name: str, client, transcript: Transcript) -> None:
        self.name, self.client, self.transcript = name, client, transcript

    async def tools(self) -> set[str]:
        names = {t.name for t in (await self.client.list_tools()).tools}
        self.transcript.write(self.name, "tools/list", {}, sorted(names), False)
        return names

    async def call(self, tool: str, **arguments) -> dict:
        r = await self.client.call_tool(tool, arguments)
        if r.structured_content is not None:
            result = r.structured_content
        else:  # attestation's tools answer with a JSON document in a text block
            text = "".join(getattr(c, "text", "") for c in r.content)
            try:
                result = json.loads(text)
            except ValueError:
                result = {"text": text}
        self.transcript.write(self.name, tool, arguments, result, bool(r.is_error))
        assert not r.is_error, f"{self.name} {tool}({arguments}) failed: {result}"
        return result


async def _poll(smeltery: Session, handle: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_S
    while True:
        status = await smeltery.call("run_status", handle=handle)
        if status["finished"]:
            return status
        assert time.monotonic() < deadline, f"the screen never finished: {status}"
        await asyncio.sleep(1.0)


def _ours(rows: list[dict], handles: list[str]) -> list[dict]:
    """runs.status rows for these screens (`run_dir` keeps its tail when clipped)."""
    tails = {Path(h).name for h in handles}
    return [r for r in rows if Path(str(r.get("run_dir", "")).lstrip(".")).name in tails]


def _ferric(handle: str) -> dict:
    """ferric's entry in the core record smeltery wrote: the file both servers read, checked here only to say what
    attestation should have found in it."""
    record = json.loads((Path(handle) / "attestation" / "run.json").read_text())
    return next(d for d in record["dependencies"] if d["name"] == "ferric")


# ---- the test ---------------------------------------------------------------------------------------------------


def test_an_agent_screens_through_smeltery_and_asks_attestation_for_ranking_and_provenance(tmp_path):
    if importlib.util.find_spec("mcp") is None:
        _unavailable("the cross-server transcript needs the `mcp` extra (pip install 'smeltery[mcp]')")
    if importlib.util.find_spec("attestation_schema") is None:
        _unavailable("campaign jobs need attestation-schema (the `attestation` extra)")
    attest = _attest_command()
    if attest is None:
        _unavailable(
            "attest-mcp is not on PATH: install attestation in its own environment (uv tool install "
            "git+https://github.com/mgoldey/attestation@main) or set SMELTERY_ATTEST_MCP"
        )

    from mcp import Client, StdioServerParameters
    from mcp.client.stdio import stdio_client

    work = tmp_path.resolve()
    fake = work / "fake"
    fake.mkdir()
    cfg = fake / "cfg.json"
    cfg.write_text(json.dumps({"rules": {}, "dir": str(fake), "build": CLEAN}))
    target = work / "target.toml"
    target.write_text(TARGET)
    research = work / "research"
    (research / PROJECT).mkdir(parents=True)
    directions = work / "metric_direction.toml"
    # attestation ranks by declared directions only (a record's own `direction` is not read for ranking).
    directions.write_text(f'[metric_direction]\n"{METRIC}" = "lower_is_better"\n')
    out = os.environ.get("SMELTERY_CROSS_SERVER_TRANSCRIPT")
    transcript = Transcript(Path(out).resolve() if out else work / "transcript.jsonl", work)

    smeltery_env = {k: v for k, v in os.environ.items() if not k.startswith("SMELTERY_MAX_")}
    smeltery_env.update(FAKE_FERRIC=str(cfg), SMELTERY_MAX_CANDIDATES="8", SMELTERY_MAX_CPU_HOURS="100")
    smeltery_params = StdioServerParameters(
        command=sys.executable,
        args=[str(HERE / "smeltery_fake_mcp.py"), str(work / "ws")],
        env=smeltery_env,
        cwd=str(REPO),
    )
    # attestation sees only what this test configures: no ledger, persona store or model endpoint of the caller's.
    leaks = ("ATTEST_", "RSS_", "RESEARCH_ROOT", "LEDGER_", "LLM_", "EMBED_", "HERMES_", "VIRTUAL_ENV")
    attest_env = {k: v for k, v in os.environ.items() if not k.startswith(leaks)}
    attest_env.update(
        ATTEST_DB=str(work / "attestation.db"),
        RESEARCH_ROOT=str(research),
        LEDGER_METRIC_DIRECTION_FILE=str(directions),
        HERMES_HOME=str(work / "hermes"),
        ATTEST_TOOLS="provenance",  # the agent surface for "which arm won, on what evidence"
        ATTEST_EXPAND="1",
        ATTEST_RESEARCH_WEB="0",
    )
    attest_params = StdioServerParameters(command=attest[0], args=attest[1:], env=attest_env, cwd=str(work))

    async def session() -> dict:
        facts: dict = {"handles": []}
        async with AsyncExitStack() as stack:
            smeltery_log = stack.enter_context((transcript.path.parent / "smeltery-mcp.stderr").open("w"))
            attest_log = stack.enter_context((transcript.path.parent / "attest-mcp.stderr").open("w"))
            smeltery = Session(
                "smeltery",
                await stack.enter_async_context(Client(stdio_client(smeltery_params, errlog=smeltery_log))),
                transcript,
            )
            assert await smeltery.tools() == SMELTERY_TOOLS
            attestation = Session(
                "attestation",
                await stack.enter_async_context(Client(stdio_client(attest_params, errlog=attest_log))),
                transcript,
            )
            missing = ATTEST_TOOLS - await attestation.tools()
            if missing:
                return {
                    "unavailable": f"{shlex.join(attest)} has no {', '.join(sorted(missing))} (needs attestation "
                    "with the core-record ledger and runs.status: main after 0.5.0)"
                }

            screen = {"target": str(target), "ligand_set": LIGANDS, "keep": 1, "campaign": CAMPAIGN}
            facts["cost"] = await smeltery.call("estimate_cost", target=str(target), ligand_set=LIGANDS)
            for cls in CLASSES:
                if cls == CLASSES[1]:  # a different engine build for the second arm
                    cfg.write_text(json.dumps({"rules": {}, "dir": str(fake), "build": DIRTY}))
                submitted = await smeltery.call(
                    "screen_candidates", **screen, target_class=cls, out_dir=str(research / PROJECT / cls)
                )
                handle = submitted["handle"]
                facts["handles"].append(handle)
                facts.setdefault("submitted", []).append(submitted)
                # Opportunistic: while smeltery says the screen is unfinished on both sides of attestation's answer,
                # attestation must list it as in flight (state.json without run.json) -- or lagging, when the record
                # was written just before the state settled.
                before = await smeltery.call("run_status", handle=handle)
                during = await attestation.call("runs.status")
                after = await smeltery.call("run_status", handle=handle)
                if not before["finished"] and not after["finished"]:
                    facts.setdefault("seen_in_flight", []).append(_ours(during["rows"], [handle]))
                facts.setdefault("status", []).append(await _poll(smeltery, handle))

            facts["runs.status"] = await attestation.call("runs.status")
            facts["runs.scan"] = await attestation.call("runs.scan", confirm=True)
            facts["runs.list"] = await attestation.call("runs.list", family=CAMPAIGN)
            facts["runs.compare"] = await attestation.call("runs.compare", family=CAMPAIGN, metric=METRIC)
            facts["runs.detail"] = {
                arm["name"]: await attestation.call("runs.detail", project=PROJECT, name=arm["name"])
                for arm in facts["runs.compare"]["arms"]
            }
            winner = facts["runs.compare"]["arms"][0]
            split = winner["split"]
            draft = research / PROJECT / "draft.md"
            draft.write_text(
                f"The {CAMPAIGN} screen ranks this arm first at {winner['value']:.3f} kcal/mol.\n"
                f"<!-- claim: {PROJECT}/{winner['name']} metric={METRIC} split={split} "
                f"value={winner['value']:.3f} tol=0.001 -->\n"
                f"A careless edit later says {winner['value'] + 1.0:.3f} kcal/mol.\n"
                f"<!-- claim: {PROJECT}/{winner['name']} metric={METRIC} split={split} "
                f"value={winner['value'] + 1.0:.3f} tol=0.001 -->\n"
            )
            facts["runs.claims_check"] = await attestation.call("runs.claims_check", path=str(draft))
        return facts

    facts = asyncio.run(asyncio.wait_for(session(), SESSION_TIMEOUT_S))
    if "unavailable" in facts:
        _unavailable(facts["unavailable"])
    handles = facts["handles"]

    # smeltery: priced, submitted under the ceiling, polled to completion.
    assert facts["cost"]["cost"]["candidates"] == len(LIGANDS) and facts["cost"]["ceiling_exceeded"] is False
    for sub in facts["submitted"]:
        assert sub["submitted"] is True and sub["ceiling"]["exceeded"] is False
    for status in facts["status"]:
        assert (status["state"], status["finished"]) == ("complete", True), status["message"]
        assert status["record"] is not None

    # attestation, before the scan: nothing of ours in flight or unrecorded, read from the files smeltery wrote.
    assert not _ours(facts["runs.status"]["rows"], handles), facts["runs.status"]
    for rows in facts.get("seen_in_flight", []):
        assert rows and all(r["kind"] in ("in_flight", "lagging") for r in rows), rows

    # The scan: both runs read through the core adapter, nothing quarantined or lossy.
    scan = facts["runs.scan"]
    assert scan["scanned"] == {PROJECT: len(CLASSES)}, scan
    assert scan["quarantined"] == [] and not scan["lossy"], scan
    listed = facts["runs.list"]
    assert {(f["project"], f["family"], f["n"]) for f in listed["families"]} == {(PROJECT, CAMPAIGN, len(CLASSES))}
    assert all(r["status"] == "complete" for r in listed["runs"])

    # The ranking: one family, both screens as its arms (named by run id; source_path is the screen's handle).
    compare = facts["runs.compare"]
    assert (compare["family"], compare["metric"], compare["direction"]) == (CAMPAIGN, METRIC, "lower_is_better")
    arms = {Path(a["source_path"]).resolve(): a for a in compare["arms"]}
    assert set(arms) == {Path(h).resolve() for h in handles}, compare["arms"]
    assert compare["winner"] == compare["arms"][0]["name"]
    assert all(a["status"] == "complete" for a in compare["arms"]) and compare["without_metric"] == []
    assert len({a["split"] for a in compare["arms"]}) == 1, "arms are ranked on one analogue, the same for both"
    name = {h: arms[Path(h).resolve()]["name"] for h in handles}
    clean_run, dirty_run = (name[h] for h in handles)

    # Provenance: ferric's build identity crosses the boundary. smeltery recorded both builds as verified (a build
    # stamp), one dirty; attestation names the dirty one -- as a caveat, never as a verdict on the numbers.
    assert (_ferric(handles[0])["provenance"], _ferric(handles[0])["dirty"]) == ("verified", False)
    assert (_ferric(handles[1])["provenance"], _ferric(handles[1])["dirty"]) == ("verified", True)
    detail = {n: d["run"] for n, d in facts["runs.detail"].items()}
    for run in detail.values():
        assert run["adapter"] == "core" and run["family"] == CAMPAIGN and run["status"] == "complete"
        assert all(f.startswith(("code ", "dependencies ")) for f in run["provenance"]), run["provenance"]
    assert not any("ferric" in f for f in detail[clean_run]["provenance"]), detail[clean_run]["provenance"]
    assert "dependencies dirty: ferric" in detail[dirty_run]["provenance"], detail[dirty_run]["provenance"]
    assert any(
        c.startswith("provenance not verified for") and dirty_run in c and "dependencies dirty: ferric" in c
        for c in compare["caveats"]
    ), compare["caveats"]

    # The draft: the ranked number quoted back is supported, a wrong one contradicted, both carrying the caveat.
    claims = facts["runs.claims_check"]
    assert claims["counts"] == {"supported": 1, "contradicted": 1}, claims
    assert [c["verdict"] for c in claims["claims"]] == ["supported", "contradicted"]
    assert all(c["run"] == f"{PROJECT}/{compare['winner']}" for c in claims["claims"])
    assert all(c["caveats"] for c in claims["claims"]) == bool(detail[compare["winner"]]["provenance"])
