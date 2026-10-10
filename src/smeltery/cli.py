"""`smeltery run <config.toml>`: poses -> field interaction -> paired ddE -> cut -> run record.

One honest path, not a framework. Stages, in order, each a loud failure when it fails
(exit 3, naming the stage); nothing is skipped silently:

1. `PairedPoses`: the parent's ETKDG ensemble plus analogue poses paired to it. The
   MCS gate refuses a pair with no common core.
2. pocket: point charges placed from the parent's pose 0 (see `[[pocket.site]]`).
3. parity: every candidate must have an even electron count at its formal charge,
   because the tier is closed-shell RHF. An odd count is a stage error, never a
   silent zero-survivor run.
4. `FieldInteraction`: dE_int = E(field) - E(vacuum) per pose (ferric RHF).
5. paired ddE against the parent, then `cut`.

The cut's floor is the tier's own systematic floor. While that is unmeasured
(`FieldInteraction` today) the config must state `[cut] floor` explicitly, and the record
says the floor came from the config. Candidates the cut cannot separate are printed as
tie groups; a run where nothing can be ordered prints UNRANKED and still exits 0.

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
    [field_interaction]     # optional: FieldInteraction knobs
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
    xtb = true              # GFN2 energy per pose in the pocket field, recorded (a gate, not a ranker)

Exit codes: 0 done (including UNRANKED), 2 bad config, 3 a stage failed.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from rdkit import Chem

from .funnel import cut, paired_delta, tier_floor, unpaired_delta
from .gates import PoseGateError, check_candidate_poses, require_passing_poses
from .model import Candidate, PointCharge
from .pocket import load_pocket
from .pocket.loader import file_digest
from .record import RunRecord
from .tiers import FieldInteraction, ForceField, Gfn2, PairedPoses, _net_charge

TOP_KEYS = {"campaign", "parent", "candidates", "poses", "pocket", "field_interaction", "cut", "docking", "gates"}
SITE_KEYS = {"q", "atom", "from_atom", "distance"}
POCKET_KEYS = {"site", "file"}
DOCKING_KEYS = {"receptor", "box_center", "box_size", "seeds", "exhaustiveness", "n_poses"}
GATE_KEYS = {"posebusters", "forcefield", "xtb"}
CUT_KEYS = {"keep", "z", "floor"}
QUANTITY = "dE_int"


class ConfigError(ValueError):
    """The config is malformed or names something that does not exist. Exit code 2."""


class StageError(RuntimeError):
    """A pipeline stage failed. Exit code 3."""


@dataclass
class Plan:
    """Everything decided before the SCFs run: candidates with poses, the field, the tiers, the inputs."""

    cfg: dict
    candidates: list[Candidate]
    parent: Candidate
    field: list[PointCharge]
    poses: PairedPoses
    tier: FieldInteraction
    floor: float
    floor_source: str
    before_poses: list = dataclasses.field(default_factory=list)  # tiers that ran before pairing (docking)
    after_poses: list = dataclasses.field(default_factory=list)  # recorded tiers between pairing and the SCFs
    extra_inputs: dict = dataclasses.field(default_factory=dict)  # file digests, box: what the stages read
    stage_log: list = dataclasses.field(default_factory=list)  # one dict per stage: its name and a summary line

    @property
    def inputs(self) -> dict:
        gates = self.cfg.get("gates", {})
        inputs = {
            "candidates": {c.name: c.smiles for c in self.candidates},
            "parent": self.parent.name,
            "cut": {"keep": self.cfg["cut"]["keep"], "z": self.cfg["cut"].get("z", 2.0)},
            "floor": {"value": self.floor, "source": self.floor_source},
            "gates": {k: bool(gates.get(k, False)) for k in sorted(GATE_KEYS)},
            **self.extra_inputs,
        }
        if "pocket" not in self.extra_inputs:  # sites: the charges ARE the input; a file: its digest is
            inputs["field"] = [(c.q, c.xyz_ang) for c in self.field]
        return inputs

    @property
    def tiers(self) -> list[dict]:
        ordered = [*self.before_poses, self.poses, *self.after_poses, self.tier]
        return [{"name": t.name, **t.settings()} for t in ordered]


def _check_keys(where: str, got: dict, allowed: set[str]) -> None:
    extra = sorted(set(got) - allowed)
    if extra:
        raise ConfigError(f"unknown key(s) {extra} in {where}; allowed: {sorted(allowed)}")


def _knobs(cfg: dict, section: str, cls: type) -> dict:
    got = cfg.get(section, {})
    if not isinstance(got, dict):
        raise ConfigError(f"[{section}] must be a table")
    internal = {"name", "field_provenance", "scaffold_maps"}  # state the tiers fill in themselves, not knobs
    allowed = {f.name for f in dataclasses.fields(cls) if f.init and f.name not in internal}
    _check_keys(f"[{section}]", got, allowed)
    return got


def load_config(path: Path) -> dict:
    try:
        cfg = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    cfg = validate_config(cfg)
    cfg["_dir"] = str(Path(path).resolve().parent)  # relative paths in the config resolve against it
    return cfg


def validate_config(cfg: dict) -> dict:
    _check_keys("the top level", cfg, TOP_KEYS)
    for key in ("campaign", "parent", "candidates", "cut"):
        if key not in cfg:
            raise ConfigError(f"missing required key {key!r}")
    cands = cfg["candidates"]
    if not isinstance(cands, dict) or len(cands) < 2:
        raise ConfigError("[candidates] needs the parent and at least one analogue (name = SMILES)")
    if cfg["parent"] not in cands:
        raise ConfigError(f"parent {cfg['parent']!r} is not listed in [candidates]")
    for name, smi in cands.items():
        if not isinstance(smi, str) or Chem.MolFromSmiles(smi) is None:
            raise ConfigError(f"candidate {name!r}: unparseable SMILES {smi!r}")
    poses_cfg = _knobs(cfg, "poses", PairedPoses)
    _knobs(cfg, "field_interaction", FieldInteraction)
    _validate_docking(cfg, poses_cfg)
    _validate_gates(cfg)
    cut_cfg = cfg["cut"]
    _check_keys("[cut]", cut_cfg, CUT_KEYS)
    n_analogues = len(cands) - 1
    if not isinstance(cut_cfg.get("keep"), int) or not 1 <= cut_cfg["keep"] <= n_analogues:
        raise ConfigError(f"[cut] keep must be an integer in 1..{n_analogues} (the number of analogues)")
    pocket = cfg.get("pocket", {})
    _check_keys("[pocket]", pocket, POCKET_KEYS)
    sites = pocket.get("site", [])
    if "file" in pocket:
        if sites:
            raise ConfigError("[pocket] takes either file or [[pocket.site]], not both")
        if not isinstance(pocket["file"], str) or Path(pocket["file"]).suffix.lower() not in (".pdb", ".pqr"):
            raise ConfigError("[pocket] file must be a path to a .pdb or .pqr")
    elif not sites:
        raise ConfigError(
            "[[pocket.site]] (or [pocket] file) needs at least one charge: an empty field has no interaction"
        )
    for i, s in enumerate(sites):
        _check_keys(f"[[pocket.site]] #{i}", s, SITE_KEYS)
        missing = sorted(SITE_KEYS - set(s))
        if missing:
            raise ConfigError(f"[[pocket.site]] #{i} is missing {missing}")
    return cfg


def _validate_docking(cfg: dict, poses_cfg: dict) -> None:
    if "docking" not in cfg:
        return
    d = cfg["docking"]
    if not isinstance(d, dict):
        raise ConfigError("[docking] must be a table")
    _check_keys("[docking]", d, DOCKING_KEYS)
    for key in ("receptor", "box_center"):
        if key not in d:
            raise ConfigError(f"[docking] is missing {key!r}")
    if not isinstance(d["receptor"], str):
        raise ConfigError("[docking] receptor must be a path to a prepared receptor (.pdbqt)")
    for key in ("box_center", "box_size"):
        v = d.get(key, [0, 0, 0])
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)):
            raise ConfigError(f"[docking] {key} must be three numbers (Angstrom, receptor frame)")
    if "box_size" in d and not all(x > 0 for x in d["box_size"]):
        raise ConfigError("[docking] box_size must be positive")
    if "seeds" in d and not (d["seeds"] and all(isinstance(x, int) for x in d["seeds"])):
        raise ConfigError("[docking] seeds must be a non-empty list of integers")
    for key in ("exhaustiveness", "n_poses"):
        if key in d and not (isinstance(d[key], int) and d[key] >= 1):
            raise ConfigError(f"[docking] {key} must be a positive integer")
    dead = sorted({"n_poses", "jitter_deg", "jitter_ang"} & set(poses_cfg))
    if dead:
        raise ConfigError(
            f"[poses] {dead} have no effect when the parent is docked (the parent's poses are used as docked); "
            "set the pose count with [docking] n_poses"
        )


def _validate_gates(cfg: dict) -> None:
    g = cfg.get("gates", {})
    if not isinstance(g, dict):
        raise ConfigError("[gates] must be a table")
    _check_keys("[gates]", g, GATE_KEYS)
    bad = sorted(k for k, v in g.items() if not isinstance(v, bool))
    if bad:
        raise ConfigError(f"[gates] {bad} must be true or false")


def _stage(name: str, fn):
    try:
        return fn()
    except (ConfigError, StageError):
        raise
    except Exception as e:
        raise StageError(f"stage {name!r} failed: {type(e).__name__}: {e}") from e


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
        electrons = sum(pt.GetAtomicNumber(s) for s in c.poses[0].symbols) - _net_charge(c.smiles)
        if electrons % 2:
            raise StageError(
                f"stage 'parity' failed: {c.name} has {electrons} electrons at charge {_net_charge(c.smiles):+d}; "
                "the field-interaction tier is closed-shell RHF and is undefined for an odd electron count"
            )


def _resolve(cfg: dict, name: str) -> Path:
    """A path from the config: absolute as given, otherwise relative to the config file's directory."""
    p = Path(name)
    return p if p.is_absolute() else Path(cfg.get("_dir", ".")) / p


def _dock_parent(cfg: dict, parent: Candidate, log: list, extra: dict):
    from .docking import Box, Docking, VinaProvider  # the docking extra: imported only when docking is configured

    d = cfg["docking"]
    receptor = _resolve(cfg, d["receptor"])
    if not receptor.is_file():
        raise ConfigError(f"[docking] receptor {str(receptor)!r} does not exist")
    provider_kw = {k: d[k] for k in ("exhaustiveness", "n_poses") if k in d}
    tier_kw = {k: d[k] for k in ("exhaustiveness",) if k in d}
    if "seeds" in d:
        tier_kw["seeds"] = tuple(d["seeds"])
    tier = Docking(VinaProvider(**provider_kw), **tier_kw)
    box = Box(tuple(float(x) for x in d["box_center"]), tuple(float(x) for x in d.get("box_size", (24.0, 24.0, 24.0))))
    tier.run([parent], {"receptor": str(receptor), "box": box})
    scores = [float(x) for x in parent.per_pose[tier.QUANTITY]]
    extra["docking"] = {
        "receptor": receptor.name,
        "receptor_sha256": file_digest(receptor),
        "box": {"center": list(box.center), "size": list(box.size)},
    }
    unit = tier.produces()[tier.QUANTITY]
    best = f"best {min(scores):.2f} {unit}"
    log.append(
        {"stage": "docking", "summary": f"docking {parent.name}: {len(parent.poses)} pose(s), {best}", "scores": scores}
    )
    return tier


def _load_field(cfg: dict, parent: Candidate, extra: dict, log: list) -> tuple[list[PointCharge], Path | None]:
    pocket = cfg["pocket"]
    if "file" not in pocket:
        sites = _place_sites(parent, pocket["site"])
        log.append(
            {"stage": "pocket", "summary": f"pocket: {len(sites)} point charge(s) placed from {parent.name} pose 0"}
        )
        return sites, None
    path = _resolve(cfg, pocket["file"])
    if not path.is_file():
        raise ConfigError(f"[pocket] file {str(path)!r} does not exist")
    field = load_pocket(path)
    if not len(field):
        raise StageError("stage 'pocket' failed: the pocket file produced no point charges")
    extra["pocket"] = dict(field.provenance)
    log.append({"stage": "pocket", "summary": f"pocket {path.name}: {len(field)} point charges"})
    return field, path


def cfg_poses_relax(cfg: dict) -> bool:
    return bool(cfg.get("poses", {}).get("relax_unmapped", False))


def _run_gates(plan_cfg: dict, cands: list[Candidate], field, pocket_path: Path | None, log: list) -> list:
    """The recorded stages between pairing and the SCFs. Returns the tiers to put in the record."""
    gates = plan_cfg.get("gates", {})
    recorded = []
    if gates.get("posebusters"):
        receptor = str(pocket_path) if pocket_path is not None and pocket_path.suffix.lower() == ".pdb" else None

        def posebusters():
            for c in cands:
                report = check_candidate_poses(c, receptor)
                try:
                    require_passing_poses(c, {"require_pose_report": True})
                except PoseGateError as e:
                    hint = ""
                    if c.name != plan_cfg["parent"] and not cfg_poses_relax(plan_cfg):
                        hint = (
                            " Analogue poses copy the parent's core and embed the substituent unrelaxed, which "
                            "PoseBusters rejects at the junction; [poses] relax_unmapped = true relaxes only the "
                            "substituent (the core stays exactly the parent's)."
                        )
                    raise StageError(f"stage 'posebusters' failed: {e}.{hint}") from e
            return report

        report = _stage("posebusters", posebusters)
        n = sum(len(c.poses) for c in cands)
        mode = "ligand + protein" if receptor else "ligand only"
        log.append(
            {
                "stage": "posebusters",
                "summary": f"posebusters ({mode}): all {n} pose(s) of {len(cands)} candidate(s) pass",
                "posebusters_version": getattr(report, "posebusters_version", None),
            }
        )
    if gates.get("forcefield"):
        ff = ForceField()
        _stage("forcefield", lambda: ff.run(cands, {}))
        strain = {
            c.name: [a - b for a, b in zip(c.per_pose["E_mmff"], c.per_pose["E_mmff_relaxed"], strict=True)]
            for c in cands
        }
        worst = max(max(v) for v in strain.values())
        log.append(
            {
                "stage": "forcefield",
                "summary": f"forcefield MMFF94 recorded; largest pose strain {worst:.1f} kcal/mol",
                "strain": strain,
            }
        )
        recorded.append(ff)
    if gates.get("xtb"):
        xtb = Gfn2()
        _stage("xtb", lambda: xtb.run(cands, {"field": field}))
        log.append(
            {
                "stage": "xtb",
                "summary": "xtb GFN2 recorded (a gate, not a ranker)",
                "E_gfn2": {c.name: [float(x) for x in c.per_pose["E_gfn2"]] for c in cands},
            }
        )
        recorded.append(xtb)
    return recorded


def build_plan(cfg: dict) -> Plan:
    """Everything before the field-interaction SCFs: docking, pairing, pocket, parity, gates and the floor."""
    cands = [Candidate(n, s) for n, s in cfg["candidates"].items()]
    parent = next(c for c in cands if c.name == cfg["parent"])
    log: list = []
    extra: dict = {}
    before = []
    if "docking" in cfg:
        before.append(_stage("docking", lambda: _dock_parent(cfg, parent, log, extra)))
    poses = PairedPoses(parent_poses_given="docking" in cfg, **cfg.get("poses", {}))
    _stage("poses", lambda: poses.run(cands, {"parent": parent}))
    log.append(
        {"stage": "poses", "summary": f"paired poses: {len(parent.poses)} per candidate, {len(cands)} candidates"}
    )
    field, pocket_path = _stage("pocket", lambda: _load_field(cfg, parent, extra, log))
    _stage("parity", lambda: _check_parity(cands))
    after = _run_gates(cfg, cands, field, pocket_path, log)
    tier = FieldInteraction(**cfg.get("field_interaction", {}))
    tier.field_provenance = getattr(field, "provenance", None)  # what run() will record, so plan and run digests agree
    try:
        floor, source = tier_floor(tier, QUANTITY), f"tier {tier.name} systematic_floor"
    except ValueError as e:
        if "floor" not in cfg["cut"]:
            raise ConfigError(f"{e} Set [cut] floor explicitly to proceed, knowing it is not a measured floor.") from e
        floor, source = float(cfg["cut"]["floor"]), "config [cut] floor (explicit; the tier's own is unmeasured)"
    return Plan(cfg, cands, parent, field, poses, tier, floor, source, before, after, extra, log)


def run_plan(plan: Plan) -> tuple[RunRecord, dict]:
    _stage("field_interaction", lambda: plan.tier.run(plan.candidates, {"field": plan.field}))
    analogues = [c for c in plan.candidates if c is not plan.parent]
    paired = {c.name: paired_delta(plan.parent, c, QUANTITY) for c in analogues}
    unpaired = {c.name: unpaired_delta(plan.parent, c, QUANTITY) for c in analogues}
    res = cut(paired, keep=plan.cfg["cut"]["keep"], z=plan.cfg["cut"].get("z", 2.0), floor=plan.floor)
    results = {
        "paired_ddE": {k: {"mean": v.mean, "sem": v.sem, "n": v.n, "per_pose": v.per_pose} for k, v in paired.items()},
        "unpaired_sem": {k: v.sem for k, v in unpaired.items()},
        "cut": {"groups": res.groups, "survivors": res.survivors, "unranked": res.unranked_at_boundary},
        "stages": plan.stage_log,
    }
    rec = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, results)
    return rec, {"paired": paired, "unpaired": unpaired, "cut": res}


def format_table(plan: Plan, out: dict) -> str:
    res = out["cut"]
    lines = [f"{'candidate':12} {'formula':10} {'paired ddE (kcal/mol)':>26} {'unpaired SEM':>13}"]
    for c in plan.candidates:
        if c is plan.parent:
            continue
        m = out["paired"][c.name]
        lines.append(f"{c.name:12} {c.formula:10} {m.mean:+12.4f} ± {m.sem:.4f} {out['unpaired'][c.name].sem:13.4f}")
    lines.append("\nstages: " + " -> ".join(e["summary"] for e in plan.stage_log) + " -> field_interaction")
    lines.append(f"floor {plan.floor:g} kcal/mol, from {plan.floor_source}; z={res.z:g}")
    lines.append("tie groups (no order within a group): " + " | ".join(", ".join(g) for g in res.groups))
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
        f"floor {plan.floor:g} from {plan.floor_source}\ninput_digest {digest}"
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="smeltery", description="Tiered, measurement-disciplined funnel.")
    sub = ap.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run a campaign config")
    run.add_argument("config", type=Path)
    run.add_argument("--out", type=Path, default=Path("out"), help="directory for the run record")
    run.add_argument("--plan", action="store_true", help="stop before the SCFs: poses, pocket, parity, digest")
    args = ap.parse_args(argv)
    try:
        plan = build_plan(load_config(args.config))
        if args.plan:
            print(_describe_plan(plan, RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest))
            return 0
        rec, out = run_plan(plan)
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        return 2
    except StageError as e:
        print(f"error: {e}", file=sys.stderr)
        return 3
    print(format_table(plan, out))
    args.out.mkdir(parents=True, exist_ok=True)
    path = args.out / f"run-{rec.input_digest[:12]}.json"
    path.write_text(rec.to_json())
    print(f"\nrun record: {path}\ninput_digest {rec.input_digest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
