# Cross-server transcript: smeltery's MCP and attestation's, driven as an agent drives them

`tests/cross_server/test_agent_transcript.py` is a scripted agent session that runs in CI (job `cross-server` in
`.github/workflows/test.yml`). It talks to two MCP servers, each a subprocess on stdio, through the `mcp` SDK's
stdio client, which is the only channel an agent has:

* **smeltery**: `tests/cross_server/smeltery_fake_mcp.py ROOT`. This is `smeltery mcp` ([mcp.md](mcp.md)) with one
  change: the local job backend runs the fake engine (`tests/fake_ferric.py`), the same setup
  `tests/test_mcp_server.py` builds in-process. The tools, schemas, refusals and ceilings are the production
  server's. The energies are not real, so nothing here checks a number.
* **attestation**: `attest-mcp` with `ATTEST_TOOLS=provenance ATTEST_EXPAND=1`, the agent surface for "which arm won,
  on what evidence". It comes from its own install (below), and the test points it at a fresh `ATTEST_DB`, a
  `RESEARCH_ROOT` holding the screens, and a `metric_direction.toml`. attestation ranks only by directions declared
  there, so the test declares `paired_ddE.mean` lower-is-better.

The two never import each other. smeltery imports `attestation_schema`, behind its `attestation` extra, and never
`attestation`. attestation never imports smeltery or ferric. The only thing they share is the run directory, and in
it the core record, `OUT/attestation/run.json` ([core-record.md](core-record.md)).

## What it proves

The session, in order:

1. smeltery `tools/list` lists exactly the four tools. `estimate_cost` prices the screen, and it is under the
   configured ceilings (`SMELTERY_MAX_CANDIDATES=8`, `SMELTERY_MAX_CPU_HOURS=100`).
2. `screen_candidates` runs twice in one campaign (`xserver`), once per target class (`protein-pocket-rhf`,
   `fast-triage`), each with `out_dir` under `RESEARCH_ROOT/smeltery/`. That gives the family two arms with the
   same ligands and different pose counts. Between the two screens, the fake engine's `ferric.__build__` stamp
   changes from clean to dirty.
3. Right after each submission the test asks attestation `runs.status`. If smeltery's `run_status` says the screen is
   unfinished both before and after that call, attestation must list the screen as `in_flight`, or as `lagging`
   when the record landed just before the state settled. smeltery's `run_status` is then polled until the screen
   reaches a terminal state (10-minute bound), and it must end `complete` with a record.
4. attestation `runs.status` reports neither screen as in flight or unrecorded. `runs.scan(confirm=true)` reads both
   runs through the core adapter, with **nothing quarantined and nothing lossy**. `runs.list` shows one family,
   `smeltery/xserver`, with two complete runs.
5. `runs.compare(family="xserver", metric="paired_ddE.mean")` is **the ranking**. Its arms are exactly the two
   screens (an arm's `source_path` is the screen's handle), `lower_is_better`, ranked on one shared analogue
   split, with the winner first.
6. `runs.detail` on each arm is **the provenance**. Both runs come through the `core` adapter. Every finding is a
   `code ...` or `dependencies ...` phrase. The clean-build run has no ferric finding. The dirty-build run carries
   `dependencies dirty: ferric`, and `runs.compare` repeats it as a grouped `provenance not verified for ...`
   caveat. The test also reads both `run.json` files to confirm what smeltery wrote: ferric `verified` (from the
   build stamp), `dirty` false and then true. So the engine's build identity is carried from smeltery's record to
   attestation's caveat, and it is reported as a caveat, not as a verdict on the numbers.
7. `runs.claims_check` runs on a draft that quotes the winner's value twice: once correctly, once off by 1.0. The
   results must be `supported` and `contradicted`, both against the winning run.

Every call goes into a JSON-lines transcript: `seq`, `server`, `tool`, `arguments`, `is_error`, and a trimmed
`result` with the temporary directory spelled `$WORK`. Both servers' stderr is saved beside it. CI uploads all
three as the `cross-server-transcript` artifact. The assertions check structure and decisions (states, names,
verdicts, findings), never floating-point values.

## Running it locally

```sh
uv sync --extra dev --extra mcp --extra attestation --no-sources     # smeltery's env: no attestation in it
uv tool install --python 3.12 git+https://github.com/mgoldey/attestation@main   # attest-mcp in its own env
SMELTERY_REQUIRE_CROSS_SERVER=1 SMELTERY_CROSS_SERVER_TRANSCRIPT=/tmp/xs/transcript.jsonl \
  uv run --no-sync pytest -q -rs tests/cross_server
```

From an attestation checkout, use `uv tool install --from /path/to/attestation attestation`, or skip installing
and set `SMELTERY_ATTEST_MCP="uvx --from /path/to/attestation attest-mcp"`. That variable takes shell words and
replaces `attest-mcp` on `PATH`. The session takes about 10 seconds.

The test **skips with the reason** in four cases: the `mcp` extra is missing, `attestation-schema` is missing,
`attest-mcp` is not found, or the `attest-mcp` it finds lacks a tool the script calls. That last case covers PyPI's
attestation 0.5.0, which has neither `runs.status` nor the core-record reader. The test's tools check names the
missing tools before any screen runs. `SMELTERY_REQUIRE_CROSS_SERVER=1` turns every skip into a failure, so the CI
job cannot pass by skipping. The `test` job installs neither the `mcp` extra nor attestation, so the test skips
there.

## Why attestation comes from git in CI

PyPI's `attestation` is 0.5.0 (2026-10-06), which is older than both pieces this test needs: the ledger's
core-record reader and `runs.status`. attestation main also requires `attestation-schema>=0.2.0`, and PyPI has only
0.1.0, so even a newer attestation from the index would not resolve. A git install builds both from the same
checkout. The job tracks `main` on purpose, because it exists to check that the two repositories' heads still fit
together. Set `ATTESTATION_REF` in the job to pin a tag once a release with both pieces is on PyPI. The install
uses `uv tool install`, which creates a separate environment with its own Python (>= 3.12, attestation's floor,
while smeltery's CI runs 3.11). A job step asserts that `attestation` is not importable from smeltery's
environment.

## Limits

* The MCP `screen_candidates` names no `arm`, so attestation names each arm by the job's `run_id`, and a reader maps
  it back to the screen through `source_path`. The target class that tells the arms apart appears only in the
  config (`runs.detail` shows `poses.n_poses`).
* `runs.compare` ranks on one analogue split (the one the arms share most). It cannot rank on the per-analogue
  `paired_ddE.*` across arms that screened different molecules, which is why named arms also emit
  `best_paired_ddE` ([core-record.md](core-record.md)).
* The in-flight check in step 3 is opportunistic. It asserts only when smeltery reported the screen unfinished on
  both sides of attestation's answer. The fake engine finishes in seconds, so a fast machine can skip it. The
  transcript shows whether it ran.
* smeltery's editable install is recorded as `code inferred`, so both runs carry that finding. The test accepts
  any `code`/`dependencies` findings and asserts only the ferric ones.
* The `mcp` 2.x client opens with `server/discover`. attestation's server (SDK 1.x) rejects it and the client falls
  back to `initialize`. The rejection appears in `attest-mcp.stderr` as pydantic validation errors, which is
  harmless.
