# MCP server: `smeltery mcp`

Four coarse tools that let an agent run a screen, follow it, and ask why a molecule did not survive — without
knowing any quantum chemistry and without choosing a single calculation setting.

```sh
pip install 'smeltery[mcp,attestation]'    # the SDK, plus the campaign-job backend the tools submit through
smeltery mcp                               # an MCP server on stdin/stdout, the form a client spawns
smeltery mcp --list-tools                  # the tool names, no server
```

`pip install` works once a release carries the `mcp` extra: smeltery 0.1.0 on PyPI (checked 2026-11-20) predates
`smeltery mcp`, so until the next release install from git, as [the next section](#install-with-uvx-or-pipx-and-wire-it-into-claude)
does.

A client entry looks like this:

```json
{"mcpServers": {"smeltery": {"command": "smeltery", "args": ["mcp"],
  "env": {"SMELTERY_ROOT": "/data/smeltery", "SMELTERY_MAX_CPU_HOURS": "40", "SMELTERY_MAX_CANDIDATES": "32"}}}}
```

It is built on the official MCP Python SDK (`mcp`, the optional `mcp` extra). The SDK's server class is
`mcp.server.mcpserver.MCPServer`, which is FastMCP renamed in SDK 2.0; the floor is `mcp>=2.3`. Every tool returns a
dataclass, so each one publishes a real `outputSchema` and a client reads fields, not prose.

## Install with uvx or pipx, and wire it into Claude

A client spawns `smeltery mcp` itself, so nothing needs installing first. `uvx` and `pipx` each fetch the server into
its own environment. smeltery supports Python 3.11 and 3.12 only, so name one (uv may otherwise pick 3.13 and fail to
resolve); `mcp` is the extra that carries the SDK and `attestation` the one that writes the core run record
attestation reads.

```sh
uvx --python 3.12 --from 'smeltery[mcp,attestation] @ git+https://github.com/mgoldey/smeltery' smeltery mcp --list-tools
pipx run --python python3.12 --spec 'smeltery[mcp,attestation] @ git+https://github.com/mgoldey/smeltery' smeltery mcp --list-tools
```

Both print the four tool names and exit. Once a release with the `mcp` extra is on PyPI, replace the requirement with
`smeltery[mcp,attestation]`.

To install versions known to work together instead of the newest of each, use the pinned set: smeltery with
`compat/constraints.txt` read from the same ref, and attestation, in its own environment, at the commit
`compat/stack.toml` pins (the two cannot share one environment today: their `mcp` and attestation-schema
requirements conflict). [compatibility.md](compatibility.md#the-pinned-stack) has the commands, and the table of
which ferric versions each smeltery was tested against.

**Claude Code.** Name first, `--` before the command (`-e` takes several values, so a name written after it is read as
another `KEY=value`):

```sh
claude mcp add smeltery -s user \
  -e SMELTERY_ROOT=/data/smeltery \
  -- uvx --python 3.12 --from 'smeltery[mcp,attestation] @ git+https://github.com/mgoldey/smeltery' smeltery mcp
```

**Claude Desktop**, in `claude_desktop_config.json` (macOS `~/Library/Application Support/Claude/`, Windows
`%APPDATA%\Claude\`; restart the app after editing). This one file wires both servers, smeltery to run the screen and
attestation to check what it wrote:

```json
{
  "mcpServers": {
    "smeltery": {
      "command": "uvx",
      "args": [
        "--python",
        "3.12",
        "--from",
        "smeltery[mcp,attestation] @ git+https://github.com/mgoldey/smeltery",
        "smeltery",
        "mcp"
      ],
      "env": {
        "SMELTERY_ROOT": "/data/smeltery"
      }
    },
    "attestation": {
      "command": "uvx",
      "args": [
        "--from",
        "git+https://github.com/mgoldey/attestation",
        "attest-mcp"
      ],
      "env": {
        "ATTEST_TOOLS": "provenance",
        "ATTEST_EXPAND": "1",
        "ATTEST_DB": "/data/attestation/ledger.db",
        "RESEARCH_ROOT": "/data/smeltery/.smeltery/screens"
      }
    }
  }
}
```

A desktop app often starts with a shorter `PATH` than your shell: if `uvx` is not found, use the absolute path from
`which uvx`.

**One workspace for both.** A screen with no `out_dir` lands in `SMELTERY_ROOT/.smeltery/screens/<campaign>-<8 hex>`.
attestation's `RESEARCH_ROOT` must be that `screens` directory, not `SMELTERY_ROOT`: its scanner skips hidden
directories below its root, so the root itself shows nothing. With that set, the flow is `estimate_cost` and
`screen_candidates` here, then attestation's `runs.status` for what is in flight (it reads each run's
`attestation/state.json`), this server's `run_status` for per-stage progress, and when the run has finished
attestation's `runs.scan` (with `confirm=true`), `runs.list` and `runs.detail` for the record and its provenance
caveats, with `explain_drop` here for why a molecule was dropped. attestation's `runs.status` and its reader for
smeltery's core record are in attestation 0.5.1; PyPI has 0.5.0, so use its git form (above) until 0.5.1 is out.
The full guide, with the example conversation and the variable table, is attestation's
[docs/guides/claude.md](https://github.com/mgoldey/attestation/blob/main/docs/guides/claude.md).

## The tools

| Tool | Signature | What it does |
|---|---|---|
| `screen_candidates` | `(target, ligand_set, reference_ligand=None, target_class=None, keep=None, campaign=None, out_dir=None, dry_run=False, confirm=False, relax_unmapped=None)` | Plans the screen, checks it against the ceiling, submits it through the campaign `JobBackend` ([jobs.md](jobs.md)) and returns a **handle at once**. It never waits for a calculation. |
| `run_status` | `(handle)` | The job's state, when it last changed, why it stopped if it did, and progress per stage where the run leaves enough on disk to tell. |
| `explain_drop` | `(candidate, handle)` | Which stage dropped a molecule, on what number, against what threshold and what systematic-error floor — all read from that run's own record. No new calculation. |
| `estimate_cost` | `(target, ligand_set, reference_ligand=None, target_class=None, keep=None, relax_unmapped=None)` | Candidates, poses, calculation count, predicted CPU-hours and memory. Submits nothing. |

There is deliberately **no fifth, catch-all tool**. A question none of these answers comes back as a refusal naming
what is answerable, rather than landing in a bucket that answers everything badly.

### Arguments an agent can actually supply

* `target` is a file: a `.pdb` or `.pqr` pocket, or a `.toml` **target definition** ([targets.md](targets.md)):
  `schema_version = 1` plus only the sections that describe the target — `[pocket]` / `[[pocket.site]]`,
  `[structure]`, `[docking]`, `[gates]` — each checked by `smeltery run`'s own validator. Anything else (candidates,
  a measurement section, an unknown key) is refused with the reason: the ligands come from `ligand_set` and the
  measurement from the target class. Relative paths inside it resolve against the file. `smeltery target check FILE`
  runs the same check.
* `relax_unmapped` (optional, `true`/`false`) replaces the class's default (`true`). Each analogue's shapes copy the
  reference molecule's shape for the shared part; `true` then relaxes only the part the analogue adds, holding the
  shared part still. `false` leaves that part exactly as placed, which risks a strained joint the pose geometry check
  refuses as "not this molecule" — a substituted analogue can then stop the screen (tested: octanol against an
  ethanol reference fails this way). An override is listed in the plan's `overrides` (setting, value, class default,
  risk), and a submitted screen's `config.toml` carries the value in `[poses]` plus a comment at its top naming the
  override, so the run is auditable.
* `ligand_set` is `{name: SMILES}` with at least two entries, one of them the reference the others are measured
  against (`reference_ligand`, else the first).
* `handle` is the screen's run directory. `RUN_DIR/attestation/state.json` names the backend and the job, which is
  what `smeltery status RUN_DIR` reads too, so a handle survives a restart and works from another process.
* `out_dir` is optional: with none, a screen goes to `ROOT/.smeltery/screens/<campaign>-<8 hex>`.

### What the outputs carry

`screen_candidates` returns `submitted`, `handle`, `job_id`, `backend`, `state`, the `plan` (class, tier, what the
tier measures in one sentence, reference ligand, `keep`, `z`, `floor` and a note on what the floor means, the cost,
and the class's `why`/`limits`), the `ceiling`, and a `note`. `run_status` returns the job state plus `tiers`: while
the energies run, the measurement stage's finished and planned calculation count (from `OUT/progress.json` and
`OUT/calcs.partial.jsonl`); once the record exists, every stage with candidates in and out. A stage that leaves no
counter is reported as a state — **no percentage is invented**. `explain_drop` returns the verdict
(`dropped` / `survivor` / `undecided` / `reference` / `excluded before the run`), the stage, the paired difference
with its standard error and pose count, the nearest kept molecule, the separation and the separation the stage
needed, the floor with its source and whether that floor was **measured**, and one jargon-free paragraph.

Responses are small: a few dozen fields, no per-pose arrays, no echoed config.

## Target-class defaults

`target_class` is how nobody picks a basis set. The table is **data** —
[`src/smeltery/data/target_classes.toml`](../src/smeltery/data/target_classes.toml) — read by
`smeltery.mcp.targets` and mapped onto the campaign-config sections `smeltery run` already validates. Adding a
class is an edit to that file, not a new branch in code.

**These classes are not protein families, on purpose.** This repository has measured nothing per family: there is no
kinase, GPCR or protease calibration anywhere in `docs/` or `benchmarks/`, so a class named for one would be an
invented chemistry claim. The classes differ only in settings the repository has evidence for, and each carries the
file that evidence is in.

| Class | Measurement tier | Poses | Settings it fixes | Source for the default |
|---|---|---|---|---|
| `protein-pocket-rhf` (default) | `field_interaction` (Hartree-Fock) | 6 | `basis = "sto-3g"`, `relax_unmapped = true`, `keep = 1`, `z = 2.0`, `floor = 0.0` | It is the configuration whose wall time is calibrated: five molecules measured (ethanol 21 bf 0.765 s/pose to ibuprofen 93 bf 91.6 s/pose), an `nbf^2.71` fit and two held-out checks — [tiers/field_interaction.md](tiers/field_interaction.md), `src/smeltery/data/cost_measurements.json`. |
| `protein-pocket-dft` | `field_dft_interaction` (PBE) | 6 | the above plus `functional = "PBE"`, `memory_budget_gb = 8.0` | PBE/STO-3G is the only functional/basis pair with a measured reference (74 atoms, 246 bf; 71.1 s vacuum, 80.7 s in a 624-charge field). 8 GB is the tier's and ferric's own budget; above it ferric spills the RI tensor to disk and re-reads it every iteration — [tiers/field_dft_interaction.md](tiers/field_dft_interaction.md), `src/smeltery/cost.py`. |
| `large-ligand-dft` | `field_dft_interaction` (PBE) | 4 | the above plus `require_free_gb = 12.0` | `memory_budget_gb` is a fixed prediction and cannot see a loaded machine, so this class also reads `MemAvailable` and refuses rather than paging; a long run was measured holding ~11 GB against a ~2 GB per-calculation prediction. Four poses because cost is linear in poses — [tiers/field_dft_interaction.md](tiers/field_dft_interaction.md), `src/smeltery/cost.py`, `src/smeltery/checkpoint.py`. |
| `fast-triage` | `field_interaction` (Hartree-Fock) | 3 | as `protein-pocket-rhf` | Same tier, basis and cost model; only the pose count differs, and two SCF legs per pose makes cost linear in it — [tiers/field_interaction.md](tiers/field_interaction.md). |

Three facts hold for every row, and each is a repository fact rather than a preference:

* **`basis = "sto-3g"` is not a choice, it is the only basis either tier has a cost model for.** Any other basis
  makes `estimate_cost()` return `predicted: None` with the reason.
* **`floor = 0.0` claims no systematic floor.** Both tiers' `systematic_floor()` returns `None`, and `smeltery run`
  refuses to cut on a quantity with no floor unless the config states one (gate G4,
  [tiers/index.md](tiers/index.md)). `0.0` means "nothing is subtracted", not "the systematic error is zero", and
  `explain_drop` says so in its answer: `floor_measured` is `false`.
* **`relax_unmapped = true`** relaxes only the substituent an analogue adds, with the shared core held fixed.
  Without it the junction is left unrelaxed and the pose geometry gate rejects the pose as "not this molecule".
  It is the one class default a caller may replace (`relax_unmapped=false`, see above), and it applies to the docked
  forms too (`[poses] relax_unmapped`, beside docking's own pose count).

**Why only `relax_unmapped` is overridable, and why as a tool argument.** It is about how the *ligands'* shapes are
built, not about the pocket, so it does not belong in a target definition (which describes only the target, by
design); an argument also puts the choice in the same call, and the same plan output, as the class it departs from.
`keep` was already an argument. The basis, tier, pose count, `z` and `floor` stay with the class: they are what its
cost model and its `why` are measured for, so replacing them would hand back numbers the class cannot vouch for.

Each class also carries `why` and `limits` strings, returned with every plan, so the caller sees what the defaults
buy and what they cost (for example: `fast-triage`'s three poses against a measured pose-ensemble noise of
4.07 kcal/mol for an unpaired ddE at n = 100 poses).

## Guardrails

| Variable | Default | Meaning |
|---|---|---|
| `SMELTERY_MAX_CPU_HOURS` | unset — no ceiling | Ceiling on a screen's predicted CPU-hours. |
| `SMELTERY_MAX_CANDIDATES` | unset — no ceiling | Ceiling on a screen's candidate count. |

Both resolve through `smeltery.settings` like every other `SMELTERY_*` variable (flag, then variable, then default;
a bad value exits 2 naming the variable), and `smeltery mcp` takes `--max-cpu-hours` / `--max-candidates` too.

* **Above a ceiling, submission is refused**, and the refusal says how to proceed:
  `refusing to submit: 7 candidates is over the SMELTERY_MAX_CANDIDATES ceiling of 4. To run it anyway, call
  screen_candidates again with confirm=True.` With `confirm=True` the screen is submitted and the `note` records
  that the ceiling was overridden.
* **An unpriceable screen counts as OVER a CPU-hour ceiling, never as under it.** When `smeltery.cost` cannot price
  the settings it returns no number, and a ceiling that passed in that case would be no ceiling at all for exactly
  the configurations smeltery cannot predict. The refusal names the reason the estimate is missing.
* **Dry run: `screen_candidates(..., dry_run=True)`, not a separate `plan_screen` tool.** Both were on the table;
  the flag won because a plan and a submission take exactly the same arguments and run exactly the same planning
  and ceiling code. A second tool would duplicate that argument surface in the schema the model reads, give an
  agent two tools for one intent to choose between, and drift the moment one gained an argument. The flag keeps one
  intent — "screen these molecules" — and one answer shape: a dry run returns the same `Screen` object with
  `submitted: false`, `handle: null`, the ceiling verdict and a note saying nothing was submitted. `estimate_cost`
  stays a separate tool because it answers a different question ("what would this cost?") and returns a different
  shape.
* **Refusals are returned verbatim.** An expected refusal — an unknown target class, a ligand set of one, a handle
  that is not a screen, a ceiling — is raised as the SDK's `ToolError` carrying smeltery's own message, so the agent
  reads the decision. Anything else is a bug: it is logged with its traceback and the caller gets one generic
  sentence (`smeltery could not complete that request; the server logged the error.`), because an internal
  exception is not something an agent can act on. Set `SMELTERY_MCP_LOG=DEBUG` for more on the server's stderr.

## Example session

```text
estimate_cost(target="pocket.pdb", ligand_set={"lead": "...", "analogue-1": "...", "analogue-2": "..."})
  -> cost: 3 candidates, 6 poses each, 36 calculations, 2.41 CPU-hours
     basis: "18 poses x (vacuum + field RHF) at sto-3g; measured benzoic acid (51 bf) 14.9 s scaled by nbf^2.71 ..."
screen_candidates(... )          -> handle "/data/smeltery/.smeltery/screens/screen-pocket-1f0a9c3d", state submitted
run_status(handle)               -> running; measurement 14 of 36 SCF legs
run_status(handle)               -> complete; poses 3/3, pocket 3/3, parity 3/3, field_interaction 3/3, cut 1 of 2 kept
explain_drop("analogue-2", handle)
  -> dropped at the ranking stage: -1.41 kcal/mol (standard error 0.53 over 6 poses) against the kept
     analogue-1's -6.50; they differ by 5.1 and the stage needed more than 4.67 to order them.
```

## Limits

* The tools submit through the campaign job backend, so they need the `attestation` extra; without it every
  submission is refused with that message. `estimate_cost` and `dry_run=True` need only `mcp`.
* The local backend runs a screen under `SMELTERY_MAX_RUNS` run slots shared with Bench ([jobs.md](jobs.md)); with
  `SMELTERY_BACKEND=temporal` the screen is a workflow and needs a worker.
* Predicted CPU-hours assume the measured reference's SCF iteration count, so they are a **lower bound** for a
  molecule that converges more slowly — the tier pages state the measured ratios.
* `explain_drop` reads a finished run's record. Before the record exists it refuses and points at `run_status`.

## Tests

`tests/test_mcp_server.py` drives the tools through the server itself (`list_tools` / `call_tool`) against the local
job backend with the fake engine `tests/test_job_backends.py` uses, so "submitted", "complete" and "dropped" mean
what they mean in production: a handle returned while an SCF hangs, a screen polled to completion, a really dropped
candidate explained, the cost estimate, the dry run, the ceiling refusal followed by `confirm=True`, an unknown
handle, a bug answered generically, and one subprocess test that speaks the protocol over stdio. One test asserts
that no tool description uses an acronym it does not spell out.

The same server, spawned over stdio beside attestation's `attest-mcp`, is driven end to end by an agent transcript
(submit, poll, then ask attestation for the ranking and the provenance): [cross-server.md](cross-server.md).
