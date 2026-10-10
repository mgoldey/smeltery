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
from .model import Candidate, PointCharge
from .record import RunRecord
from .tiers import FieldInteraction, PairedPoses, _net_charge

TOP_KEYS = {"campaign", "parent", "candidates", "poses", "pocket", "field_interaction", "cut"}
SITE_KEYS = {"q", "atom", "from_atom", "distance"}
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

    @property
    def inputs(self) -> dict:
        return {
            "candidates": {c.name: c.smiles for c in self.candidates},
            "parent": self.parent.name,
            "field": [(c.q, c.xyz_ang) for c in self.field],
            "cut": {"keep": self.cfg["cut"]["keep"], "z": self.cfg["cut"].get("z", 2.0)},
            "floor": {"value": self.floor, "source": self.floor_source},
        }

    @property
    def tiers(self) -> list[dict]:
        return [{"name": t.name, **t.settings()} for t in (self.poses, self.tier)]


def _check_keys(where: str, got: dict, allowed: set[str]) -> None:
    extra = sorted(set(got) - allowed)
    if extra:
        raise ConfigError(f"unknown key(s) {extra} in {where}; allowed: {sorted(allowed)}")


def _knobs(cfg: dict, section: str, cls: type) -> dict:
    got = cfg.get(section, {})
    if not isinstance(got, dict):
        raise ConfigError(f"[{section}] must be a table")
    allowed = {f.name for f in dataclasses.fields(cls) if f.init and f.name != "name"}
    _check_keys(f"[{section}]", got, allowed)
    return got


def load_config(path: Path) -> dict:
    try:
        cfg = tomllib.loads(Path(path).read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    return validate_config(cfg)


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
    _knobs(cfg, "poses", PairedPoses)
    _knobs(cfg, "field_interaction", FieldInteraction)
    cut_cfg = cfg["cut"]
    _check_keys("[cut]", cut_cfg, CUT_KEYS)
    n_analogues = len(cands) - 1
    if not isinstance(cut_cfg.get("keep"), int) or not 1 <= cut_cfg["keep"] <= n_analogues:
        raise ConfigError(f"[cut] keep must be an integer in 1..{n_analogues} (the number of analogues)")
    sites = cfg.get("pocket", {}).get("site", [])
    _check_keys("[pocket]", cfg.get("pocket", {}), {"site"})
    if not sites:
        raise ConfigError("[[pocket.site]] needs at least one charge: a field of nothing has no interaction")
    for i, s in enumerate(sites):
        _check_keys(f"[[pocket.site]] #{i}", s, SITE_KEYS)
        missing = sorted(SITE_KEYS - set(s))
        if missing:
            raise ConfigError(f"[[pocket.site]] #{i} is missing {missing}")
    return cfg


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


def build_plan(cfg: dict) -> Plan:
    """Poses, pocket, parity and the floor: everything before the SCFs. Needs RDKit only."""
    cands = [Candidate(n, s) for n, s in cfg["candidates"].items()]
    parent = next(c for c in cands if c.name == cfg["parent"])
    poses = PairedPoses(**cfg.get("poses", {}))
    _stage("poses", lambda: poses.run(cands, {"parent": parent}))
    field = _place_sites(parent, cfg["pocket"]["site"])
    _stage("parity", lambda: _check_parity(cands))
    tier = FieldInteraction(**cfg.get("field_interaction", {}))
    try:
        floor, source = tier_floor(tier, QUANTITY), f"tier {tier.name} systematic_floor"
    except ValueError as e:
        if "floor" not in cfg["cut"]:
            raise ConfigError(f"{e} Set [cut] floor explicitly to proceed, knowing it is not a measured floor.") from e
        floor, source = float(cfg["cut"]["floor"]), "config [cut] floor (explicit; the tier's own is unmeasured)"
    return Plan(cfg, cands, parent, field, poses, tier, floor, source)


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
    lines.append(f"\nfloor {plan.floor:g} kcal/mol, from {plan.floor_source}; z={res.z:g}")
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
        f"{len(plan.field)} pocket charge(s); {plan.poses.n_poses} poses per candidate; "
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
