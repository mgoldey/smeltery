"""The plan/run use case of `smeltery run`, with no argparse and no printing: what the CLI, the Temporal runner
(`smeltery.temporal_run`) and Bench (`smeltery.bench.use_cases.impact`) all execute.

`build_plan` does everything before the field-interaction SCFs (structure prep, docking, pairing, the pocket, parity,
the gates, the floor) and returns a `Plan`; `run_plan` runs the measurement, pairs, cuts and builds the run record.
What a run then writes under OUT is `smeltery.run_outputs`. `smeltery.cli` re-exports every name here (the same
objects) and holds only the command line around them.
"""

from __future__ import annotations

import contextvars
import dataclasses
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from rdkit import Chem

from .config import ConfigError, StageError, gate_keys
from .cost import available_memory_gb
from .decisions import DecisionSet
from .funnel import cut, paired_delta, tier_floor, unpaired_delta
from .gates import PoseGateError, check_candidate_poses, require_passing_poses
from .generate.record import GenerationFileError, generation_block, load_generation
from .methods import Analysis, FieldSource, Measure, MethodSpec, PoseSource, active, active_hooks, registry
from .model import Candidate, PointCharge
from .pocket import load_pocket
from .pocket.loader import file_digest
from .record import RunRecord, input_digest
from .run_view import QUANTITY
from .tiers import (
    FieldDftInteraction,
    FieldInteraction,
    ForceField,
    Gfn2,
    PairedPoses,
    net_charge,
    parity_problem,
)


@dataclass
class Plan:
    """Everything decided before the SCFs run: candidates with poses, the field, the tiers, the inputs."""

    cfg: dict
    candidates: list[Candidate]
    parent: Candidate
    field: list[PointCharge]
    poses: PairedPoses
    tier: FieldInteraction | FieldDftInteraction
    floor: float
    floor_source: str
    before_poses: list = dataclasses.field(default_factory=list)  # tiers that ran before pairing (docking)
    after_poses: list = dataclasses.field(default_factory=list)  # recorded tiers between pairing and the SCFs
    extra_inputs: dict = dataclasses.field(default_factory=dict)  # file digests, box: what the stages read
    stage_log: list = dataclasses.field(default_factory=list)  # one dict per stage: its name and a summary line
    receptor_pdb: Path | None = None  # a receptor .pdb in the poses' frame, when one exists
    decisions_in: dict | None = None  # {"file", "sha256", "excluded"} when [decisions] was given
    generation_in: dict | None = None  # {"file", "sha256", "block"} when [generation] was given
    gate_results: dict = dataclasses.field(default_factory=dict)  # results["gates"]: per-pose gate results that ran
    stage_seconds: dict = dataclasses.field(default_factory=dict)  # this plan's wall time per stage (`_stage`)
    structure_fetch: dict | None = None  # results["structure_fetch"]: when [structure] was fetched (not an input)

    @property
    def inputs(self) -> dict:
        gates = self.cfg.get("gates", {})
        inputs = {
            "candidates": {c.name: c.smiles for c in self.candidates},
            "parent": self.parent.name,
            "cut": {"keep": self.cfg["cut"]["keep"], "z": self.cfg["cut"].get("z", 2.0)},
            "floor": {"value": self.floor, "source": self.floor_source},
            "gates": {k: bool(gates.get(k, False)) for k in sorted(gate_keys())},
            **self.extra_inputs,
        }
        if "max_strain_kcal" in gates:  # only when set, so configs from before this knob keep their digests
            inputs["gates"]["max_strain_kcal"] = float(gates["max_strain_kcal"])
        if "pocket" not in self.extra_inputs:  # sites: the charges ARE the input; a file: its digest is
            inputs["field"] = [(c.q, c.xyz_ang) for c in self.field]
        if self.decisions_in is not None:  # only when given, so configs without [decisions] keep their digests
            inputs["decisions"] = {"sha256": self.decisions_in["sha256"]}
        if self.generation_in is not None:  # likewise: configs without [generation] keep their digests
            inputs["generation"] = {"sha256": self.generation_in["sha256"]}
        return inputs

    @property
    def tiers(self) -> list[dict]:
        ordered = [*self.before_poses, self.poses, *self.after_poses, self.tier]
        return [{"name": t.name, **t.settings()} for t in ordered]

    @property
    def input_digest(self) -> str:
        """The run record's `input_digest` for this plan (`record.input_digest` of its inputs and tiers)."""
        return input_digest(self.inputs, self.tiers)


def _canonical(smiles: str) -> str:
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


def _read_generation(cfg: dict) -> dict | None:
    """Read `[generation] file` (smeltery.generate.record) for the record, or None without the section."""
    if "generation" not in cfg:
        return None
    path = _resolve(cfg, cfg["generation"]["file"])
    if not path.is_file():
        raise ConfigError(f"[generation] file {str(path)!r} does not exist")
    try:
        doc = load_generation(path)
    except GenerationFileError as e:
        raise ConfigError(f"[generation] {e}") from e
    return {"file": str(path.resolve()), "sha256": file_digest(path), "block": generation_block(doc)}


def _apply_decisions(cfg: dict) -> tuple[dict, dict | None]:
    """Read `[decisions] file` and drop the rejected candidates. Returns the config to plan from (a copy whose
    [candidates] omit them; the input is not modified) and `decisions_in` for the record, or (cfg, None) without
    the section. Every refusal is a ConfigError (module docstring)."""
    if "decisions" not in cfg:
        return cfg, None
    path = _resolve(cfg, cfg["decisions"]["file"])
    if not path.is_file():
        raise ConfigError(f"[decisions] file {str(path)!r} does not exist")
    try:
        ds = DecisionSet.load(path)
    except ValueError as e:
        raise ConfigError(f"[decisions] {e}") from e
    cands = cfg["candidates"]
    unknown = [d.candidate for d in ds.decisions if d.candidate not in cands]
    if unknown:
        raise ConfigError(f"[decisions] {path.name} decides on {unknown}, which [candidates] does not list")
    for d in ds.decisions:
        if _canonical(d.smiles) != _canonical(cands[d.candidate]):
            raise ConfigError(
                f"[decisions] {d.candidate!r} was decided on as {d.smiles!r}, but [candidates] has "
                f"{cands[d.candidate]!r}: a different molecule under the same name"
            )
    excluded = ds.by_status("reject")
    if cfg["parent"] in excluded:
        raise ConfigError(
            f"[decisions] rejects the parent {cfg['parent']!r}: every analogue is measured against it; "
            "choose another parent or drop the reject"
        )
    kept = {n: s for n, s in cands.items() if n not in excluded}
    n_left = len(kept) - 1
    if n_left < 1 or cfg["cut"]["keep"] > n_left:
        raise ConfigError(
            f"[decisions] rejects {excluded}, leaving {n_left} analogue(s); [cut] keep = {cfg['cut']['keep']} "
            "needs at least that many"
        )
    info = {"file": str(path.resolve()), "sha256": file_digest(path), "excluded": excluded}
    return {**cfg, "candidates": kept}, info


#: Wall time per stage name, summed over its `_stage` calls: the dict of the plan being built or run in this context
#: (`build_plan` starts a fresh one and keeps it on the plan; `run_plan` makes it current again). A context variable,
#: not a module global, so two runs on one worker (threads copy the context) never write each other's seconds.
_STAGE_SECONDS: contextvars.ContextVar[dict[str, float] | None] = contextvars.ContextVar("stage_seconds", default=None)


def _stage_seconds() -> dict[str, float]:
    d = _STAGE_SECONDS.get()
    if d is None:
        d = {}
        _STAGE_SECONDS.set(d)
    return d


def _stage(name: str, fn, log: list | None = None):
    """Run one stage, converting an unexpected failure to a StageError and timing it.

    The seconds go to every `stage_log` entry of that stage: those already in `log` (a stage that logs from
    inside `fn`) now, and those `_log` writes after the call returns.
    """
    t0 = time.perf_counter()
    secs = _stage_seconds()
    try:
        return fn()
    except (ConfigError, StageError):
        raise
    except Exception as e:
        raise StageError(f"stage {name!r} failed: {type(e).__name__}: {e}", stage=name) from e
    finally:
        secs[name] = secs.get(name, 0.0) + (time.perf_counter() - t0)
        for e in log or []:
            if e["stage"] == name:
                e["seconds"] = secs[name]


def _log(log: list, stage: str, summary: str, n_in: int | None, n_out: int | None, kind: str, **extra) -> None:
    """One stage entry: its name, a summary line, and how many candidates went in and came out.

    `kind`: prep (no candidates yet), search (parent only), pair, field, gate (rejects loudly, so a run that
    reaches the next stage has n_out == n_in), recorded (measured and kept, no rejection threshold).
    """
    entry = {
        "stage": stage,
        "summary": summary,
        "kind": kind,
        "candidates_in": n_in,
        "candidates_out": n_out,
        # wall time of that stage's `_stage` call(s); None when it was never timed (not "took 0 s")
        "seconds": _stage_seconds().get(stage),
    }
    log.append({**entry, **extra})


def _place_sites(parent: Candidate, sites: list[dict]) -> list[PointCharge]:
    xyz, n = parent.poses[0].coords_ang, len(parent.poses[0].symbols)
    out = []
    for i, s in enumerate(sites):
        a, f = s["atom"], s["from_atom"]
        if not (isinstance(a, int) and isinstance(f, int) and 0 <= a < n and 0 <= f < n and a != f):
            raise ConfigError(f"[[pocket.site]] #{i}: atom/from_atom must be distinct indices in 0..{n - 1}")
        u = xyz[a] - xyz[f]
        out.append(PointCharge(float(s["q"]), tuple(float(v) for v in xyz[a] + s["distance"] * u / np.linalg.norm(u))))
    return out


def _check_parity(candidates: list[Candidate]) -> None:
    pt = Chem.GetPeriodicTable()
    for c in candidates:
        charge = net_charge(c.smiles)
        why = parity_problem(c.name, sum(pt.GetAtomicNumber(s) for s in c.poses[0].symbols) - charge, charge)
        if why is not None:
            raise StageError(f"stage 'parity' failed: {why}", stage="parity")


def _resolve(cfg: dict, name: str) -> Path:
    """A path from the config: absolute as given, otherwise relative to the config file's directory."""
    p = Path(name)
    return p if p.is_absolute() else Path(cfg.get("_dir", ".")) / p


def _dock_parent(cfg: dict, parent: Candidate, log: list, extra: dict, prepared=None):
    from .docking import Box, Docking, VinaProvider  # the docking extra: imported only when docking is configured

    d = cfg.get("docking", {})
    if prepared is not None:  # [structure]: the receptor and the centre are the stage's own artefacts
        receptor, center = prepared.receptor_pdbqt, prepared.center
    else:
        receptor, center = _resolve(cfg, d["receptor"]), tuple(float(x) for x in d["box_center"])
    if not receptor.is_file():
        raise ConfigError(f"[docking] receptor {str(receptor)!r} does not exist")
    provider_kw = {k: d[k] for k in ("exhaustiveness", "n_poses") if k in d}
    tier_kw = {k: d[k] for k in ("exhaustiveness",) if k in d}
    if "seeds" in d:
        tier_kw["seeds"] = tuple(d["seeds"])
    tier = Docking(VinaProvider(**provider_kw), **tier_kw)
    box = Box(center, tuple(float(x) for x in d.get("box_size", (24.0, 24.0, 24.0))))
    tier.run([parent], {"receptor": str(receptor), "box": box})
    if prepared is not None:  # frame check: a docked pose outside the box is not in the frame the box was stated in
        mid = np.array([p.coords_ang.mean(axis=0) for p in parent.poses])
        outside = (np.abs(mid - np.asarray(box.center)) > np.asarray(box.size) / 2).any(axis=1)
        if outside.any():
            raise StageError(
                f"stage 'docking' failed: {int(outside.sum())} of {len(parent.poses)} docked pose(s) have "
                "their centroid outside the search box; the receptor and the box are not in one frame",
                stage="docking",
            )
    scores = [float(x) for x in parent.per_pose[tier.QUANTITY]]
    extra["docking"] = {
        "receptor": receptor.name,
        "receptor_sha256": file_digest(receptor),
        "box": {"center": list(box.center), "size": list(box.size)},
    }
    unit = tier.produces()[tier.QUANTITY]
    best = f"best {min(scores):.2f} {unit}"
    _log(
        log,
        "docking",
        f"docking {parent.name}: {len(parent.poses)} pose(s), {best}",
        1,
        1,
        "search",
        scores=scores,
    )
    return tier


def _load_field(
    cfg: dict, parent: Candidate, extra: dict, log: list, prepared=None
) -> tuple[list[PointCharge], Path | None]:
    n_c = len(cfg["candidates"])
    if prepared is not None:  # already loaded around the stated centre with the explicit cutoff
        _log(
            log,
            "pocket",
            f"pocket {prepared.receptor_pdb.name}: {len(prepared.field)} point charges",
            n_c,
            n_c,
            "field",
        )
        return prepared.field, prepared.receptor_pdb
    pocket = cfg["pocket"]
    if "file" not in pocket:
        sites = _place_sites(parent, pocket["site"])
        _log(log, "pocket", f"pocket: {len(sites)} point charge(s) placed from {parent.name} pose 0", n_c, n_c, "field")
        return sites, None
    path = _resolve(cfg, pocket["file"])
    if not path.is_file():
        raise ConfigError(f"[pocket] file {str(path)!r} does not exist")
    field = load_pocket(path)
    if not len(field):
        raise StageError("stage 'pocket' failed: the pocket file produced no point charges", stage="pocket")
    extra["pocket"] = dict(field.provenance)
    _log(log, "pocket", f"pocket {path.name}: {len(field)} point charges", n_c, n_c, "field")
    return field, path


def cfg_poses_relax(cfg: dict) -> bool:
    return bool(cfg.get("poses", {}).get("relax_unmapped", False))


def _run_gates(
    plan_cfg: dict, cands: list[Candidate], field, pocket_path: Path | None, log: list, results: dict
) -> list:
    """The recorded stages between pairing and the SCFs. Returns the tiers to put in the record.

    Every gate is evaluated on every pose of every candidate and its per-pose result goes into `results`
    (`posebusters`: {candidate: PoseReport.to_dict()}, `strain_kcal`: {candidate: [kcal/mol]}) whether or not a
    gate then rejects. Rejections are collected: one StageError names every failing candidate and pose, with
    `results` attached. Once PoseBusters has rejected, the later gates do not run: their tiers refuse poses that
    failed it, so those gates are simply absent from `results`.
    """
    gates = plan_cfg.get("gates", {})
    recorded = []
    failures: list[str] = []
    if gates.get("posebusters"):
        receptor = str(pocket_path) if pocket_path is not None and pocket_path.suffix.lower() == ".pdb" else None

        def posebusters():
            report, bad = None, []
            for c in cands:
                report = check_candidate_poses(c, receptor)
                results.setdefault("posebusters", {})[c.name] = report.to_dict()
                try:
                    require_passing_poses(c, {"require_pose_report": True})
                except PoseGateError as e:
                    bad.append((c.name, str(e)))
            return report, bad

        report, bad = _stage("posebusters", posebusters)
        n = sum(len(c.poses) for c in cands)
        mode = "ligand + protein" if receptor else "ligand only"
        if bad:
            hint = ""
            if any(name != plan_cfg["parent"] for name, _ in bad) and not cfg_poses_relax(plan_cfg):
                hint = (
                    " Analogue poses copy the parent's core and embed the substituent unrelaxed, which "
                    "PoseBusters rejects at the junction; [poses] relax_unmapped = true relaxes only the "
                    "substituent (the core stays exactly the parent's)."
                )
            failures.append(f"stage 'posebusters' failed: {'; '.join(m for _, m in bad)}.{hint}")
        else:
            _log(
                log,
                "posebusters",
                f"posebusters ({mode}): all {n} pose(s) of {len(cands)} candidate(s) pass",
                len(cands),
                len(cands),
                "gate",
                posebusters_version=getattr(report, "posebusters_version", None),
            )
    if gates.get("forcefield") and not failures:  # the tier itself refuses poses that failed PoseBusters
        ff = ForceField()
        _stage("forcefield", lambda: ff.run(cands, {}))
        strain = {
            c.name: [float(a - b) for a, b in zip(c.per_pose["E_mmff"], c.per_pose["E_mmff_relaxed"], strict=True)]
            for c in cands
        }
        results["strain_kcal"] = strain
        worst = max(max(v) for v in strain.values())
        limit = gates.get("max_strain_kcal")
        over = []
        if limit is not None:
            for name, v in strain.items():
                bad_poses = [i for i, x in enumerate(v) if x > limit]
                if bad_poses:
                    over.append(
                        f"{name} pose(s) {bad_poses} have MMFF94 strain {max(v):.1f} kcal/mol "
                        f"> the configured max_strain_kcal {limit:g}"
                    )
        if over:
            failures.append(f"stage 'forcefield' failed: {'; '.join(over)}")
        else:
            what = f"gated at {limit:g} kcal/mol" if limit is not None else "recorded, no rejection threshold"
            _log(
                log,
                "forcefield",
                f"forcefield MMFF94 {what}; largest pose strain {worst:.1f} kcal/mol",
                len(cands),
                len(cands),
                "gate" if limit is not None else "recorded",
                strain=strain,
            )
        recorded.append(ff)
    if failures:
        err = StageError("\n".join(failures), stage="gates")
        err.gates = results
        raise err
    if gates.get("xtb"):
        xtb = Gfn2()
        _stage("xtb", lambda: xtb.run(cands, {"field": field}))
        _log(
            log,
            "xtb",
            "xtb GFN2 recorded (a failed xtb run is a stage error; no energy threshold; not a ranker)",
            len(cands),
            len(cands),
            "recorded",
            E_gfn2={c.name: [float(x) for x in c.per_pose["E_gfn2"]] for c in cands},
        )
        recorded.append(xtb)
    return recorded


def _prepare_structure(cfg: dict, out_dir: Path | None, fetch, log: list, extra: dict):
    from .prep import PrepConfigError, obtain_structure, prepare_target_from_structure

    if out_dir is None:
        raise ConfigError("[structure] writes its prepared artefacts under an output directory: pass out_dir")
    s = cfg["structure"]
    size = tuple(float(x) for x in cfg.get("docking", {}).get("box_size", (24.0, 24.0, 24.0)))
    base = Path(cfg.get("_dir", "."))

    def guarded(fn):
        try:
            return fn()
        except PrepConfigError as e:  # the structure section names something that does not exist: exit 2, not 3
            raise ConfigError(str(e)) from e

    result = _stage(
        "structure",
        lambda: guarded(
            lambda: obtain_structure(s["spec"], s.get("provider", "pdb"), base, s.get("allow_network", False), fetch)
        ),
    )
    prepared = _stage("structure", lambda: guarded(lambda: prepare_target_from_structure(s, result, size, out_dir)))
    extra["structure"] = prepared.record
    extra["pocket"] = dict(prepared.field.provenance)
    _log(log, "structure", prepared.summary, None, None, "prep", structure_kind=result.kind)
    return prepared


def _hook_inputs(cfg: dict, extra: dict) -> None:
    """Each hook-bearing method the config uses puts its section into the inputs (so the digest moves with its
    knobs), with every file knob replaced by the file's sha256 (so a staged copy digests the same). Builtins carry
    no hook, so configs that use only them keep their digests."""
    for spec in registry().values():
        if spec.hook is None or spec.section is None or not active(spec, cfg):
            continue
        section = json.loads(json.dumps(cfg.get(spec.section, {}), sort_keys=True, default=str))
        for k in spec.file_knobs:
            v = section.get(k)
            if isinstance(v, str) and _resolve(cfg, v).is_file():
                section[k] = {"name": Path(v).name, "sha256": file_digest(_resolve(cfg, v))}
        extra[spec.key] = section


def _hook_poses(spec: MethodSpec, cfg: dict, parent: Candidate, ctx: dict) -> None:
    """A `pose_source` hook gives the parent's poses; the analogues are then paired to them."""
    hook = cast(PoseSource, spec.hook)

    def run():
        got = list(hook.parent_poses(cfg, parent, ctx))
        if not got:
            raise StageError(f"stage {spec.key!r} failed: it gave no parent pose", stage=spec.key)
        parent.poses = got

    _stage(spec.key, run, ctx["log"])
    if not any(e["stage"] == spec.key for e in ctx["log"]):
        _log(ctx["log"], spec.key, f"{spec.label}: {len(parent.poses)} parent pose(s)", 1, 1, "search")


def _hook_field(spec: MethodSpec, cfg: dict, parent: Candidate, ctx: dict):
    """A `field_source` hook gives the point charges (a `PocketField` keeps its provenance in the record)."""
    hook = cast(FieldSource, spec.hook)
    field = _stage(spec.key, lambda: hook.field(cfg, parent, ctx), ctx["log"])
    if not len(field):
        raise StageError(f"stage {spec.key!r} failed: it gave no point charges", stage=spec.key)
    if getattr(field, "provenance", None) is not None:
        ctx["extra"].setdefault(spec.key, {})["field"] = dict(field.provenance)
    if not any(e["stage"] == spec.key for e in ctx["log"]):
        n = len(cfg["candidates"])
        _log(ctx["log"], spec.key, f"{spec.label}: {len(field)} point charges", n, n, "field")
    return field


def build_plan(cfg: dict, out_dir: Path | None = None, fetch=None) -> Plan:
    """Everything before the field-interaction SCFs: structure prep, docking, pairing, pocket, parity, gates, floor.

    `out_dir` is where `[structure]` writes its prepared receptor (required then). `fetch` replaces the
    network fetch of the structure providers (tests inject committed fixtures).
    """
    cfg, decisions_in = _apply_decisions(cfg)
    generation_in = _read_generation(cfg)
    cands = [Candidate(n, s) for n, s in cfg["candidates"].items()]
    parent = next(c for c in cands if c.name == cfg["parent"])
    log: list = []
    extra: dict = {}
    stage_seconds: dict[str, float] = {}
    _STAGE_SECONDS.set(stage_seconds)
    before = []
    _hook_inputs(cfg, extra)
    ctx = {"log": log, "extra": extra, "out_dir": out_dir, "before_poses": before}
    docked = "docking" in cfg or "structure" in cfg
    prepared = _prepare_structure(cfg, out_dir, fetch, log, extra) if "structure" in cfg else None
    if docked:
        before.append(_stage("docking", lambda: _dock_parent(cfg, parent, log, extra, prepared), log))
    sources = active_hooks("pose_source", cfg)
    if sources and docked:
        raise ConfigError(f"[{sources[0].section}] gives the parent's poses; so does docking: keep one")
    for spec in sources:  # validate_config allows at most one
        _hook_poses(spec, cfg, parent, ctx)
        docked = True  # the parent's poses are given; analogues are paired to them
    poses = PairedPoses(parent_poses_given=docked, **cfg.get("poses", {}))
    _stage("poses", lambda: poses.run(cands, {"parent": parent}))
    _log(
        log,
        "poses",
        f"paired poses: {len(parent.poses)} per candidate, {len(cands)} candidates",
        len(cands),
        len(cands),
        "pair",
    )
    fields = active_hooks("field_source", cfg)
    if fields:  # validate_config allows at most one, and not beside [pocket] or [structure]
        field, pocket_path = _hook_field(fields[0], cfg, parent, ctx), None
    else:
        field, pocket_path = _stage("pocket", lambda: _load_field(cfg, parent, extra, log, prepared), log)
    _stage("parity", lambda: _check_parity(cands))
    gate_results: dict = {}
    after = _run_gates(cfg, cands, field, pocket_path, log, gate_results)
    tier = measurement_tier(cfg)
    if hasattr(tier, "field_provenance"):  # what run() will record, so plan and run digests agree
        tier.field_provenance = getattr(field, "provenance", None)
    try:
        floor, source = tier_floor(tier, QUANTITY), f"tier {tier.name} systematic_floor"
    except ValueError as e:
        if "floor" not in cfg["cut"]:
            raise ConfigError(f"{e} Set [cut] floor explicitly to proceed, knowing it is not a measured floor.") from e
        floor, source = float(cfg["cut"]["floor"]), "config [cut] floor (explicit; the tier's own is unmeasured)"
    receptor = pocket_path if pocket_path is not None and pocket_path.suffix.lower() == ".pdb" else None
    plan = Plan(cfg, cands, parent, field, poses, tier, floor, source, before, after, extra, log)
    plan.receptor_pdb, plan.decisions_in = receptor, decisions_in
    plan.generation_in = generation_in
    plan.gate_results = gate_results
    plan.stage_seconds = stage_seconds
    plan.structure_fetch = prepared.fetch if prepared is not None else None
    return plan


def measurement_tier(cfg: dict) -> FieldInteraction | FieldDftInteraction:
    """The tier that measures dE_int: a registered `measure` hook whose section is in the config, else
    `[field_dft_interaction]` selects KS-DFT, otherwise RHF (the default)."""
    hooks = active_hooks("measure", cfg)
    if hooks:  # validate_config allows at most one measurement section
        return cast(Measure, hooks[0].hook).tier(cfg)
    if "field_dft_interaction" in cfg:
        return FieldDftInteraction(**cfg["field_dft_interaction"])
    return FieldInteraction(**cfg.get("field_interaction", {}))


def funnel_counts(plan: Plan, res=None) -> list[dict]:
    """Per stage: kind and how many candidates went in and came out. Gates cannot thin the list silently
    (a rejection is a stage error), so for them out == in; only the ranked cut removes candidates.

    The cut counts ANALOGUES: the parent is the paired reference, not a candidate for survival. With `res`
    (the CutResult) the last two rows are the measurement and the cut; `candidates_out` is None for an
    unranked cut, because no survivor set exists.
    """
    n = len(plan.candidates)
    rows = []
    if plan.generation_in is not None:  # the only stage that loses molecules before the funnel sees them
        block = plan.generation_in["block"]
        rows.append(
            {
                "stage": "generate",
                "kind": "generate",
                "candidates_in": block["n_raw"],
                "candidates_out": block["n_returned"],
                **{
                    k: block[k]
                    for k in ("generator", "rejected_by_reason", "rejected_total", "rejected_listed", "rejected")
                },
            }
        )
    for e in plan.stage_log:
        rows.append({k: e[k] for k in ("stage", "kind", "candidates_in", "candidates_out")})
        if e["stage"] == "pocket":  # parity runs right after the pocket, before the recorded gates
            rows.append({"stage": "parity", "kind": "gate", "candidates_in": n, "candidates_out": n})
    if res is not None:
        rows.append(
            {
                "stage": plan.tier.name,
                "kind": "measure",
                "candidates_in": n,
                "candidates_out": n,
                "seconds": plan.stage_seconds.get(plan.tier.name),  # wall time of the measurement `_stage` call
            }
        )
        rows.append(
            {
                "stage": "cut",
                "kind": "rank",
                "candidates_in": n - 1,
                "candidates_out": None if res.unranked_at_boundary else len(res.survivors or []),
                "unranked": bool(res.unranked_at_boundary),
            }
        )
    return rows


def per_pose_results(plan: Plan) -> tuple[dict, dict]:
    """`results.per_pose` and `results.units`: every per-pose quantity each candidate carries, parent included,
    and its unit from the tiers that ran (`produces()`). A quantity no tier declares, or a list not aligned with
    the candidate's poses, is a StageError: an unlabelled number is not recorded."""
    declared: dict[str, str] = {}
    for t in [*plan.before_poses, plan.poses, *plan.after_poses, plan.tier]:
        declared.update(t.produces())
    per_pose, units = {}, {}
    for c in plan.candidates:
        per_pose[c.name] = {}
        for q, values in c.per_pose.items():
            if q not in declared:
                raise StageError(
                    f"stage 'outputs' failed: {c.name} carries {q!r}, which no tier that ran declares", stage="outputs"
                )
            if len(values) != len(c.poses):
                raise StageError(
                    f"stage 'outputs' failed: {c.name} has {len(values)} {q!r} value(s) for {len(c.poses)} pose(s)",
                    stage="outputs",
                )
            per_pose[c.name][q] = [float(x) for x in values]
            units[q] = declared[q]
    return per_pose, units


def _paired_parent(plan: Plan, c: Candidate, pose_index: dict[str, list[int]] | None) -> Candidate:
    """The parent as analogue `c` pairs with it: itself, or (after a pairwise pose drop) a view holding only the
    parent's values at `c`'s kept pose indices, so pose i still pairs with pose i."""
    if not pose_index:
        return plan.parent
    at = {orig: k for k, orig in enumerate(pose_index[plan.parent.name])}
    view = Candidate(plan.parent.name, plan.parent.smiles)
    view.per_pose = {q: [v[at[i]] for i in pose_index[c.name]] for q, v in plan.parent.per_pose.items()}
    return view


def run_plan(
    plan: Plan,
    measure=None,
    partial: bool = False,
    skip_analyses: frozenset[str] = frozenset(),
    pose_index: dict[str, list[int]] | None = None,
    checkpoint=None,
    stages=None,
) -> tuple[RunRecord, dict]:
    """Run the measurement, pair, cut, and build the record (written by `write_outputs`).

    `measure(plan)`, when given, fills `per_pose[QUANTITY]` on every candidate in place of running
    `plan.tier` (the field-interaction SCFs); tests inject fixed numbers, so everything after the SCFs is checked
    without ferric.

    `skip_analyses`: analysis keys the caller computes itself (the Temporal runner summarizes the ones that
    re-measure). `pose_index`: after a pairwise pose drop, each candidate's kept ORIGINAL pose indices (its poses
    and per_pose hold only those); an analogue then pairs with the parent's values at its own kept indices, and
    the record carries `results.pose_index`.

    `checkpoint` (`smeltery.checkpoint.ScfCheckpoint`): the measurement tier reuses the SCFs it holds and appends the
    ones it runs (a plain run into OUT; analyses never see it).

    `stages` (`smeltery.reuse.RunStages`): each stage is digested and logged as it completes; the measurement and
    the analyses are taken from the stage store when an identical earlier run stored them, and stored otherwise.
    """
    _STAGE_SECONDS.set(plan.stage_seconds)  # this plan's seconds, whatever was built in this context since
    if stages is not None and stages.plan_digest is None:  # the Temporal runner logs its plan stages when it builds it
        stages.run_id = plan.input_digest[:12]
        stages.plan_built(plan, funnel_counts(plan))
    if measure is None:
        ctx = {"field": plan.field} if checkpoint is None else {"field": plan.field, "checkpoint": checkpoint}
        if stages is None:
            _stage(plan.tier.name, lambda: plan.tier.run(plan.candidates, ctx))
        else:  # a reused measurement is never timed: its funnel row says seconds null, not "took 0 s"
            stages.measure(plan, lambda: _stage(plan.tier.name, lambda: plan.tier.run(plan.candidates, ctx)))
    elif stages is None:
        _stage(plan.tier.name, lambda: measure(plan))
    else:  # the Temporal runner's measurement, from its log: logged as a completed stage like any other
        stages.measure(plan, lambda: _stage(plan.tier.name, lambda: measure(plan)))
    analogues = [c for c in plan.candidates if c is not plan.parent]
    if partial:
        paired = {}
        unpaired = {}
        res = None
        results: dict[str, object] = {
            "stages": plan.stage_log,
        }
    else:
        paired = {c.name: paired_delta(_paired_parent(plan, c, pose_index), c, QUANTITY) for c in analogues}
        unpaired = {c.name: unpaired_delta(_paired_parent(plan, c, pose_index), c, QUANTITY) for c in analogues}
        res = cut(paired, keep=plan.cfg["cut"]["keep"], z=plan.cfg["cut"].get("z", 2.0), floor=plan.floor)
        results: dict[str, object] = {
            "paired_ddE": {
                k: {"mean": v.mean, "sem": v.sem, "n": v.n, "per_pose": v.per_pose} for k, v in paired.items()
            },
            "unpaired_sem": {k: v.sem for k, v in unpaired.items()},
            "cut": {"groups": res.groups, "survivors": res.survivors, "unranked": res.unranked_at_boundary},
            "stages": plan.stage_log,
            "funnel": funnel_counts(plan, res),
        }
        if stages is not None:
            stages.cut(plan, results)
    results["candidate_order"] = [c.name for c in plan.candidates]
    if pose_index:  # only after a pairwise drop, so ordinary records keep their shape
        results["pose_index"] = {k: list(v) for k, v in pose_index.items()}
    # (analogue atom, parent atom) pairs copied exactly from the parent's pose: the core a view draws solid
    results["pose_maps"] = {k: [list(p) for p in v] for k, v in plan.poses.scaffold_maps.items()}
    results["per_pose"], results["units"] = per_pose_results(plan)
    results["field"] = [[float(c.q), *(float(v) for v in c.xyz_ang)] for c in plan.field]
    results["receptor"] = (
        None
        if plan.receptor_pdb is None
        else {"file": str(Path(plan.receptor_pdb).resolve()), "sha256": file_digest(plan.receptor_pdb)}
    )
    if plan.decisions_in is not None:
        results["decisions_in"] = dict(plan.decisions_in)
    if plan.generation_in is not None:
        results["generation_in"] = {k: plan.generation_in[k] for k in ("file", "sha256")}
    if plan.structure_fetch is not None:  # a wall-clock time: in results, never in inputs (the digest)
        results["structure_fetch"] = dict(plan.structure_fetch)
    rec = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, results)
    if not partial:  # an analysis reads the whole measurement; a combine of the partials runs it
        for spec in active_hooks("analysis", plan.cfg):
            if spec.key in skip_analyses:
                continue
            _run_analysis(spec, plan, rec, {"measure": measure, "log": plan.stage_log}, stages)
    return rec, {"paired": paired, "unpaired": unpaired, "cut": res}


def _run_analysis(spec: MethodSpec, plan: Plan, rec: RunRecord, ctx: dict, stages=None) -> None:
    """An `analysis` hook runs after the measurement; the dict it returns is merged into `results[spec.key]`.
    `stages` (`smeltery.reuse.RunStages`): the stored dict of an identical earlier run, when there is one."""
    hook = cast(Analysis, spec.hook)

    def run():
        return _stage(spec.key, lambda: hook.run(plan, rec, ctx))

    got = run() if stages is None else stages.analysis(spec, plan, rec, run)
    if not isinstance(got, dict):
        raise StageError(
            f"stage {spec.key!r} failed: an analysis returns a dict, got {type(got).__name__}", stage=spec.key
        )
    rec.results[spec.key] = {**(rec.results.get(spec.key) or {}), **got}


def memory_estimate(plan) -> dict:
    """The memory facts `--plan` and Bench's impact both state, structured. `plan` needs only `.tier` and
    `.candidates`. `estimate` is the measurement tier's own `estimate_memory` dict (the one its pre-SCF check
    uses, so the plan and the run cannot disagree) or None when the tier has no model; `verdict` is "fits",
    "does not fit", "cannot say" (the model cannot count this basis or element: never "fits") or "no model";
    `basis` says why when the verdict is not a number-backed one, else None."""
    tier = plan.tier
    fn = getattr(tier, "estimate_memory", None)
    mem = fn(plan.candidates) if fn is not None else None
    basis = None
    if mem is None:
        verdict, basis = "no model", f"{tier.name} has no memory model"
    elif "total_gb" not in mem:
        verdict, basis = "cannot say", mem.get("basis")
    else:
        verdict = "fits" if mem["fits_budget"] else "does not fit"
    return _memory_facts(tier, tier.name, mem, verdict, basis)


def memory_cannot_say(tier, basis: str) -> dict:
    """`memory_estimate`'s shape for when there are no poses to size (or no tier): the same keys, `estimate` None."""
    return _memory_facts(tier, getattr(tier, "name", None), None, "cannot say", basis)


def _memory_facts(tier, name: str | None, mem: dict | None, verdict: str, basis: str | None) -> dict:
    """The one shape of `memory_estimate` and `memory_cannot_say`, in its key order."""
    return {
        "tier": name,
        "estimate": mem,
        "free_gb": available_memory_gb(),
        "require_free_gb": getattr(tier, "require_free_gb", None),
        "check_memory": bool(getattr(tier, "check_memory", True)),
        "verdict": verdict,
        "basis": basis,
    }


def partial_digest(plan: Plan, parent: str, name: str) -> str:
    """The input digest `--only-candidate name` records for this config: the plan's inputs with only the parent and
    `name` as candidates (a partial plan is built from exactly those), and the same tiers."""
    inputs = plan.inputs
    keep = {k: v for k, v in inputs["candidates"].items() if k in (parent, name)}
    return input_digest({**inputs, "candidates": keep}, plan.tiers)
