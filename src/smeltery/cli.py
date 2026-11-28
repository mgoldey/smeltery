"""`smeltery run <config.toml>`: poses -> field interaction -> paired ddE -> cut -> run record.

One honest path, not a framework. Stages, in order, each a loud failure when it fails
(exit 3, naming the stage); nothing is skipped silently:

1. `PairedPoses`: the parent's ETKDG ensemble plus analogue poses paired to it. The
   MCS gate refuses a pair with no common core.
2. pocket: point charges placed from the parent's pose 0 (see `[[pocket.site]]`).
3. parity: every candidate must have an even electron count at its formal charge,
   because both measurement tiers are closed-shell (RHF, or restricted KS-DFT). An odd
   count is a stage error, never a silent zero-survivor run.
4. the measurement: dE_int = E(field) - E(vacuum) per pose. `FieldInteraction` (ferric
   RHF) by default; `[field_dft_interaction]` selects `FieldDftInteraction` (ferric PBE,
   D3(BJ) recorded separately) instead.
5. paired ddE against the parent, then `cut`.

The cut's floor is the tier's own systematic floor. While that is unmeasured
(both tiers today) the config must state `[cut] floor` explicitly, and the record
says the floor came from the config. Candidates the cut cannot separate are printed as
tie groups; a run where nothing can be ordered prints UNRANKED and still exits 0 (4 with `--fail-on-unranked`).

Config (TOML; unknown keys are an error, not ignored):

    campaign = "demo"
    parent = "benzoic"
    [candidates]            # name = SMILES; the parent must be listed
    benzoic = "OC(=O)c1ccccc1"
    4-F = "OC(=O)c1ccc(F)cc1"
    [poses]                 # optional: PairedPoses knobs
    n_poses = 6
    [[pocket.site]]         # a point charge `distance` Å from parent atom `atom`, along from_atom -> atom
    q = 1.0                 # atom indices are the parent SMILES with explicit H (`--plan` lists them)
    atom = 1
    from_atom = 2
    distance = 3.0
    [field_interaction]     # optional: FieldInteraction (RHF) knobs
    basis = "sto-3g"
    [cut]
    keep = 1
    z = 2.0
    floor = 0.0             # required while the tier's systematic floor is unmeasured

Optional sections (absent = the stages above only):

    [docking]               # dock the PARENT with Vina; analogues are PAIRED to its docked poses, not docked
    receptor = "pocket.pdbqt"   # relative paths resolve against the config file's directory
    box_center = [0.4, -0.1, -0.1]
    box_size = [22.0, 16.0, 16.0]
    seeds = [7]
    exhaustiveness = 4
    n_poses = 3             # with [docking], n_poses/jitter_* in [poses] are an error: they would do nothing
    [pocket]                # instead of [[pocket.site]]: a .pdb (via pdb2pqr30) or .pqr; same frame as the receptor
    file = "pocket.pdb"
    [poses]
    relax_unmapped = true   # relax only the substituent (MMFF94, core fixed): needed to pass PoseBusters
    [gates]                 # run before the SCFs; a failing pose is a stage error, never silently dropped
    posebusters = true      # needs the posebusters extra; also checks against the pocket when it is a .pdb
    forcefield = true       # MMFF94 energy and strain per pose, recorded
    max_strain_kcal = 25.0  # optional, needs forcefield: reject (stage error) any pose with more strain; no default
    xtb = true              # GFN2 energy per pose in the pocket field, recorded (a gate, not a ranker)

Choosing the measurement (one of the two; absent = RHF with default knobs):

    [field_dft_interaction] # KS-DFT instead of RHF: FieldDftInteraction knobs
    functional = "PBE"      # a libxc name ferric accepts; D3(BJ) is computed separately and cancels in dE_int
    basis = "sto-3g"        # the memory check counts basis functions for STO-3G only (check_memory = false otherwise)
    memory_budget_gb = 8.0  # refuse before the first SCF if the largest pose is predicted not to fit
    require_free_gb = 12.0  # optional: refuse unless this much memory is actually free

Starting from a target (replaces [docking] receptor/box_center and [pocket]; see `smeltery.prep`):

    [structure]
    spec = "pocket_pep5.pdb"    # a .pdb path (relative to the config), a PDB id, or (provider afdb) a UniProt id
    provider = "pdb"            # optional: "pdb" (default) or "afdb" (PREDICTED: licence + attribution recorded)
    allow_network = false       # an id or accession is fetched only when this is true; a local file never is
    center = [0.4, -0.1, -0.1]  # EXACTLY ONE of: explicit box centre (the structure's frame, Angstrom) ...
    reference_ligand = "LIG"    # ... or the heavy-atom centroid of HETATM residue RES[:CHAIN[:RESNUM]]
    pocket_cutoff = 12.0        # required, Angstrom: the point-charge field is cut at this radius from the centre.
                                # No default: truncation is not monotone, so the choice is recorded, not assumed.
    chains = ["A"]              # optional: protein chains to keep (default all)
    [docking]                   # optional here: seeds, exhaustiveness, n_poses, box_size (default 24 A)

The structure stage writes `receptor.pdb` (first model, altloc blank/A, every HETATM dropped and counted) and
`receptor.pdbqt` (Meeko) under `<out>/structure/`, identifies both by sha256 in the record, and checks the frame
(centre near the receptor; field covers the box faces; PDBQT atoms coincide with the PDB; no receptor atoms lost to
Meeko inside the box; docked parent inside the box). A failed check is a stage error.

Carrying earlier decisions into this run (`smeltery.decisions`; docs/gui-contract.md section 3):

    [decisions]
    file = "decisions.json"     # relative to the config's directory; read and validated before anything is posed

Candidates with status `reject` are dropped before posing and listed in `results.decisions_in.excluded`; `keep` and
`hold` run as usual. Each of these is a config error (exit 2): an invalid file, a decision naming a candidate the
config does not list, a decision whose SMILES is not the config's molecule (compared canonically), rejecting the
parent, and `[cut] keep` larger than the analogues left. The file's sha256 is an input (it changes the digest);
configs without the section keep their digests.

Outputs under `--out` (docs/gui-contract.md section 1): the run record `run-<digest12>.json`, whose `results` also
carry `per_pose` (every per-pose quantity of every candidate, parent included) with `units`, `poses` (one SDF per
candidate under `poses/`, with sha256), `field` ([q, x, y, z], Angstrom) and `receptor` (a .pdb in the poses'
frame, or null). SDF atoms are the candidate's SMILES with explicit H, in that order; a pose whose elements differ
from it, or two candidates whose sanitised file names collide, is a stage error.

Funnel semantics (smallest sound form): every stage reports candidates in and out (`results.funnel`, and the table).
Gate stages can only REJECT, loudly: parity, PoseBusters and MMFF strain when `[gates] max_strain_kcal` is set (an
explicit kcal/mol threshold; absent = strain is recorded, nothing is rejected, and the stage says so) -- so a gate's
out count is its in count, or the run stops. xtb is recorded, never a filter. Only the one ranked stage, the paired
ddE `cut`, removes candidates, with the tier floor (explicit `[cut] floor` while unmeasured), tie groups and
UNRANKED exactly as above; it counts analogues, because the parent is the paired reference. A second ranked stage
is deliberately not offered: the only wired ranker has an unmeasured floor, so a ladder would need a second invented
floor, and totals are never compared across formulas.

Durable runs (`smeltery.temporal_run`): `smeltery run CONFIG --out OUT --temporal [--workers N]` runs the same plan as
a Temporal workflow, one activity per stage and per SCF leg (candidate, pose, field model), and `smeltery
temporal-worker` executes it. The
same OUT and staged config resume the same workflow; finished SCFs are read back from OUT/calcs.jsonl. With the server
down it exits 2 naming the docker command; it never falls back to an in-process run.

    [run]
    scf_timeout_s = 14400       # bounds one SCF leg (one rung) under --temporal (not an input: no digest change)
    [field_interaction]
    remedies = [{level_shift = 0.5}, {max_iter = 300, soscf = true}]  # tried in order when an SCF does not converge

Resuming a plain run (`smeltery.checkpoint`): with `--out OUT`, every SCF is appended to OUT/calcs.partial.jsonl as it
finishes (fsync'd), and OUT/progress.json names that file and the planned SCF count. Rerunning the same command after
a crash, kill or cancel reuses each finished SCF whose input digest and staged config are unchanged ("resuming: k of N
SCFs reused") and computes only the rest; the record is the one an uninterrupted run writes. OUT is refused only once
it holds a run record (then `--force` reruns it from scratch); a checkpoint of other inputs is refused (exit 2) unless
`--force` discards it. Both files are removed when the record is written.

Exit codes: 0 done (including UNRANKED), 2 bad config, 3 a stage failed, 4 UNRANKED under `--fail-on-unranked`
(in-process runs; not `--temporal`).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path

from . import config as _config
from .calc_log import primary_model
from .config import (  # noqa: F401 -- the config rules live in `smeltery.config`; re-exported, the same objects
    DECISIONS_KEYS,
    OWN_TOP_KEYS,
    RUN_KEYS,
    SCF_TIMEOUT_S,
    SITE_KEYS,
    ConfigError,
    OutGuardRefused,
    StageError,
    _check_keys,
    _exclusive_with,
    _knobs,
    _validate_cut,
    _validate_decisions,
    _validate_docking,
    _validate_gates,
    _validate_measurement,
    _validate_pocket,
    _validate_run,
    _validate_section,
    _validate_structure,
    allow_mixed_builds,
    gate_keys,
    gate_option_keys,
    load_config,
    scf_timeout_s,
    section_keys,
    top_keys,
    validate_config,
)
from .methods import active_hooks
from .planning import (  # noqa: F401 -- moved to `smeltery.planning`; re-exported, the same objects
    _STAGE_SECONDS,
    Plan,
    _apply_decisions,
    _canonical,
    _check_parity,
    _dock_parent,
    _hook_field,
    _hook_inputs,
    _hook_poses,
    _load_field,
    _log,
    _memory_facts,
    _paired_parent,
    _place_sites,
    _prepare_structure,
    _resolve,
    _run_analysis,
    _run_gates,
    _stage,
    _stage_seconds,
    build_plan,
    cfg_poses_relax,
    funnel_counts,
    measurement_tier,
    memory_cannot_say,
    memory_estimate,
    partial_digest,
    per_pose_results,
    run_plan,
)
from .record import RunRecord, dump_record, stamp_record, write_text_atomic  # noqa: F401 -- RunRecord: re-exported
from .run_inputs import check_out_guard as _guard_out  # the staging of `--out`, `smeltery.run_inputs`
from .run_inputs import pose_sections
from .run_inputs import stage_config as _stage_config
from .run_outputs import (  # noqa: F401 -- moved to `smeltery.run_outputs`; re-exported, the same objects
    POSES_DIR,
    _analysis_calcs,
    _count_models,
    _gates_in_out,
    _out_path,
    calc_file_name,
    merged_partial_calcs,
    partial_candidate,
    partial_file_name,
    pose_file_name,
    pose_mol,
    write_calc_log,
    write_outputs,
    write_plan_preview,
    write_pose_sdfs,
)
from .run_view import QUANTITY  # noqa: F401 -- re-exported: the measured quantity


def __getattr__(name: str):
    """The key sets derived from the method registry on every read (`smeltery.config`), and `POSE_SECTIONS`."""
    if name == "POSE_SECTIONS":
        return pose_sections()
    if name in _config._DERIVED:
        return _config._DERIVED[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def parse_ext(text: str | None) -> dict | None:
    """`--ext-json`: the submitter's own `ext` namespaces for the core record, as JSON. A namespace is a non-empty
    name mapping to an object (`ext.<namespace>`, the schema's shape); `smeltery` itself is the run's own and is
    refused. None without the flag."""
    if text is None:
        return None
    try:
        obj = json.loads(text)
    except ValueError as e:
        raise ConfigError(f"--ext-json is not valid JSON: {e}") from e
    if not isinstance(obj, dict) or not obj:
        raise ConfigError("--ext-json must be a non-empty object of ext namespaces")
    for key, value in obj.items():
        if not isinstance(key, str) or not key:
            raise ConfigError("--ext-json namespaces must be non-empty names")
        if key == "smeltery":
            raise ConfigError("--ext-json cannot set the `smeltery` namespace: it is the run's own")
        if not isinstance(value, dict):
            raise ConfigError(f"--ext-json namespace {key!r} must map to an object")
    return obj


def format_table(plan: Plan, out: dict) -> str:
    res = out["cut"]
    lines = [f"{'candidate':12} {'formula':10} {'paired ddE (kcal/mol)':>26} {'unpaired SEM':>13}"]
    for c in plan.candidates:
        if c is plan.parent:
            continue
        m = out["paired"][c.name]
        lines.append(f"{c.name:12} {c.formula:10} {m.mean:+12.4f} ± {m.sem:.4f} {out['unpaired'][c.name].sem:13.4f}")
    lines.append("\nstages: " + " -> ".join(e["summary"] for e in plan.stage_log) + f" -> {plan.tier.name}")
    lines.append("candidates in -> out per stage (a gate rejects loudly or passes everyone; only the cut ranks):")
    rows = funnel_counts(plan, res)
    w = max(18, *(len(r["stage"]) for r in rows))  # field_dft_interaction is wider than the old fixed 18
    for r in rows:
        cin = "-" if r["candidates_in"] is None else r["candidates_in"]
        cout = "unranked" if r.get("unranked") else ("-" if r["candidates_out"] is None else r["candidates_out"])
        lines.append(f"  {r['stage']:{w}} {r['kind']:9} {cin!s:>3} -> {cout!s}")
    st = plan.extra_inputs.get("structure")
    if st:
        lines.append(f"structure: {st['kind']}, {st['structure']['source']}")
        if st["kind"] == "PREDICTED":
            lines.append(f"  licence {st['structure']['license_id']}; {st['structure']['attribution']}")
    if plan.decisions_in is not None:
        excl = ", ".join(plan.decisions_in["excluded"]) or "none"
        lines.append(f"decisions {Path(plan.decisions_in['file']).name}: rejected before the run: {excl}")
    lines.append(f"floor {plan.floor:g} kcal/mol, from {plan.floor_source}; z={res.z:g}")
    lines.append("tie groups (no order within a group): " + " | ".join(", ".join(g) for g in res.groups))
    pairing = out.get("pairing")
    if pairing:  # failed SCFs dropped poses: n per analogue, and every drop with its reason (never silent)
        req = max(pairing["requested_poses"].values())
        lines.append(
            f"paired poses per analogue (of {req} requested; at least {pairing['min_pairs']} to rank): "
            + ", ".join(f"{k} {v}" for k, v in pairing["n_pairs"].items())
        )
        for d in out.get("dropped") or []:
            lines.append(f"dropped: {d['candidate']} pose {d['pose']} ({d['leg']}): {d['reason']}")
    if res.unranked_at_boundary:
        lines.append("UNRANKED: " + "; ".join(res.notes))
    else:
        lines.append(f"survivors (keep={plan.cfg['cut']['keep']}): {', '.join(res.survivors or [])}")
    return "\n".join(lines)


def _describe_plan(plan: Plan, digest: str) -> str:
    sym = plan.parent.poses[0].symbols
    atoms = ", ".join(f"{i}:{s}" for i, s in enumerate(sym))
    return (
        f"plan OK (no SCF run)\nparent {plan.parent.name} atoms (index:element): {atoms}\n"
        f"{len(plan.field)} pocket charge(s); {len(plan.parent.poses)} poses per candidate; "
        f"stages: {' -> '.join(e['stage'] for e in plan.stage_log)}; "
        f"floor {plan.floor:g} from {plan.floor_source}\n"
        + "\n".join(describe_memory(plan))
        + "\n"
        + describe_scf_work(plan)
        + f"\ninput_digest {digest}"
    )


def describe_scf_work(plan: Plan) -> str:
    """How many scores (poses x candidates x field models) the run will do and how many SCFs that is: under
    `--temporal` one vacuum leg per pose shared by the models plus one field leg per model, in a plain run two legs
    per score; and the tier's own time estimate (`estimate_cost`, for two legs per pose) scaled to each, in CPU-h."""
    n_models = 1
    for spec in active_hooks("analysis", plan.cfg):
        hook = spec.hook
        if hasattr(hook, "extra_models"):  # cheap: the model names from the config
            n_models += len(hook.extra_models(plan.cfg))
        elif hasattr(hook, "extra_fields"):
            n_models += len(hook.extra_fields(plan, {"log": plan.stage_log, "out_dir": None}))
    n_poses = sum(len(c.poses) for c in plan.candidates)
    per_cand = sorted({len(c.poses) for c in plan.candidates})
    poses = str(per_cand[0]) if len(per_cand) == 1 else f"{per_cand[0]}-{per_cand[-1]}"
    n = n_poses * n_models
    temporal, plain = n_poses * (1 + n_models), 2 * n
    head = (
        f"SCF work: {n} scores = {poses} poses x {len(plan.candidates)} candidates x {n_models} field model(s); "
        f"{temporal} SCFs under --temporal (one vacuum leg per pose, shared), {plain} in a plain run"
    )
    est = plan.tier.estimate_cost(plan.candidates)
    if est.get("predicted") is None:
        return f"{head}; time estimate: none ({est.get('basis')})"
    legs_h = float(est["predicted"]) / 2 / 3600.0  # one leg of every pose: estimate_cost covers two
    return (
        f"{head}; estimate {legs_h * (1 + n_models):.2f} CPU-h under --temporal, "
        f"{legs_h * 2 * n_models:.2f} CPU-h plain (summed SCF wall time; {est.get('basis')})"
    )


def describe_memory(plan: Plan) -> list[str]:
    """What `--plan` says about memory: `memory_estimate`, as text."""
    m = memory_estimate(plan)
    name, mem, enforced = m["tier"], m["estimate"], m["check_memory"]
    if mem is None:
        return [f"memory: {name} has no memory model; nothing is checked before its SCFs"]
    if m["verdict"] == "cannot say":
        lines = [f"memory ({name}, per calculation): cannot say ({mem['basis']}); the run will not refuse on it"]
    else:
        verdict = (
            "fits"
            if mem["fits_budget"]
            else "does NOT fit: the run will refuse before its first SCF"
            if enforced
            else "does NOT fit (check_memory = false, so the run will start anyway and may spill to disk)"
        )
        lines = [
            f"memory ({name}, per calculation): the largest pose ({mem['n_atoms']} atoms, "
            f"{mem['n_basis_functions']} basis functions) needs {mem['total_gb']:.2f} GB "
            f"(AO cache {mem['ao_cache_gb']:.2f} + RI tensor {mem['ri_tensor_gb']:.2f}); "
            f"budget {mem['budget_gb']:g} GB: {verdict}"
        ]
    free, need = m["free_gb"], m["require_free_gb"]
    have = "unknown (no /proc/meminfo)" if free is None else f"{free:.1f} GB available now"
    if need is None:
        lines.append(f"machine: {have}; require_free_gb unset, so free memory is not checked")
    elif free is not None and free < need and enforced:
        lines.append(f"machine: {have}, below require_free_gb {need:g}: the run would refuse if started now")
    else:
        lines.append(f"machine: {have}; require_free_gb {need:g}")
    lines.append(
        "  (a per-calculation figure: a long run has been measured holding ~11 GB against a ~2 GB prediction, "
        "so leave the machine headroom beyond it)"
    )
    return lines


def write_plan_error(out_dir: Path, message: str, stage: str | None = None) -> None:
    """`--plan --out OUT` that failed: OUT/plan.json says so (`status` "error", the failing `stage` or null, the
    `message`) instead of leaving the last good preview or nothing. `input_digest` is always null: a failure leaves
    no finished plan to digest (the key is there so readers of an ok plan.json find the same keys)."""
    rec = {"status": "error", "stage": stage, "message": message, "input_digest": None}
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        write_text_atomic(out_dir / "plan.json", json.dumps(rec, indent=2, sort_keys=True) + "\n")
    except OSError as oe:  # never mask the exit code of the failure itself
        print(f"warning: could not write plan.json: {oe}", file=sys.stderr)


#: Subcommands owned by `smeltery.gui`; their own parsers read the rest of the command line.
VIEWERS = {
    "gui": "smeltery.gui.app",
    "report": "smeltery.gui.report",
    "dashboard": "smeltery.dashboard",
    "central": "smeltery.dashboard",
    "avogadro": "smeltery.avogadro",
    "golden": "smeltery.golden",
    "floor": "smeltery.floor_measure",
    "bench": "smeltery.bench.app",
    "target": "smeltery.target_file",
    "verify-run": "smeltery.run_tools:verify_run",
    "inspect": "smeltery.run_tools:inspect_run",
    "diff-run": "smeltery.run_tools:diff_run",
    "export-sdf": "smeltery.sdf_export",
    "export": "smeltery.dataset_export",
    "backup": "smeltery.backup:backup_main",
    "restore": "smeltery.backup:restore_main",
    "record": "smeltery.core_record",
    "al": "smeltery.al:al_main",
    "mcp": "smeltery.mcp:main",
    "serve": "smeltery.services:serve_main",
    "worker": "smeltery.services:worker_main",
    "submit": "smeltery.jobs:submit_main",
    "status": "smeltery.jobs:status_main",
    "cancel": "smeltery.jobs:cancel_main",
    "result": "smeltery.jobs:result_main",
}


def _open_checkpoint(plan: Plan, out_dir: Path, config_sha256: str, force: bool):
    """The plain run's SCF checkpoint in OUT (`smeltery.checkpoint`): `--force` discards an old one first; one of
    other inputs or another staged config is a ConfigError. Writes OUT/progress.json and says what it reuses."""
    from .checkpoint import CheckpointRefused, ScfCheckpoint, discard, planned_scfs

    if force:
        discard(out_dir)
    digest = plan.input_digest
    try:
        cp = ScfCheckpoint(out_dir, digest, config_sha256, primary_model(plan.field))
    except CheckpointRefused as e:
        raise ConfigError(str(e)) from e
    total = planned_scfs(plan.candidates)
    cp.write_progress(total)
    if cp.n_reusable:
        print(f"resuming: {cp.n_reusable} of {total} SCFs reused from {Path(out_dir) / 'calcs.partial.jsonl'}")
    return cp


def _check_partials(plan: Plan, parent: str, recs: dict[Path, dict]) -> None:
    """A combine joins partials of THIS config only: each partial's input digest is the one this plan gives its
    candidate, and every partial staged the same config (results.config sha256). Else ConfigError."""
    for p, r in recs.items():
        name = partial_candidate(p)
        want = partial_digest(plan, parent, name)
        if r.get("input_digest") != want:
            raise ConfigError(
                f"{p.name} was measured with other inputs (input_digest {str(r.get('input_digest'))[:12]}, this "
                f"config gives {want[:12]}): a combine joins partials of one config; re-run that partial"
            )
    shas = {p.name: ((r.get("results") or {}).get("config") or {}).get("sha256") for p, r in recs.items()}
    if len(set(shas.values())) > 1:
        each = ", ".join(f"{k}: {str(v)[:12] if v else 'none'}" for k, v in sorted(shas.items()))
        raise ConfigError(
            f"the partials staged different configs ({each}): a combine record states one config; re-run the "
            "partials from one staged config"
        )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in VIEWERS:  # imported only when asked for: they need no tier, and gui needs the gui extra
        import importlib

        module, _, func = VIEWERS[argv[0]].partition(":")  # "module" means module.main; "module:func" names the entry
        return getattr(importlib.import_module(module), func or "main")(argv[1:])
    ap = argparse.ArgumentParser(prog="smeltery", description="Tiered, measurement-disciplined funnel.")
    sub = ap.add_subparsers(dest="command", required=True)
    sub.add_parser("gui", help="browse a finished run, record decisions (smeltery gui OUT --help)")
    sub.add_parser("report", help="write a self-contained HTML report of a run (smeltery report OUT --help)")
    sub.add_parser("dashboard", help="centralized dashboard for runs and tasks (smeltery dashboard ROOT --help)")
    sub.add_parser("central", help="alias for dashboard")
    sub.add_parser("avogadro", help="export a run and open it in Avogadro 2 (smeltery avogadro OUT --help)")
    sub.add_parser("golden", help="golden-path helpers: init | open | bundle")
    sub.add_parser("floor", help="FieldInteraction floor evidence (smeltery floor measure RESULTS...; docs/limits.md)")
    sub.add_parser("bench", help="Bench web app (smeltery bench ROOT --help)")
    sub.add_parser("serve", help="Bench, configured by SMELTERY_* env vars (smeltery serve --help)")
    sub.add_parser("mcp", help="serve the screening tools to an MCP client over stdio (docs/mcp.md; `mcp` extra)")
    sub.add_parser("worker", help="the worker for SMELTERY_BACKEND=local|temporal (smeltery worker --help)")
    sub.add_parser("submit", help="submit `run CONFIG --out RUN_DIR` as a campaign job, return at once (docs/jobs.md)")
    sub.add_parser("status", help="a campaign job's state (smeltery status RUN_DIR)")
    sub.add_parser("cancel", help="cancel a campaign job (smeltery cancel RUN_DIR); idempotent")
    sub.add_parser("result", help="a finished campaign job's core record (smeltery result RUN_DIR)")
    sub.add_parser("target", help="check a target definition .toml (smeltery target check FILE; docs/targets.md)")
    sub.add_parser("verify-run", help="check a run record's self-hash and file hashes (accidental edits only)")
    sub.add_parser("inspect", help="plain-text summary of a run record")
    sub.add_parser("export-sdf", help="poses + ddE, tie group, gates, decisions as one SDF (smeltery export-sdf OUT)")
    sub.add_parser(
        "export", help="runs as one Parquet or Arrow file (smeltery export parquet RUN... -o F; dataset extra)"
    )
    sub.add_parser("diff-run", help="compare two runs' inputs and tiers")
    sub.add_parser("backup", help="archive a Bench workspace with a hash manifest (smeltery backup ROOT --out F)")
    sub.add_parser("restore", help="restore a backup into an empty directory (smeltery restore F --into DIR)")
    sub.add_parser("record", help="write a run's core record (smeltery record core RUN.json; attestation extra)")
    sub.add_parser("al", help="active-learning loop: propose, score, record each iteration (docs/active-learning.md)")
    tw = sub.add_parser("task-worker", help="run a Temporal worker for smeltery tasks")
    # Alias of `smeltery worker --backend temporal` for the tasks queue alone. Defaults are None so that
    # settings.resolve gives flag > SMELTERY_* env var > default (docs/temporal-runs.md).
    tw.add_argument("--host", default=None, help="Temporal server (env SMELTERY_TEMPORAL_ADDRESS)")
    tw.add_argument("--namespace", default=None, help="env SMELTERY_TEMPORAL_NAMESPACE")
    tw.add_argument("--task-queue", default=None, help="env SMELTERY_TASK_QUEUE")
    tw.add_argument(
        "--concurrency", type=int, default=None, help="max concurrent activities (SMELTERY_TASK_CONCURRENCY)"
    )
    run = sub.add_parser("run", help="run a campaign config")
    run.add_argument("config", type=Path)
    run.add_argument(
        "--out",
        type=Path,
        default=None,
        help="directory for the run record (default out); when given it also receives config.toml and inputs/",
    )
    run.add_argument("--plan", action="store_true", help="stop before the SCFs: poses, pocket, parity, digest")
    run.add_argument(
        "--only-candidate",
        dest="only_candidate",
        default=None,
        help="measure one candidate only, writing a partial result",
    )  # noqa: E501
    run.add_argument(
        "--fail-on-unranked", dest="fail_on_unranked", action="store_true", help="exit 4 when the cut is UNRANKED"
    )
    run.add_argument(
        "--force",
        action="store_true",
        help="run into an OUT that already holds a run record; also discards an interrupted run's SCF checkpoint "
        "(OUT/calcs.partial.jsonl), which a rerun without --force resumes from",
    )
    run.add_argument(
        "--stage-cache",
        dest="stage_cache",
        type=Path,
        default=None,
        help="a stage store (smeltery.reuse): reuse the stages an identical earlier run completed, store this run's; "
        "with --out only (env SMELTERY_STAGE_CACHE)",
    )
    run.add_argument(
        "--run-id",
        dest="run_id",
        default=None,
        help="the core record's run_id (default: the input digest's first 12 hex); a campaign job passes its own",
    )
    run.add_argument(
        "--arm",
        default=None,
        help="the core record's arm (default none: smeltery has no arm of its own); a campaign job passes the arm its "
        "submitter named, e.g. an active-learning iteration (docs/active-learning.md)",
    )
    run.add_argument(
        "--ext-json",
        dest="ext_json",
        default=None,
        help="the submitter's own ext namespaces for the core record, as JSON (ext.<namespace>: an object); the "
        "`smeltery` namespace is the run's own and is refused",
    )
    run.add_argument("--combine", action="store_true", help="combine partial results into the full record")
    run.add_argument(
        "--temporal",
        action="store_true",
        help="run as a durable Temporal workflow (needs --out, a server and `smeltery temporal-worker`)",
    )
    run.add_argument("--workers", type=int, default=None, help="with --temporal: scores run at once (SMELTERY_WORKERS)")
    run.add_argument("--host", default=None, help="with --temporal: the Temporal server (SMELTERY_TEMPORAL_ADDRESS)")
    run.add_argument("--task-queue", dest="task_queue", default=None, help="with --temporal (SMELTERY_PIPELINE_QUEUE)")
    run.add_argument("--namespace", default=None, help="with --temporal (SMELTERY_TEMPORAL_NAMESPACE)")
    me = sub.add_parser("methods", help="every method's config section, knobs and defaults, as TOML")
    me.add_argument("--out", help="write one <key>.toml per method plus index.toml into this directory")
    me.add_argument("--only", help="one method key, e.g. field_interaction")
    pw = sub.add_parser("temporal-worker", help="run a Temporal worker for `smeltery run --temporal`")
    pw.add_argument(
        "--workers", type=int, default=None, help="SCFs at once, one child process each (memory-bound; default 2)"
    )
    pw.add_argument(
        "--threads-per-worker", dest="threads_per_worker", type=int, default=None, help="RAYON_NUM_THREADS per SCF"
    )
    pw.add_argument("--host", default=None, help="Temporal server (env SMELTERY_TEMPORAL_ADDRESS)")
    pw.add_argument("--namespace", default=None, help="env SMELTERY_TEMPORAL_NAMESPACE")
    pw.add_argument("--task-queue", dest="task_queue", default=None, help="env SMELTERY_PIPELINE_QUEUE")
    args = ap.parse_args(argv)
    temporal_settings = None
    if args.command in ("task-worker", "temporal-worker") or (args.command == "run" and args.temporal):
        from .settings import SettingsError, resolve

        # one resolution for the Temporal flags of all three commands: flag > SMELTERY_* env var > default. The
        # `--task-queue` flag means the tasks queue for task-worker and the pipeline queue for the other two.
        is_tasks = args.command == "task-worker"
        try:
            temporal_settings = resolve(
                os.environ,
                temporal_address=args.host,
                namespace=args.namespace,
                task_queue=args.task_queue if is_tasks else None,
                pipeline_queue=None if is_tasks else args.task_queue,
                workers=getattr(args, "workers", None),
                threads_per_worker=getattr(args, "threads_per_worker", None),
                task_concurrency=getattr(args, "concurrency", None),
            )
        except SettingsError as e:
            print(f"config error: {e}", file=sys.stderr)
            return 2
    if getattr(args, "command", None) == "golden":
        from smeltery.golden import main as _golden_main

        return _golden_main(argv[1:])
    if getattr(args, "command", None) == "methods":
        from smeltery.methods import registry
        from smeltery.methods.usage import all_methods_toml, method_toml, write_methods_toml

        if args.only:
            spec = registry().get(args.only)
            if spec is None:
                print(f"no method {args.only!r}; one of: {', '.join(sorted(registry()))}", file=sys.stderr)
                return 2
            print(method_toml(spec), end="")
            return 0
        if args.out:
            paths = write_methods_toml(args.out)
            print(f"wrote {len(paths)} file(s) to {args.out}: index.toml and one per method")
            return 0
        print(all_methods_toml(), end="")
        return 0
    if getattr(args, "command", None) == "task-worker":
        from smeltery.tasks import worker_cli as task_worker_cli

        ts = temporal_settings
        assert ts is not None
        return task_worker_cli(ts.temporal_address, ts.namespace, ts.task_queue, ts.task_concurrency)
    if args.command == "temporal-worker":
        from .temporal_run import worker_cli

        ts = temporal_settings
        assert ts is not None
        return worker_cli(ts.temporal_address, ts.pipeline_queue, ts.workers, ts.threads_per_worker, ts.namespace)
    explicit_out = args.out is not None
    from .core_record import emit_core_record, utc_now

    started_at = utc_now()  # the core record's started_at (a whole run); None without the `attestation` extra
    if args.temporal and (not explicit_out or args.only_candidate is not None or args.combine):
        print(
            "config error: --temporal needs --out OUT, and runs the whole config (no --only-candidate/--combine)",
            file=sys.stderr,
        )
        return 2
    plan = None
    calc_file = "calcs.jsonl"
    if args.out is None:
        args.out = Path("out")
    try:
        config_info = None
        extra_ext = parse_ext(args.ext_json)  # a bad namespace is a config error, before any work
        if explicit_out and not (
            args.plan or args.combine or args.only_candidate is not None or args.temporal or args.force
        ):
            if done := sorted(args.out.glob("run-*.json")):  # a re-run would replace it
                raise ConfigError(
                    f"{args.out} already holds the run record {done[0].name}; use another --out, or --force to rerun"
                )
        if explicit_out and not args.combine:
            args.config, config_info = _stage_config(args.config, args.out, temporal=args.temporal)
        elif explicit_out:  # a combine writes its record into OUT
            _guard_out(args.out, None)
        cfg = load_config(args.config)
        given = tomllib.loads(Path(args.config).read_text())  # verbatim: load_config adds `_dir`, a machine path
        # --plan runs no SCF, so planning one candidate of such a config is fine
        if (args.only_candidate is not None and not args.plan) or args.combine:
            from .temporal_run import remeasuring_analyses

            again = remeasuring_analyses(cfg)
            if again:  # a partial measures one field only; a combine has no SCFs to re-measure with
                raise ConfigError(
                    f"[{again[0].section}] re-measures every pose under other fields, which a partial "
                    "(--only-candidate) run does not do and a combine cannot; run the whole config, or use --temporal"
                )
        if args.temporal and not args.plan:
            from .temporal_run import run_cli

            ts = temporal_settings
            assert ts is not None
            return run_cli(
                args.config,
                args.out,
                ts.workers,
                ts.temporal_address,
                ts.pipeline_queue,
                config_info,
                namespace=ts.namespace,
                run_id=args.run_id,
                arm=args.arm,  # the workflow's record stage writes them into the core record at its first write
                ext=extra_ext,
            )
        if args.only_candidate is not None:
            if args.only_candidate not in cfg["candidates"]:
                print(f"config error: unknown candidate {args.only_candidate!r}", file=sys.stderr)
                return 2
            parent = cfg["parent"]
            if args.only_candidate == parent:
                filtered = {parent: cfg["candidates"][parent]}
            else:
                filtered = {
                    parent: cfg["candidates"][parent],
                    args.only_candidate: cfg["candidates"][args.only_candidate],
                }
            cfg2 = {**cfg, "candidates": filtered}
            plan = build_plan(cfg2, out_dir=args.out)
            if args.plan:
                print(_describe_plan(plan, plan.input_digest))
                return 0
            target_name = args.only_candidate
            calc_file = calc_file_name(target_name)

            def measure_one(p):
                target = next(c for c in p.candidates if c.name == target_name)
                p.tier.run([target], {"field": p.field})

            rec, out = run_plan(plan, measure=measure_one, partial=True)
            if config_info is not None:
                rec.results["config"] = config_info
            path = _stage("outputs", lambda: write_outputs(plan, rec, args.out, calc_file))
            # rename to partial-<name>.json and drop cut
            p = Path(path)
            new_path = p.parent / partial_file_name(args.only_candidate)
            data = json.loads(p.read_text())
            data.get("results", {}).pop("cut", None)
            data.get("results", {}).pop("paired_ddE", None)
            if "record_sha256" in data:  # the pops changed the record: restamp it, or verify-run reports an edit
                stamp_record(data)
            write_text_atomic(new_path, dump_record(data))
            try:
                p.unlink()
            except OSError:
                pass
            print(f"\npartial record: {new_path}\ninput_digest {rec.input_digest}")
            return 0
        if args.combine:
            out_dir = Path(args.out)
            partials = sorted(out_dir.glob(partial_file_name("*")))
            names = {partial_candidate(p) for p in partials}
            analogues = [n for n in cfg["candidates"] if n != cfg["parent"]]
            missing = [n for n in analogues if n not in names]
            if missing:
                print("combine: missing partials for " + ", ".join(missing), file=sys.stderr)
                return 2
            plan = build_plan(cfg, out_dir=args.out)
            per_candidate_values = {}
            parent_values = None
            partial_configs = []
            recs = {p: json.loads(p.read_text()) for p in partials}
            _check_partials(plan, cfg["parent"], recs)
            for p in partials:
                recp = recs[p]
                partial_configs.append(recp["results"].get("config"))
                pp = recp["results"]["per_pose"]
                name = partial_candidate(p)
                if name in pp:
                    per_candidate_values[name] = pp[name]
                if name == cfg["parent"] and parent_values is None and cfg["parent"] in pp:
                    parent_values = pp[cfg["parent"]]
            if parent_values is None:
                print("combine: missing partial for " + cfg["parent"], file=sys.stderr)
                return 2

            def measure(plan):
                for c in plan.candidates:
                    if c is plan.parent:
                        for q, vals in (parent_values or {}).items():
                            c.per_pose[q] = list(vals)
                    else:
                        vals = per_candidate_values.get(c.name, {})
                        for q, v in vals.items():
                            c.per_pose[q] = list(v)

            rec, out = run_plan(plan, measure=measure)
            # the partials staged the config (_check_partials: all the same one); the combine carries it
            if partial_configs and partial_configs[0] is not None:
                rec.results["config"] = partial_configs[0]
            merged = merged_partial_calcs(out_dir, partials)
            path = _stage("outputs", lambda: write_outputs(plan, rec, args.out, calc_rows=merged))
            # a combine's start is its partials' starts: not one instant
            core = emit_core_record(path, None, run_id=args.run_id, arm=args.arm, extra_ext=extra_ext)
            print(format_table(plan, out))
            print(f"\nrun record: {path}\ninput_digest {rec.input_digest}")
            if core is not None:
                print(f"core record: {core}")
            return 4 if args.fail_on_unranked and out["cut"].unranked_at_boundary else 0
        plan = build_plan(cfg, out_dir=args.out)
        if args.plan:
            digest = plan.input_digest
            print(_describe_plan(plan, digest))
            if explicit_out:
                write_plan_preview(plan, digest, args.out, args.config)
            return 0
        checkpoint = stages = None
        if config_info is not None:  # a run into an explicit OUT checkpoints its SCFs there (smeltery.checkpoint)
            from .reuse import RunStages, store_for

            checkpoint = _open_checkpoint(plan, args.out, config_info["sha256"], args.force)
            # every completed stage is logged in OUT; with a stage store, reused or stored (smeltery.reuse)
            stages = RunStages(args.out, store_for(args.stage_cache))
            if stages.why_not:
                print(f"stage reuse off: {stages.why_not}")
        rec, out = run_plan(plan, checkpoint=checkpoint, stages=stages)
        if config_info is not None:
            rec.results["config"] = config_info
        reuse = stages.summary() if stages is not None else None
        if reuse is not None:
            rec.results["reuse"] = reuse
            print(f"stages reused: {reuse['reused']} of {reuse['reusable']} reusable")
        path = _stage("outputs", lambda: write_outputs(plan, rec, args.out))
        if checkpoint is not None:  # the record is written: a finished OUT holds today's outputs only
            from .checkpoint import discard
            from .reuse import discard_progress

            discard(args.out)
            discard_progress(args.out)
        # last: the manifest covers what the run left in OUT. Without --out nothing was staged, so the config the run
        # read is handed over as it read it (with --out, write_core reads OUT/config.toml by its recorded digest)
        core = emit_core_record(
            path,
            started_at,
            run_id=args.run_id,
            config=None if config_info is not None else given,
            arm=args.arm,
            extra_ext=extra_ext,
        )
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        if explicit_out and args.plan and not isinstance(e, OutGuardRefused):
            write_plan_error(args.out, str(e))
        return 2
    except StageError as e:
        print(f"error: {e}", file=sys.stderr)
        if explicit_out and args.plan:
            write_plan_error(args.out, str(e), getattr(e, "stage", None))
        if explicit_out and e.gates:  # a rejected gate still leaves every pose's result
            try:
                args.out.mkdir(parents=True, exist_ok=True)
                write_text_atomic(
                    args.out / "gates.json",
                    json.dumps(_gates_in_out(e.gates, args.out), indent=2, sort_keys=True) + "\n",
                )
            except OSError as oe:  # never mask the exit code of the failure itself
                print(f"warning: could not write gates.json: {oe}", file=sys.stderr)
        if explicit_out and plan is not None:  # a failed SCF still leaves the SCFs it ran
            try:
                write_calc_log(plan, args.out, calc_file)
            except OSError as oe:  # the log is a courtesy: never mask the exit code of the failure itself
                print(f"warning: could not write {calc_file}: {oe}", file=sys.stderr)
        return 3
    print(format_table(plan, out))
    print(f"\nrun record: {path}\ninput_digest {rec.input_digest}")
    if core is not None:
        print(f"core record: {core}")
    return 4 if args.fail_on_unranked and out["cut"].unranked_at_boundary else 0


if __name__ == "__main__":
    sys.exit(main())
