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
import tomllib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import targets

#: The quantity the funnel cuts on; mirrors `cli.QUANTITY` (pinned equal in tests/test_mcp_server.py).
QUANTITY = "dE_int"
#: Config sections a target file may carry. Everything else about the screen comes from the ligands and the class.
TARGET_SECTIONS = ("pocket", "structure", "docking", "gates")
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

    A `.pdb` or `.pqr` is the pocket itself; a `.toml` is a target definition holding any of
    `[pocket]`/`[[pocket.site]]`, `[structure]`, `[docking]`, `[gates]` -- the parts of a campaign config that
    describe the target rather than the ligands. Relative file paths inside it are resolved against the file, so
    the sections can be staged into a run directory from anywhere."""
    p = Path(target).expanduser()
    if not p.is_file():
        raise Refusal(f"target {target!r} is not a file; pass a .pdb or .pqr pocket, or a .toml target definition")
    suffix = p.suffix.lower()
    if suffix in (".pdb", ".pqr"):
        return {"pocket": {"file": str(p.resolve())}}
    if suffix != ".toml":
        raise Refusal(f"target {target!r}: expected a .pdb or .pqr pocket, or a .toml target definition")
    try:
        cfg = tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise Refusal(f"cannot read the target definition {target!r}: {e}") from e
    extra = sorted(set(cfg) - set(TARGET_SECTIONS))
    if extra:
        raise Refusal(
            f"the target definition {target!r} holds {extra}, which describe the ligands or the measurement, not "
            f"the target; it may hold only {list(TARGET_SECTIONS)} (the ligands come from ligand_set and the "
            "measurement from the target class)"
        )
    if not cfg:
        raise Refusal(f"the target definition {target!r} is empty; it must describe the pocket")
    return _absolute_paths(cfg, p.resolve().parent)


def _absolute_paths(cfg: dict, base: Path) -> dict:
    from ..run_inputs import input_paths

    out = json.loads(json.dumps(cfg, default=str))
    for table, key in input_paths():
        section = out.get(table)
        value = section.get(key) if isinstance(section, dict) else None
        if isinstance(value, str) and not Path(value).is_absolute() and (base / value).is_file():
            section[key] = str((base / value).resolve())
    return out


def build_config(
    target: str,
    ligand_set: dict[str, str],
    *,
    reference_ligand: str | None = None,
    target_class: str | None = None,
    campaign: str | None = None,
    keep: int | None = None,
) -> tuple[dict, targets.TargetClass]:
    """The campaign config a screen runs, and the class it came from. Validated by `smeltery run`'s own checker."""
    from ..cli import ConfigError, validate_config

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
    if "docking" not in cfg and "structure" not in cfg:
        # With docking the parent's poses are docked and `[poses] n_poses` is refused, so the class's pose count
        # applies only to the undocked form; `plan.poses_per_candidate` reports what was actually built either way.
        cfg["poses"] = {"n_poses": cls.n_poses, "relax_unmapped": cls.relax_unmapped}
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
) -> tuple[dict, Plan]:
    """Build the config, build the plan everything before the SCFs needs, and price it. Submits nothing."""
    from ..cli import ConfigError, StageError, build_plan, memory_estimate

    cfg, cls = build_config(
        target,
        ligand_set,
        reference_ligand=reference_ligand,
        target_class=target_class,
        campaign=campaign,
        keep=keep,
    )
    with tempfile.TemporaryDirectory(prefix="smeltery-plan-") as scratch:
        try:
            built = build_plan(cfg, out_dir=Path(scratch))
        except (ConfigError, StageError) as e:
            raise Refusal(f"the screen cannot be planned: {e}") from e
        est = built.tier.estimate_cost(built.candidates)
        mem = memory_estimate(built)
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
    return cfg, Plan(
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


def _backend(settings, *, root: str | None = None, name: str | None = None):
    from .. import jobs

    if not jobs.AVAILABLE:
        raise Refusal(jobs.NOT_INSTALLED)
    try:
        return jobs.backend_for(settings, root=root, name=name)
    except jobs.JobError as e:
        raise Refusal(str(e)) from e


def _run_dir(out_dir: str | None, campaign: str, settings, root: str | None) -> Path:
    if out_dir is not None:
        return Path(out_dir).expanduser().resolve()
    base = root or settings.root
    if base is None:
        from ..bench.app import default_root

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
    out_dir: str | None = None,
    root: str | None = None,
    dry_run: bool = False,
    confirm: bool = False,
    backend=None,
) -> Screen:
    """Plan, check the ceiling, and (unless `dry_run`) submit. Returns as soon as the job is queued."""
    from .. import jobs

    cfg, built = plan(
        target,
        ligand_set,
        reference_ligand=reference_ligand,
        target_class=target_class,
        campaign=campaign,
        keep=keep,
    )
    ceiling = check_ceiling(built.cost, settings)
    if dry_run:
        note = "Nothing was submitted: this is the plan only."
        if ceiling.exceeded:
            note += f" It is over the configured ceiling ({'; '.join(ceiling.over)}). {HOW_TO_CONFIRM}"
        return Screen(False, None, None, None, None, built, ceiling, note)
    if ceiling.exceeded and not confirm:
        raise Refusal(f"refusing to submit: {'; '.join(ceiling.over)}. {HOW_TO_CONFIRM}")
    run_dir = _run_dir(out_dir, built.campaign, settings, root)
    backend = backend if backend is not None else _backend(settings, root=root)
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = jobs.write_config(run_dir, cfg)
        handle = backend.submit(jobs.campaign_spec(config_path, run_dir))
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
        ),
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


def _finished_tiers(record: dict) -> list[TierProgress]:
    rows = (record.get("results") or {}).get("funnel") or []
    out = []
    for row in rows:
        n_in, n_out = row.get("candidates_in"), row.get("candidates_out")
        kept = "could not be ordered, so nothing was dropped" if n_out is None else f"{n_out} of {n_in} kept"
        out.append(TierProgress(str(row.get("stage")), "done", None, None, "candidates", f"{row.get('kind')}: {kept}"))
    return out


def status(handle: str, *, settings, root: str | None = None, backend=None) -> Status:
    """A screen's state, plus progress per stage where it is knowable.

    Before the run ends, only the measurement stage can be counted: the run writes its planned SCF count and
    appends each finished one (`OUT/progress.json`, `OUT/calcs.partial.jsonl`). The stages before it leave no
    counter, so they are reported as a state and nothing is invented. Once the record exists, every stage's
    candidates in and out come from it."""
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
        running = _scf_progress(run_dir, "running" if st.state.value == "running" else st.state.value)
        if running is not None:
            tiers = [running]
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
