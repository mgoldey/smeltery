"""What the MCP tools actually do, with no MCP in sight.

The tool functions in `smeltery.mcp.server` are thin: they declare arguments, call one function here, and return
its dataclass. Everything in this module is importable without the `mcp` extra, so the behaviour can be tested
(and reused) without a protocol server.

Four intents, in the order a caller meets them:

* `estimate(...)`   build the plan and price it (`smeltery.cost`), submitting nothing;
* `screen(...)`     the same plan, then `JobBackend.submit` (`smeltery.jobs`), returning a handle at once;
* `status(...)`     where a submitted screen is, with per-tier progress where the run leaves enough to tell;
* `explain(...)`    why one candidate is not in the survivors, from the run's own record.

A handle is the run directory: `attestation/state.json` inside it names the backend and the job, which is what
`smeltery status RUN_DIR` uses too (docs/jobs.md), so a handle stays valid across processes and machines.
"""

from __future__ import annotations

import json
import math
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import targets

#: The quantity the funnel cuts on; mirrors `run_view.QUANTITY` (pinned equal in tests/test_mcp_server.py).
QUANTITY = "dE_int"
#: Config sections a target file may carry. Everything else about the screen comes from the ligands and the class.
TARGET_SECTIONS = ("pocket", "structure", "docking", "gates")  # mirrors smeltery.target_file.SECTIONS
#: Where a screen goes when the caller names no directory, under the workspace root.
SCREEN_DIR = Path(".smeltery") / "screens"


class Refusal(Exception):
    """An expected refusal: the caller is told exactly this, verbatim, and can act on it.

    Everything else that escapes is a bug: the server logs it with a traceback and returns a generic message,
    because smeltery's internals are not the caller's to act on."""


# ---- building the campaign config -----------------------------------------------------------------------------


def _ligands(ligand_set: dict[str, str], reference: str | None) -> tuple[dict[str, str], str]:
    if not isinstance(ligand_set, dict) or not ligand_set:
        raise Refusal("ligand_set is empty: pass {name: SMILES, ...} with at least two molecules")
    bad = sorted(k for k, v in ligand_set.items() if not isinstance(v, str) or not v.strip())
    if bad:
        raise Refusal(f"these ligands have no SMILES string: {', '.join(bad)}")
    if len(ligand_set) < 2:
        raise Refusal(
            "a screen compares analogues against one reference molecule, so it needs at least two ligands; "
            f"got {len(ligand_set)} ({', '.join(ligand_set)})"
        )
    ref = reference if reference is not None else next(iter(ligand_set))
    if ref not in ligand_set:
        raise Refusal(f"reference_ligand {ref!r} is not in ligand_set; its names are: {', '.join(ligand_set)}")
    return dict(ligand_set), ref


def target_sections(target: str) -> dict:
    """The pocket/structure part of the config, from the target file.

    A `.pdb` or `.pqr` is the pocket itself; a `.toml` is a target definition (`smeltery.target_file`,
    docs/targets.md): `schema_version = 1` and any of `[pocket]`/`[[pocket.site]]`, `[structure]`, `[docking]`,
    `[gates]`, each checked by `smeltery run`'s own validator. Relative file paths inside it are resolved against the
    file, so the sections can be staged into a run directory from anywhere."""
    from ..target_file import TargetFileError, load

    p = Path(target).expanduser()
    if not p.is_file():
        raise Refusal(f"target {target!r} is not a file; pass a .pdb or .pqr pocket, or a .toml target definition")
    suffix = p.suffix.lower()
    if suffix in (".pdb", ".pqr"):
        return {"pocket": {"file": str(p.resolve())}}
    if suffix != ".toml":
        raise Refusal(f"target {target!r}: expected a .pdb or .pqr pocket, or a .toml target definition")
    try:
        return load(p)
    except TargetFileError as e:
        raise Refusal(str(e)) from e


@dataclass
class Override:
    """A target-class default the caller replaced, recorded so the screen is auditable."""

    setting: str
    value: bool
    class_default: bool
    risk: str


RELAX_RISK = (
    "With relax_unmapped off, the part of each analogue that differs from the reference molecule is placed without "
    "being relaxed, so the joint between the shared core and the new part can be strained; the pose geometry check "
    "may then reject substituted analogues as not being the molecule they claim to be, which stops the screen."
)


def overrides(cls: targets.TargetClass, relax_unmapped: bool | None) -> list[Override]:
    """The class defaults this screen replaces (empty when it takes the class as it is)."""
    if relax_unmapped is None or bool(relax_unmapped) == cls.relax_unmapped:
        return []
    return [Override("relax_unmapped", bool(relax_unmapped), cls.relax_unmapped, "" if relax_unmapped else RELAX_RISK)]


def build_config(
    target: str,
    ligand_set: dict[str, str],
    *,
    reference_ligand: str | None = None,
    target_class: str | None = None,
    campaign: str | None = None,
    keep: int | None = None,
    relax_unmapped: bool | None = None,
) -> tuple[dict, targets.TargetClass]:
    """The campaign config a screen runs, and the class it came from. Validated by `smeltery run`'s own checker."""
    from ..config import ConfigError, validate_config

    try:
        cls = targets.get(target_class)
    except targets.TargetClassError as e:
        raise Refusal(str(e)) from e
    ligands, ref = _ligands(ligand_set, reference_ligand)
    sections = target_sections(target)
    n_keep = cls.keep if keep is None else int(keep)
    if not 1 <= n_keep <= len(ligands) - 1:
        raise Refusal(
            f"keep={n_keep} is not a number of survivors this screen can have: it counts analogues (every ligand "
            f"but the reference), so it must be between 1 and {len(ligands) - 1}"
        )
    section, knobs = cls.tier_section()
    cfg = {
        "campaign": campaign or f"screen-{Path(target).stem}",
        "parent": ref,
        "candidates": ligands,
        **sections,
        section: knobs,
        "cut": {"keep": n_keep, "z": cls.z, "floor": cls.floor},
    }
    if relax_unmapped is not None and not isinstance(relax_unmapped, bool):
        raise Refusal(
            f"relax_unmapped must be true or false (or left out for the class default), got {relax_unmapped!r}"
        )
    relax = cls.relax_unmapped if relax_unmapped is None else relax_unmapped
    if "docking" not in cfg and "structure" not in cfg:
        # With docking the parent's poses are docked and `[poses] n_poses` is refused, so the class's pose count
        # applies only to the undocked form; `plan.poses_per_candidate` reports what was actually built either way.
        cfg["poses"] = {"n_poses": cls.n_poses, "relax_unmapped": relax}
    else:  # docked: the pose count is docking's, but the analogues are still built on the docked parent's poses
        cfg["poses"] = {"relax_unmapped": relax}
    try:
        return validate_config(json.loads(json.dumps(cfg))), cls
    except ConfigError as e:
        raise Refusal(f"the screen's config is not valid: {e}") from e


# ---- planning and pricing --------------------------------------------------------------------------------------


@dataclass
class Cost:
    """What a screen is predicted to cost, and how honest each number is.

    `cpu_hours` is None whenever `smeltery.cost` cannot price these settings -- another basis, an element outside
    its table, a functional it has no reference for. `cpu_hours_basis` then says which, and no number is invented
    in its place."""

    candidates: int
    poses_per_candidate: int
    scf_legs: int
    cpu_hours: float | None
    cpu_hours_basis: str
    memory_gb: float | None
    memory_verdict: str
    memory_basis: str


@dataclass
class Plan:
    """A screen, priced, before anything is submitted."""

    campaign: str
    target_class: str
    tier: str
    measures: str
    reference_ligand: str
    keep: int
    z: float
    floor: float
    floor_note: str
    cost: Cost
    class_why: str
    class_limits: str
    #: Whether each analogue's own part is relaxed after being placed on the reference's shape (see `overrides`).
    relax_unmapped: bool = True
    #: The class defaults the caller replaced; empty when the screen takes its class as it is.
    overrides: list[Override] = field(default_factory=list)


FLOOR_NOTE = (
    "The cut keeps a candidate only when its difference from a neighbour is larger than both the statistical "
    "spread (z x the combined standard error) and this floor. Neither measurement tier has a MEASURED systematic "
    "floor (systematic_floor() returns None), so floor=0.0 means 'no systematic error is subtracted', not 'the "
    "systematic error is zero'."
)


def _measures(tier_name: str) -> str:
    """One jargon-free sentence about what the tier decides. Both tiers measure the same quantity."""
    how = {
        "field_interaction": "Hartree-Fock (no electron correlation)",
        "field_dft_interaction": "density-functional theory, PBE (includes electron correlation)",
    }[tier_name]
    return (
        f"For each pose it computes the ligand's interaction energy with the pocket's electric field using {how}, "
        "in kcal/mol, then ranks each analogue by its paired difference against the reference molecule."
    )


def plan(
    target: str,
    ligand_set: dict[str, str],
    *,
    reference_ligand: str | None = None,
    target_class: str | None = None,
    campaign: str | None = None,
    keep: int | None = None,
    relax_unmapped: bool | None = None,
) -> tuple[dict, Plan]:
    """Build the config, build the plan everything before the SCFs needs, and price it. Submits nothing."""
    cfg, built, _reuse = _plan(
        target,
        ligand_set,
        reference_ligand=reference_ligand,
        target_class=target_class,
        campaign=campaign,
        keep=keep,
        relax_unmapped=relax_unmapped,
    )
    return cfg, built


@dataclass
class Reuse:
    """How many of a screen's stages an identical earlier screen already computed, and will not be recomputed."""

    reused: int
    reusable: int
    why_not: str | None


def _plan(
    target: str,
    ligand_set: dict[str, str],
    *,
    reference_ligand: str | None = None,
    target_class: str | None = None,
    campaign: str | None = None,
    keep: int | None = None,
    relax_unmapped: bool | None = None,
    store=None,
) -> tuple[dict, Plan, Reuse]:
    """`plan`, plus what the stage store (`smeltery.reuse`) already holds of this screen."""
    from ..config import ConfigError, StageError
    from ..planning import build_plan, memory_estimate
    from ..reuse import predict

    cfg, cls = build_config(
        target,
        ligand_set,
        reference_ligand=reference_ligand,
        target_class=target_class,
        campaign=campaign,
        keep=keep,
        relax_unmapped=relax_unmapped,
    )
    with tempfile.TemporaryDirectory(prefix="smeltery-plan-") as scratch:
        try:
            built = build_plan(cfg, out_dir=Path(scratch))
        except (ConfigError, StageError) as e:
            raise Refusal(f"the screen cannot be planned: {e}") from e
        est = built.tier.estimate_cost(built.candidates)
        mem = memory_estimate(built)
        reused, reusable, why_not = predict(built, store)  # last: it fills the plan's candidates from the store
    predicted = est.get("predicted")
    per_candidate = sorted({len(c.poses) for c in built.candidates})
    estimate = mem.get("estimate") or {}
    cost = Cost(
        candidates=len(built.candidates),
        poses_per_candidate=per_candidate[0],
        # Two SCFs per pose (one in vacuum, one in the field); `smeltery.checkpoint.planned_scfs` counts the same.
        scf_legs=2 * sum(len(c.poses) for c in built.candidates),
        cpu_hours=None if predicted is None else float(predicted) / 3600.0,
        cpu_hours_basis=str(est.get("basis") or "no basis recorded"),
        memory_gb=estimate.get("total_gb"),
        memory_verdict=mem["verdict"],
        memory_basis=mem.get("basis") or (f"budget {estimate['budget_gb']:g} GB" if "budget_gb" in estimate else ""),
    )
    return (
        cfg,
        Plan(
            campaign=cfg["campaign"],
            target_class=cls.name,
            tier=built.tier.name,
            measures=_measures(built.tier.name),
            reference_ligand=cfg["parent"],
            keep=cfg["cut"]["keep"],
            z=float(cfg["cut"]["z"]),
            floor=float(cfg["cut"]["floor"]),
            floor_note=FLOOR_NOTE,
            cost=cost,
            class_why=cls.why,
            class_limits=cls.limits,
            relax_unmapped=bool(cfg["poses"]["relax_unmapped"]),
            overrides=overrides(cls, relax_unmapped),
        ),
        Reuse(reused, reusable, why_not),
    )


# ---- the ceiling -----------------------------------------------------------------------------------------------


@dataclass
class Ceiling:
    """The configured ceilings and what this screen does against them."""

    max_cpu_hours: float | None
    max_candidates: int | None
    #: Every reason this screen is over a ceiling; empty means it is under every configured one.
    over: list[str] = field(default_factory=list)
    #: `bool(over)`, as a FIELD rather than a property: a property is not part of the dataclass, so it would be
    #: missing from the tool's output schema and from what a client reads off the wire.
    exceeded: bool = False


HOW_TO_CONFIRM = "To run it anyway, call screen_candidates again with confirm=True."


def check_ceiling(cost: Cost, settings) -> Ceiling:
    """Measure a priced screen against SMELTERY_MAX_CPU_HOURS / SMELTERY_MAX_CANDIDATES.

    An unknown CPU cost counts as OVER a CPU-hour ceiling, never as under it: a ceiling that passes whenever the
    estimator gives up would be no ceiling at all for exactly the configurations smeltery cannot price."""
    ceiling = Ceiling(settings.max_cpu_hours, settings.max_candidates)
    if ceiling.max_candidates is not None and cost.candidates > ceiling.max_candidates:
        ceiling.over.append(
            f"{cost.candidates} candidates is over the SMELTERY_MAX_CANDIDATES ceiling of {ceiling.max_candidates}"
        )
    if ceiling.max_cpu_hours is not None:
        if cost.cpu_hours is None:
            ceiling.over.append(
                f"a ceiling of {ceiling.max_cpu_hours:g} CPU-hours is set (SMELTERY_MAX_CPU_HOURS) and this screen's "
                f"CPU time cannot be estimated ({cost.cpu_hours_basis}), so it cannot be shown to be under it"
            )
        elif cost.cpu_hours > ceiling.max_cpu_hours:
            ceiling.over.append(
                f"{cost.cpu_hours:.2f} predicted CPU-hours is over the SMELTERY_MAX_CPU_HOURS ceiling of "
                f"{ceiling.max_cpu_hours:g}"
            )
    ceiling.exceeded = bool(ceiling.over)
    return ceiling


# ---- submitting ------------------------------------------------------------------------------------------------


@dataclass
class Screen:
    """What `screen_candidates` returns, submitted or not."""

    submitted: bool
    handle: str | None
    job_id: str | None
    backend: str | None
    state: str | None
    plan: Plan
    ceiling: Ceiling
    note: str
    #: Of the stages that can be reused (the energy calculations, and any analysis that runs no outside program),
    #: how many an identical earlier screen in this workspace already finished, so they will not be computed again.
    reused_stages: int = 0
    reusable_stages: int = 0


def _stage_store(settings, root: str | None, backend):
    """The workspace's stage store a local job reuses from; None for a backend that reuses nothing (Temporal)."""
    from ..reuse import workspace_store

    if backend is not None:
        base = getattr(backend, "root", None)
        return None if base is None else workspace_store(base)
    if settings.backend != "local":
        return None
    base = root or settings.root
    if base is None:
        try:
            from ..bench.adapters.workspace_root import default_root
        except ImportError:
            return None
        base = default_root()
    return workspace_store(Path(base).expanduser())


def _reuse_note(reuse: Reuse) -> str:
    if reuse.reused:
        return (
            f" Reused {reuse.reused} of {reuse.reusable} stages: an identical earlier screen in this workspace "
            "already finished them, so they are taken from it, not computed again."
        )
    if reuse.why_not and reuse.reusable:
        return f" Nothing is reused from earlier screens ({reuse.why_not})."
    return ""


def _backend(settings, *, root: str | None = None, name: str | None = None):
    from .. import jobs

    if not jobs.AVAILABLE:
        raise Refusal(jobs.NOT_INSTALLED)
    try:
        return jobs.backend_for(settings, root=root, name=name)
    except jobs.JobError as e:
        raise Refusal(str(e)) from e


def _write_config(run_dir: Path, cfg: dict, changed: list[Override]) -> Path:
    """RUN_DIR/config.toml; a replaced class default is also named in a comment at its top (staging keeps comments),
    beside the value itself in its section, so the run's own config says it was the caller's choice."""
    from .. import jobs

    if not changed:
        return jobs.write_config(run_dir, cfg)
    import tomlkit

    doc = tomlkit.document()
    for o in changed:
        doc.add(
            tomlkit.comment(
                f"screen_candidates override: {o.setting} = {str(o.value).lower()} "
                f"(target class default {str(o.class_default).lower()})"
            )
        )
    for k, v in cfg.items():
        doc[k] = v
    run_dir.mkdir(parents=True, exist_ok=True)
    dest = run_dir / "config.toml"
    dest.write_text(tomlkit.dumps(doc))
    return dest


def _run_dir(out_dir: str | None, campaign: str, settings, root: str | None) -> Path:
    if out_dir is not None:
        return Path(out_dir).expanduser().resolve()
    base = root or settings.root
    if base is None:
        from ..bench.adapters.workspace_root import default_root

        base = default_root()
    return (Path(base).expanduser() / SCREEN_DIR / f"{campaign}-{uuid.uuid4().hex[:8]}").resolve()


def screen(
    target: str,
    ligand_set: dict[str, str],
    *,
    settings,
    reference_ligand: str | None = None,
    target_class: str | None = None,
    campaign: str | None = None,
    keep: int | None = None,
    relax_unmapped: bool | None = None,
    out_dir: str | None = None,
    root: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    backend=None,
    arm: str | None = None,
    ext: dict | None = None,
) -> Screen:
    """Plan, check the ceiling, and (unless `dry_run`) submit. Returns as soon as the job is queued.

    `arm` / `ext` are the submitter's, carried into the campaign job's spec (`jobs.campaign_spec`) so the run states
    them in its core record the first time it writes one. The MCP tools pass neither; `smeltery al` names the loop's
    iteration arm and its own `ext.smeltery.al` namespace (docs/active-learning.md)."""
    from .. import jobs

    cfg, built, reuse = _plan(
        target,
        ligand_set,
        reference_ligand=reference_ligand,
        target_class=target_class,
        campaign=campaign,
        keep=keep,
        relax_unmapped=relax_unmapped,
        store=_stage_store(settings, root, backend),
    )
    ceiling = check_ceiling(built.cost, settings)
    counts = {"reused_stages": reuse.reused, "reusable_stages": reuse.reusable}
    if dry_run:
        note = "Nothing was submitted: this is the plan only."
        if ceiling.exceeded:
            note += f" It is over the configured ceiling ({'; '.join(ceiling.over)}). {HOW_TO_CONFIRM}"
        return Screen(False, None, None, None, None, built, ceiling, note + _reuse_note(reuse), **counts)
    if ceiling.exceeded and not confirm:
        raise Refusal(f"refusing to submit: {'; '.join(ceiling.over)}. {HOW_TO_CONFIRM}")
    run_dir = _run_dir(out_dir, built.campaign, settings, root)
    backend = backend if backend is not None else _backend(settings, root=root)
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = _write_config(run_dir, cfg, built.overrides)
        handle = backend.submit(jobs.campaign_spec(config_path, run_dir, arm=arm, ext=ext))
    except jobs.JobError as e:  # an existing run, a backend that is down: the caller is told which
        raise Refusal(str(e)) from e
    except OSError as e:
        raise Refusal(f"cannot write the screen into {run_dir}: {e}") from e
    state = backend.status(handle)
    return Screen(
        True,
        str(run_dir),
        handle.job_id,
        handle.backend,
        state.state.value,
        built,
        ceiling,
        "Submitted. Poll run_status(handle) for its state; nothing here waited for the calculations."
        + (
            f" The configured ceiling was overridden with confirm=True ({'; '.join(ceiling.over)})."
            if confirm and ceiling.exceeded
            else ""
        )
        + _reuse_note(reuse),
        **counts,
    )


# ---- status ----------------------------------------------------------------------------------------------------


@dataclass
class TierProgress:
    """One stage of the run, and how far it is -- where the run leaves enough on disk to tell."""

    stage: str
    state: str
    done: int | None
    total: int | None
    unit: str
    note: str


@dataclass
class Status:
    """Where a submitted screen is."""

    handle: str
    job_id: str
    backend: str
    state: str
    updated_at: str
    message: str | None
    finished: bool
    record: str | None
    tiers: list[TierProgress]


def _handle(handle: str, settings, root: str | None, backend=None):
    from .. import jobs

    if not jobs.AVAILABLE:
        raise Refusal(jobs.NOT_INSTALLED)
    run_dir = Path(handle).expanduser()
    try:
        h = jobs.handle_in(run_dir)
    except jobs.JobNotFoundError as e:
        raise Refusal(
            f"unknown handle {handle!r}: {e}. A handle is the run directory a screen was submitted into; it is the "
            "`handle` field screen_candidates returned."
        ) from e
    except (ValueError, OSError) as e:
        raise Refusal(f"unknown handle {handle!r}: {e}") from e
    return h, (backend if backend is not None else _backend(settings, root=root, name=h.backend))


def _record_path(run_dir: Path) -> Path | None:
    found = sorted(run_dir.glob("run-*.json"))
    return found[0] if found else None


def _scf_progress(run_dir: Path, state: str) -> TierProgress | None:
    """SCF legs finished against legs planned, from what the run writes while it runs (`smeltery.checkpoint`)."""
    from ..checkpoint import PARTIAL, PROGRESS

    try:
        doc = json.loads((run_dir / PROGRESS).read_text())
        total = int(doc["total_scfs"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    try:
        done = sum(1 for line in (run_dir / PARTIAL).read_text().splitlines() if line.strip())
    except OSError:
        done = 0
    return TierProgress(
        "measurement",
        state,
        done,
        total,
        "SCF legs",
        "Each pose needs two of these: one calculation in vacuum and one in the pocket's field.",
    )


REUSED_NOTE = "taken from an identical earlier run (run {run}), not computed again"


def _reused(entry: dict | None) -> str | None:
    """The note for a stage that was reused, else None."""
    if not entry or not entry.get("reused"):
        return None
    return REUSED_NOTE.format(run=(entry.get("reused_from") or {}).get("run_id"))


def _finished_tiers(record: dict) -> list[TierProgress]:
    results = record.get("results") or {}
    rows = results.get("funnel") or []
    reuse = {e.get("stage"): e for e in (results.get("reuse") or {}).get("stages") or []}
    out = []
    for row in rows:
        n_in, n_out = row.get("candidates_in"), row.get("candidates_out")
        kept = "could not be ordered, so nothing was dropped" if n_out is None else f"{n_out} of {n_in} kept"
        note = f"{row.get('kind')}: {kept}"
        reused = _reused(reuse.get(row.get("stage")))
        state = "done" if reused is None else "reused"
        out.append(
            TierProgress(
                str(row.get("stage")), state, None, None, "candidates", note + (f"; {reused}" if reused else "")
            )
        )
    return out


def _logged_tiers(run_dir: Path) -> list[TierProgress]:
    """The stages a live or stopped run has completed, from its stage log (`smeltery.reuse`)."""
    from ..reuse import read_progress

    out = []
    for e in read_progress(run_dir) or []:
        reused = _reused(e)
        out.append(
            TierProgress(
                str(e["stage"]),
                "done" if reused is None else "reused",
                None,
                None,
                "stage",
                reused or "finished",
            )
        )
    return out


def status(handle: str, *, settings, root: str | None = None, backend=None) -> Status:
    """A screen's state, plus progress per stage where it is knowable.

    Before the run ends, the stages it has completed come from its stage log (`OUT/stages.partial.jsonl`), each
    "done" or "reused" (taken from an identical earlier run), and the measurement in progress is counted: the run
    writes its planned SCF count and appends each finished one (`OUT/progress.json`, `OUT/calcs.partial.jsonl`). A
    cancelled or failed screen keeps the stages it completed. Once the record exists, every stage's candidates in and
    out come from it."""
    from .. import jobs

    h, backend = _handle(handle, settings, root, backend)
    try:
        st = backend.status(h)
    except jobs.JobError as e:
        raise Refusal(str(e)) from e
    run_dir = Path(h.run_dir)
    record = _record_path(run_dir)
    finished = st.state in jobs.TERMINAL
    tiers: list[TierProgress] = []
    if record is not None:
        try:
            tiers = _finished_tiers(json.loads(record.read_text()))
        except (OSError, ValueError):
            tiers = []
    if not tiers:
        logged = _logged_tiers(run_dir)
        measured = any(t.stage in ("field_interaction", "field_dft_interaction") for t in logged)
        running = None if measured else _scf_progress(run_dir, st.state.value)
        if logged or running is not None:
            tiers = logged + ([running] if running is not None else [])
        else:
            tiers = [
                TierProgress(
                    "measurement",
                    "not started" if st.state.value == "submitted" else st.state.value,
                    None,
                    None,
                    "SCF legs",
                    "The run has not written its planned calculation count yet, so there is nothing to count.",
                )
            ]
    return Status(
        handle=str(run_dir),
        job_id=h.job_id,
        backend=h.backend,
        state=st.state.value,
        updated_at=st.updated_at,
        message=st.message,
        finished=finished,
        record=str(record) if record is not None else None,
        tiers=tiers,
    )


# ---- explaining a drop -----------------------------------------------------------------------------------------


@dataclass
class Drop:
    """Why one candidate is not among the survivors, read from the run's own record."""

    candidate: str
    handle: str
    verdict: str
    stage: str | None
    quantity: str
    unit: str
    value: float | None
    standard_error: float | None
    n_poses: int | None
    compared_with: str | None
    compared_value: float | None
    separation: float | None
    separation_needed: float | None
    floor: float | None
    floor_source: str | None
    floor_measured: bool
    z: float | None
    keep: int | None
    explanation: str


def _declared_floor(record: dict) -> tuple[float | None, str]:
    """The MEASUREMENT tier's own declared floor for the cut quantity, through the tier protocol."""
    from ..dataset_export import tier_declarations

    tiers = record.get("tiers") or []
    units = (record.get("results") or {}).get("units") or {}
    if not tiers:
        return None, "the record names no tier"
    for declared in tier_declarations(tiers[-1], units):
        if declared.quantity == QUANTITY:
            return declared.floor, declared.floor_source
    return None, "the measurement tier declares no floor for this quantity"


def explain(candidate: str, handle: str, *, settings, root: str | None = None, backend=None) -> Drop:
    """Which stage dropped `candidate`, on what number, against what threshold and floor."""
    h, _backend_unused = _handle(handle, settings, root, backend)
    run_dir = Path(h.run_dir)
    record_path = _record_path(run_dir)
    if record_path is None:
        state = json.loads((run_dir / "attestation" / "state.json").read_bytes())["state"]
        raise Refusal(
            f"the screen at {handle} is {state} and has written no run record, so no candidate has been dropped "
            "yet; call run_status(handle) and try again when it is complete"
        )
    try:
        record = json.loads(record_path.read_text())
    except (OSError, ValueError) as e:
        raise Refusal(f"cannot read the run record {record_path}: {e}") from e
    results = record.get("results") or {}
    inputs = record.get("inputs") or {}
    names = list((inputs.get("candidates") or {}))
    excluded = ((results.get("decisions_in") or {}).get("excluded")) or []
    unit = (results.get("units") or {}).get(QUANTITY, "kcal/mol")
    floor_cfg = inputs.get("floor") or {}
    floor, floor_source = floor_cfg.get("value"), floor_cfg.get("source")
    declared, declared_source = _declared_floor(record)
    cut = results.get("cut") or {}
    z = (inputs.get("cut") or {}).get("z")
    keep = (inputs.get("cut") or {}).get("keep")
    base = {
        "candidate": candidate,
        "handle": str(run_dir),
        "quantity": QUANTITY,
        "unit": unit,
        "floor": floor,
        "floor_source": floor_source,
        "floor_measured": declared is not None,
        "z": z,
        "keep": keep,
        "value": None,
        "standard_error": None,
        "n_poses": None,
        "compared_with": None,
        "compared_value": None,
        "separation": None,
        "separation_needed": None,
    }
    if candidate in excluded:
        return Drop(
            **base,
            verdict="excluded before the run",
            stage="decisions",
            explanation=(
                f"{candidate} never ran: a decisions file given with this screen rejected it, so it was dropped "
                "before any pose was built."
            ),
        )
    if candidate not in names:
        raise Refusal(f"{candidate!r} is not a candidate of this run; its candidates are: {', '.join(names)}")
    if candidate == inputs.get("parent"):
        return Drop(
            **base,
            verdict="reference",
            stage=None,
            explanation=(
                f"{candidate} is the reference molecule every other ligand is measured against, so it is never "
                "ranked or dropped."
            ),
        )
    paired = (results.get("paired_ddE") or {}).get(candidate) or {}
    base |= {
        "value": paired.get("mean"),
        "standard_error": paired.get("sem"),
        "n_poses": paired.get("n"),
    }
    floor_text = (
        f"The systematic-error floor was {floor:g} {unit} ({floor_source})."
        if floor is not None
        else "No systematic-error floor was recorded."
    ) + (
        ""
        if declared is not None
        else f" The measurement tier declares no measured floor of its own ({declared_source}), so a difference "
        "smaller than the tier's unknown systematic error cannot be told from a real one."
    )
    survivors = cut.get("survivors")
    if cut.get("unranked"):
        return Drop(
            **base,
            verdict="undecided",
            stage="cut",
            explanation=(
                f"Nothing was dropped: the ranking stopped because the cut-off between keeping {keep} and dropping "
                "the rest fell inside a group of candidates the measurements cannot tell apart. "
                f"{candidate} scored {_fmt(paired.get('mean'))} {unit} (standard error "
                f"{_fmt(paired.get('sem'))}), lower being better. {floor_text}"
            ),
        )
    if survivors is not None and candidate in survivors:
        return Drop(
            **base,
            verdict="survivor",
            stage="cut",
            explanation=(
                f"{candidate} was kept: it is one of the {keep} survivors, scoring {_fmt(paired.get('mean'))} "
                f"{unit} (standard error {_fmt(paired.get('sem'))}), lower being better. {floor_text}"
            ),
        )
    rival, rival_value, needed = _boundary(results, candidate, survivors or [], z, floor)
    base |= {
        "compared_with": rival,
        "compared_value": rival_value,
        "separation": (
            None
            if rival_value is None or paired.get("mean") is None
            else abs(float(paired["mean"]) - float(rival_value))
        ),
        "separation_needed": needed,
    }
    rival_text = (
        ""
        if rival is None
        else (
            f" The worst-scoring molecule that was kept, {rival}, scored {_fmt(rival_value)} {unit}; the two differ "
            f"by {_fmt(base['separation'])}, and the stage needed more than {_fmt(needed)} to call them apart "
            f"({z} x their combined standard error, and more than the floor)."
        )
    )
    return Drop(
        **base,
        verdict="dropped",
        stage="cut",
        explanation=(
            f"{candidate} was dropped at the ranking stage. Its interaction energy difference against the "
            f"reference molecule was {_fmt(paired.get('mean'))} {unit} (standard error {_fmt(paired.get('sem'))} "
            f"over {paired.get('n')} poses), and lower is better, so it ranked below the {keep} kept."
            f"{rival_text} {floor_text}"
        ),
    )


def _fmt(x) -> str:
    return "unknown" if x is None else f"{float(x):.3g}"


def _boundary(results: dict, candidate: str, survivors: list[str], z, floor):
    """The kept candidate nearest the boundary, its score, and the separation the cut needed to order the two.

    `funnel.resolved`: the difference must exceed BOTH z x hypot(sem_a, sem_b) and the floor, so the number
    reported here is the larger of the two."""
    paired = results.get("paired_ddE") or {}
    mine = paired.get(candidate) or {}
    kept = [(n, paired[n]) for n in survivors if n in paired]
    if not kept or mine.get("mean") is None:
        return None, None, None
    rival, row = max(kept, key=lambda kv: float(kv[1].get("mean", 0.0)))  # the worst-scoring survivor
    sem_a, sem_b = mine.get("sem"), row.get("sem")
    needed = None
    if sem_a is not None and sem_b is not None and z is not None:
        needed = float(z) * math.hypot(float(sem_a), float(sem_b))
        if floor is not None:
            needed = max(needed, float(floor))
    return rival, row.get("mean"), needed
