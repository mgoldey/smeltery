"""`smeltery mcp`: four coarse tools over the campaign funnel, for an agent with no quantum chemistry.

The surface is INTENT-shaped, not config-shaped. A caller says which target, which ligands and which target class;
it never picks a basis set, a functional, a convergence threshold or a pose count. Those come from the
target-class table (`src/smeltery/data/target_classes.toml`), which states a source for every default.

    screen_candidates(target, ligand_set, ...)   plan + submit, returns a handle at once (dry_run=True: plan only)
    run_status(handle)                           where that screen is, with per-stage progress where it is knowable
    explain_drop(candidate, handle)              why one molecule is not in the survivors
    estimate_cost(target, ligand_set, ...)       CPU-hours, candidates and memory, submitting nothing

There is deliberately no fifth, catch-all tool: a question none of these four answers should come back as a
refusal naming what IS answerable, not land in a bucket that answers everything badly.

Errors. An expected refusal -- an unknown target class, a ligand set of one, a screen over the configured ceiling
-- is raised as `ToolError` carrying the refusal text, so the caller reads exactly what smeltery decided. Anything
else is a bug: it is logged here with its traceback and the caller gets one generic sentence, because an internal
exception is not something an agent can act on.

Transport is stdio (`smeltery mcp`), the form an MCP client spawns. The server is built by `build_server()`, which
tests drive in process through `list_tools()` / `call_tool()` without any transport at all.
"""

from __future__ import annotations

import argparse
import functools
import logging
import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from ..settings import SettingsError, resolve
from . import screening, targets
from .screening import Cost, Drop, Plan, Refusal, Screen, Status

log = logging.getLogger(__name__)

#: What the caller is told when a tool fails for a reason that is not an expected refusal. Deliberately free of
#: internals: the traceback goes to the server's log, not to the agent.
GENERIC = "smeltery could not complete that request; the server logged the error."

SERVER_NAME = "smeltery"


@dataclass
class ToolContext:
    """What every tool needs and no tool should take as an argument: the deployment settings and the workspace."""

    settings: object
    root: str | None = None
    #: A ready JobBackend, used instead of building one from the settings. Only tests pass it (they point the
    #: local backend's runner at a fake engine); a served session leaves it None.
    backend: object | None = None


def _guard(fn):
    """Expected refusals reach the caller verbatim; anything else is logged and answered generically."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Refusal as e:
            raise ToolError(str(e)) from e
        except ToolError:
            raise
        except Exception as e:  # noqa: BLE001 - the presentation envelope: a bug must not reach the caller as a
            # traceback, and must not be mistaken for a refusal it could act on. Logged in full here.
            log.exception("smeltery mcp: %s failed", fn.__name__)
            raise ToolError(GENERIC) from e

    return wrapper


def _class_list() -> str:
    return "; ".join(f"{c['name']} ({c['summary']})" for c in targets.catalogue())


def build_server(ctx: ToolContext | None = None) -> MCPServer:
    """The server, with its four tools registered. `ctx` is for tests: it pins the settings and the workspace."""
    ctx = ctx if ctx is not None else ToolContext(resolve(os.environ))
    server = MCPServer(SERVER_NAME, instructions=INSTRUCTIONS)
    classes = _class_list()

    @server.tool(
        name="screen_candidates",
        description=(
            "Rank candidate molecules against one reference molecule in a target's binding pocket, and return a "
            "handle immediately -- the calculations run in the background and this call never waits for them. "
            "`target` is a file: a .pdb or .pqr pocket, or a .toml target definition holding the pocket, the "
            "structure or the docking box. `ligand_set` maps each molecule's name to its SMILES string (the plain "
            "text form of a molecule, e.g. 'CCO' for ethanol); it needs at least two, one of which is the "
            "reference. `target_class` chooses the settings -- how the energies are computed, how many shapes "
            "(poses) of each molecule are tried, how strict the ranking is -- so that nobody has to pick a basis "
            f"set or a functional. The classes are: {classes}. Set dry_run=True to get the plan and its predicted "
            "cost without submitting anything. A screen over the configured ceiling is refused unless "
            "confirm=True. Returns the handle, the plan, and what the ceiling said."
        ),
    )
    @_guard
    def screen_candidates(
        target: str,
        ligand_set: dict[str, str],
        reference_ligand: str | None = None,
        target_class: str | None = None,
        keep: int | None = None,
        campaign: str | None = None,
        out_dir: str | None = None,
        dry_run: bool = False,
        confirm: bool = False,
    ) -> Screen:
        return screening.screen(
            target,
            ligand_set,
            settings=ctx.settings,
            reference_ligand=reference_ligand,
            target_class=target_class,
            campaign=campaign,
            keep=keep,
            out_dir=out_dir,
            root=ctx.root,
            backend=ctx.backend,
            dry_run=dry_run,
            confirm=confirm,
        )

    @server.tool(
        name="run_status",
        description=(
            "Where a screen is: submitted, running, complete, failed or cancelled, when that last changed, and why "
            "if it stopped early. `handle` is what screen_candidates returned (the screen's directory). It also "
            "reports progress per stage where the run leaves enough on disk to tell: while the energies are being "
            "computed, how many of the planned calculations have finished; once the screen is complete, how many "
            "molecules went into each stage and how many came out. A stage that leaves no counter is reported as a "
            "state rather than as a made-up percentage."
        ),
    )
    @_guard
    def run_status(handle: str) -> Status:
        return screening.status(handle, settings=ctx.settings, root=ctx.root, backend=ctx.backend)

    @server.tool(
        name="explain_drop",
        description=(
            "Why one molecule is not among the survivors of a finished screen, read from that run's own record -- "
            "no new calculation and no guessing. Says which stage decided (only the final ranking stage removes "
            "molecules; every earlier stage either passes everything or stops the whole run), the number it was "
            "ranked on (its interaction-energy difference from the reference molecule, in kcal/mol, where lower is "
            "better), the standard error of that number, the nearest molecule that was kept, and how far apart the "
            "two had to be before the stage would order them: the statistical threshold and the systematic-error "
            "floor. It also says whether that floor was actually measured for this kind of calculation, or "
            "merely stated in the configuration."
        ),
    )
    @_guard
    def explain_drop(candidate: str, handle: str) -> Drop:
        return screening.explain(candidate, handle, settings=ctx.settings, root=ctx.root, backend=ctx.backend)

    @server.tool(
        name="estimate_cost",
        description=(
            "What a screen would cost, without submitting it: how many candidate molecules, how many shapes "
            "(poses) of each, how many quantum-chemistry calculations that is, the predicted CPU-hours, and the "
            "memory the largest calculation needs against the budget. Takes the same arguments as "
            "screen_candidates. The CPU-hours figure is null whenever smeltery's cost model cannot honestly price "
            "these settings -- a basis set or element it has never measured -- and the accompanying basis says "
            "which; no number is invented in its place. Where a figure does exist it is a prediction from measured "
            "reference runs, and a lower bound for a molecule whose calculations converge more slowly than the "
            "reference did."
        ),
    )
    @_guard
    def estimate_cost(
        target: str,
        ligand_set: dict[str, str],
        reference_ligand: str | None = None,
        target_class: str | None = None,
        keep: int | None = None,
    ) -> Estimate:
        _cfg, built = screening.plan(
            target,
            ligand_set,
            reference_ligand=reference_ligand,
            target_class=target_class,
            keep=keep,
        )
        ceiling = screening.check_ceiling(built.cost, ctx.settings)
        return Estimate(
            plan=built,
            cost=built.cost,
            ceiling_exceeded=ceiling.exceeded,
            ceiling_says=ceiling.over,
            max_cpu_hours=ceiling.max_cpu_hours,
            max_candidates=ceiling.max_candidates,
        )

    return server


@dataclass
class Estimate:
    """`estimate_cost`'s answer: the plan, its price, and what the ceiling makes of it."""

    plan: Plan
    cost: Cost
    ceiling_exceeded: bool
    ceiling_says: list[str]
    max_cpu_hours: float | None
    max_candidates: int | None


INSTRUCTIONS = (
    "smeltery ranks candidate molecules in a protein pocket by computing, for each one, the energy of its "
    "interaction with the pocket's electric field, and comparing that against a reference molecule. Start with "
    "estimate_cost or screen_candidates(dry_run=True) to see what a screen would cost, then screen_candidates to "
    "submit it, run_status to poll, and explain_drop to ask why a molecule did not survive. Nothing here waits "
    "for a calculation, and no tool invents a number it could not compute."
)


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery mcp`: serve the four tools over stdio. 2 for a bad setting."""
    env = os.environ if env is None else env
    ap = argparse.ArgumentParser(
        prog="smeltery mcp",
        description="Serve smeltery's screening tools to an MCP client over stdio (docs/mcp.md).",
    )
    ap.add_argument("--backend", default=None, help="local (default) or temporal; env SMELTERY_BACKEND")
    ap.add_argument("--root", default=None, help="workspace holding the job queue and screens; env SMELTERY_ROOT")
    ap.add_argument("--max-cpu-hours", dest="max_cpu_hours", default=None, help="env SMELTERY_MAX_CPU_HOURS")
    ap.add_argument("--max-candidates", dest="max_candidates", default=None, help="env SMELTERY_MAX_CANDIDATES")
    ap.add_argument("--list-tools", action="store_true", help="print the tool names and exit (no server)")
    args = ap.parse_args(argv)
    try:
        settings = resolve(
            env,
            backend=args.backend,
            root=args.root,
            max_cpu_hours=args.max_cpu_hours,
            max_candidates=args.max_candidates,
        )
    except SettingsError as e:
        print(f"smeltery mcp: {e}", file=sys.stderr)
        return 2
    server = build_server(ToolContext(settings, args.root))
    if args.list_tools:
        import asyncio

        print("\n".join(t.name for t in asyncio.run(server.list_tools())))
        return 0
    logging.basicConfig(stream=sys.stderr, level=os.environ.get("SMELTERY_MCP_LOG", "INFO"))
    server.run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
