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
a Temporal workflow, one activity per (candidate, pose, field model), and `smeltery temporal-worker` executes it. The
same OUT and staged config resume the same workflow; finished SCFs are read back from OUT/calcs.jsonl. With the server
down it exits 2 naming the docker command; it never falls back to an in-process run.

    [run]
    scf_timeout_s = 14400       # bounds one SCF leg (one rung) under --temporal (not an input: no digest change)
    [field_interaction]
    remedies = [{level_shift = 0.5}, {max_iter = 300, soscf = true}]  # tried in order when an SCF does not converge

Exit codes: 0 done (including UNRANKED), 2 bad config, 3 a stage failed, 4 UNRANKED under `--fail-on-unranked`
(in-process runs; not `--temporal`).
"""

from __future__ import annotations

import argparse
import contextvars
import dataclasses
import hashlib
import json
import os
import re
import sys
import time
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import numpy as np
from rdkit import Chem

from .cost import available_memory_gb
from .decisions import DecisionSet
from .funnel import cut, paired_delta, tier_floor, unpaired_delta
from .gates import PoseGateError, check_candidate_poses, require_passing_poses
from .methods import Analysis, FieldSource, Measure, MethodSpec, PoseSource, active, active_hooks, by_slot, registry
from .model import Candidate, PointCharge
from .pocket import load_pocket
from .pocket.loader import file_digest
from .record import RunRecord
from .tiers import (
    FieldDftInteraction,
    FieldInteraction,
    ForceField,
    Gfn2,
    PairedPoses,
    _net_charge,
    check_remedies,
)

#: Top-level keys that are inputs of the pipeline, not a method's section (the Ligands step, and the gate switches).
OWN_TOP_KEYS = {"campaign", "parent", "candidates", "gates", "decisions", "run"}
#: `[run]`: how the run executes, not what it measures (not an input: it never moves the digest).
RUN_KEYS = {"scf_timeout_s", "allow_mixed_builds"}
#: `[run] scf_timeout_s` default: one SCF leg (vacuum or field, one rung) is ended after this long under `--temporal`.
SCF_TIMEOUT_S = 14400
DECISIONS_KEYS = {"file"}
SITE_KEYS = {"q", "atom", "from_atom", "distance"}
QUANTITY = "dE_int"
POSES_DIR = "poses"


def top_keys() -> set[str]:
    """Every key a config may have at the top level: the pipeline's own, plus each registered method's section."""
    return OWN_TOP_KEYS | {s.section for s in registry().values() if s.section is not None}


def section_keys(key: str) -> set[str]:
    """The knobs method `key`'s config section accepts (its registration's `knobs` dataclass or `params`)."""
    return set(registry()[key].knob_names())


def gate_keys() -> set[str]:
    """The switches under `[gates]`: each registered gate method's `gate_key`."""
    return {s.gate_key for s in registry().values() if s.gate_key is not None}


def gate_option_keys() -> set[str]:
    """Explicit rejection thresholds under `[gates]` (absent = recorded only): the gate methods' own knobs."""
    return {k for s in registry().values() if s.gate_key is not None for k in s.knob_names()}


#: Names that used to be hand-written sets, now derived from the method registry on every read.
_DERIVED = {
    "TOP_KEYS": top_keys,
    "GATE_KEYS": gate_keys,
    "GATE_OPTION_KEYS": gate_option_keys,
    "STRUCTURE_KEYS": lambda: section_keys("structure"),
    "POCKET_KEYS": lambda: section_keys("pocket"),
    "DOCKING_KEYS": lambda: section_keys("docking"),
    "CUT_KEYS": lambda: section_keys("cut"),
    "POSE_SECTIONS": lambda: pose_sections(),
}


def __getattr__(name: str):
    if name in _DERIVED:
        return _DERIVED[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ConfigError(ValueError):
    """The config is malformed or names something that does not exist. Exit code 2."""


class StageError(RuntimeError):
    """A pipeline stage failed. Exit code 3.

    `gates`: when the failure is a gate rejection, every gate result computed before it stopped (the
    `results["gates"]` dict), so the CLI can leave OUT/gates.json behind.
    """

    gates: dict | None = None
    stage: str | None = None  # the failing stage's name, when known

    def __init__(self, *args, stage: str | None = None):
        super().__init__(*args)
        self.stage = stage


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
    """Check a config (as `tomllib` parses it); return it unchanged or raise ConfigError naming the first problem.

    The top-level keys and every method section's rules come from the method registry: each registration's
    `validate`, or the generic section check (`_validate_section`) for one that declares only its knobs."""
    _check_keys("the top level", cfg, top_keys())
    for key in ("campaign", "parent", "candidates"):
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
    for spec in registry().values():
        if spec.validate is not None:
            spec.validate(cfg)
        else:
            _validate_section(spec, cfg)
    measures = [s for s in by_slot("measure") if s.section in cfg]
    if len(measures) > 1:
        names = " and ".join(f"[{s.section}]" for s in measures)
        raise ConfigError(f"{names} each choose how dE_int is measured; keep one")
    for slot in ("pose_source", "field_source"):
        hooks = active_hooks(slot, cfg)
        if len(hooks) > 1:
            raise ConfigError(f"{' and '.join(f'[{s.section}]' for s in hooks)} are each a {slot}; keep one")
    for spec in active_hooks("pose_source", cfg):
        for other, why in (("structure", "[structure] implies docking, which"), ("docking", "[docking]")):
            if other in cfg:
                raise ConfigError(f"[{spec.section}] gives the parent's poses, and {why} gives them too; keep one")
    for spec in active_hooks("field_source", cfg):
        if "structure" in cfg:
            raise ConfigError(
                f"[{spec.section}] gives the point-charge field, and [structure] supplies the field from its prepared "
                "receptor; keep one"
            )
    _validate_gates(cfg)
    _validate_decisions(cfg)
    _validate_run(cfg)
    return cfg


def _validate_run(cfg: dict) -> None:
    """[run]: execution settings. `scf_timeout_s` (seconds, > 0) bounds one SCF leg (one rung) under `--temporal`;
    `allow_mixed_builds` (bool) lets a --temporal run whose SCFs ran under different builds be finalized."""
    if "run" not in cfg:
        return
    r = cfg["run"]
    if not isinstance(r, dict):
        raise ConfigError("[run] must be a table")
    _check_keys("[run]", r, RUN_KEYS)
    t = r.get("scf_timeout_s", SCF_TIMEOUT_S)
    if isinstance(t, bool) or not isinstance(t, (int, float)) or t <= 0:
        raise ConfigError(f"[run] scf_timeout_s must be a positive number of seconds, got {t!r}")
    if not isinstance(r.get("allow_mixed_builds", False), bool):
        raise ConfigError(f"[run] allow_mixed_builds must be true or false, got {r['allow_mixed_builds']!r}")


def allow_mixed_builds(cfg: dict) -> bool:
    """`[run] allow_mixed_builds`: a --temporal run whose calcs.jsonl rows name more than one (ferric, smeltery) build
    is finalized (recorded as mixed in results.builds) instead of refused."""
    return bool((cfg.get("run") or {}).get("allow_mixed_builds", False))


def scf_timeout_s(cfg: dict) -> float:
    """`[run] scf_timeout_s`, or the default (4 h)."""
    return float((cfg.get("run") or {}).get("scf_timeout_s", SCF_TIMEOUT_S))


def _exclusive_with(spec: MethodSpec) -> list[MethodSpec]:
    """The methods that cannot share a config with `spec`: those it names, and those that name it."""
    reg = registry()
    keys = list(spec.exclusive) + [o.key for o in reg.values() if spec.key in o.exclusive]
    return [reg[k] for k in dict.fromkeys(keys) if k in reg]


def _validate_section(spec: MethodSpec, cfg: dict) -> None:
    """The generic rules of a method section: a table, of the method's knobs only, beside no exclusive method's."""
    if spec.section is None or spec.section not in cfg:
        return
    got = cfg[spec.section]
    if not isinstance(got, dict):
        raise ConfigError(f"[{spec.section}] must be a table")
    _check_keys(f"[{spec.section}]", got, set(spec.knob_names()))
    for other in _exclusive_with(spec):
        if other.section is not None and other.section in cfg:
            raise ConfigError(f"[{spec.section}] ({spec.label}) cannot be combined with [{other.section}]; keep one")


def _validate_measurement(cfg: dict) -> None:
    """[field_interaction] (RHF) or [field_dft_interaction] (KS-DFT): tier knobs, and at most one of them."""
    rhf = _knobs(cfg, "field_interaction", FieldInteraction)
    _knobs(cfg, "field_dft_interaction", FieldDftInteraction)
    if "remedies" in rhf:
        try:
            check_remedies(rhf["remedies"])
        except ValueError as e:
            raise ConfigError(f"[field_interaction] {e}") from e
    if "field_interaction" in cfg and "field_dft_interaction" in cfg:
        raise ConfigError(
            "[field_interaction] (RHF) and [field_dft_interaction] (KS-DFT) both choose how dE_int is measured; "
            "keep one"
        )


def _validate_cut(cfg: dict) -> None:
    if "cut" not in cfg:
        raise ConfigError("missing required key 'cut'")
    cut_cfg = cfg["cut"]
    if not isinstance(cut_cfg, dict):
        raise ConfigError("[cut] must be a table")
    _check_keys("[cut]", cut_cfg, section_keys("cut"))
    n_analogues = len(cfg["candidates"]) - 1
    if not isinstance(cut_cfg.get("keep"), int) or not 1 <= cut_cfg["keep"] <= n_analogues:
        raise ConfigError(f"[cut] keep must be an integer in 1..{n_analogues} (the number of analogues)")


def _validate_pocket(cfg: dict) -> None:
    """[pocket]: a file or point-charge sites, unless the field comes from elsewhere ([structure], or another
    registered field source)."""
    pocket = cfg.get("pocket", {})
    elsewhere = ["structure"] if "structure" in cfg else []
    elsewhere += [s.section for s in active_hooks("field_source", cfg) if s.key != "pocket" and s.section]
    if elsewhere:
        if pocket:
            raise ConfigError(
                f"[pocket] cannot be combined with [{elsewhere[0]}]: the field comes from [{elsewhere[0]}]"
            )
        return
    if not isinstance(pocket, dict):
        raise ConfigError("[pocket] must be a table")
    _check_keys("[pocket]", pocket, section_keys("pocket"))
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


def _validate_structure(cfg: dict) -> None:
    if "structure" not in cfg:
        return
    s = cfg["structure"]
    if not isinstance(s, dict):
        raise ConfigError("[structure] must be a table")
    _check_keys("[structure]", s, section_keys("structure"))
    if not isinstance(s.get("spec"), str) or not s["spec"]:
        raise ConfigError("[structure] spec is required: a .pdb path, a PDB id or (provider afdb) a UniProt accession")
    if s.get("provider", "pdb") not in ("pdb", "afdb"):
        raise ConfigError("[structure] provider must be 'pdb' or 'afdb'")
    if not isinstance(s.get("allow_network", False), bool):
        raise ConfigError("[structure] allow_network must be true or false")
    if ("center" in s) == ("reference_ligand" in s):
        raise ConfigError("[structure] needs exactly one of center = [x, y, z] or reference_ligand = 'RES[:CHAIN]'")
    if "center" in s:
        v = s["center"]
        if not (isinstance(v, list) and len(v) == 3 and all(isinstance(x, (int, float)) for x in v)):
            raise ConfigError("[structure] center must be three numbers (Angstrom, the structure's frame)")
    elif s.get("provider", "pdb") == "afdb" or not isinstance(s["reference_ligand"], str):
        raise ConfigError("[structure] reference_ligand is a residue name in an experimental structure")
    cutoff = s.get("pocket_cutoff")
    if isinstance(cutoff, bool) or not isinstance(cutoff, (int, float)) or cutoff <= 0:
        raise ConfigError(
            "[structure] pocket_cutoff (Angstrom, > 0) is required: truncating the field is not monotone, "
            "so there is no default cutoff"
        )
    if "chains" in s and not (s["chains"] and all(isinstance(c, str) and len(c) == 1 for c in s["chains"])):
        raise ConfigError("[structure] chains must be a non-empty list of one-letter chain ids")


def _validate_docking(cfg: dict) -> None:
    poses_cfg = cfg.get("poses", {}) if isinstance(cfg.get("poses", {}), dict) else {}
    structure = "structure" in cfg
    if "docking" not in cfg and not structure:
        return
    d = cfg.get("docking", {})
    if not isinstance(d, dict):
        raise ConfigError("[docking] must be a table")
    _check_keys("[docking]", d, section_keys("docking"))
    if structure:
        clash = sorted({"receptor", "box_center"} & set(d))
        if clash:
            raise ConfigError(
                f"[docking] {clash} come from [structure] (prepared receptor, stated centre); remove them"
            )
    else:
        for key in ("receptor", "box_center"):
            if key not in d:
                raise ConfigError(f"[docking] is missing {key!r}")
        if not isinstance(d["receptor"], str):
            raise ConfigError("[docking] receptor must be a path to a prepared receptor (.pdbqt)")
    for key in ("box_center", "box_size"):
        if key not in d and structure:
            continue
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


def _validate_decisions(cfg: dict) -> None:
    if "decisions" not in cfg:
        return
    d = cfg["decisions"]
    if not isinstance(d, dict):
        raise ConfigError("[decisions] must be a table")
    _check_keys("[decisions]", d, DECISIONS_KEYS)
    if not isinstance(d.get("file"), str) or not d["file"]:
        raise ConfigError("[decisions] file is required: the path of a decisions JSON (smeltery.decisions)")


def _canonical(smiles: str) -> str:
    return Chem.MolToSmiles(Chem.MolFromSmiles(smiles))


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


def _validate_gates(cfg: dict) -> None:
    g = cfg.get("gates", {})
    if not isinstance(g, dict):
        raise ConfigError("[gates] must be a table")
    switches = gate_keys()
    _check_keys("[gates]", g, switches | gate_option_keys())
    bad = sorted(k for k, v in g.items() if k in switches and not isinstance(v, bool))
    if bad:
        raise ConfigError(f"[gates] {bad} must be true or false")
    if "max_strain_kcal" in g:
        v = g["max_strain_kcal"]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            raise ConfigError("[gates] max_strain_kcal must be a positive number (kcal/mol)")
        if not g.get("forcefield"):
            raise ConfigError("[gates] max_strain_kcal needs forcefield = true: it thresholds the MMFF94 strain")


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
        electrons = sum(pt.GetAtomicNumber(s) for s in c.poses[0].symbols) - _net_charge(c.smiles)
        if electrons % 2:
            raise StageError(
                f"stage 'parity' failed: {c.name} has {electrons} electrons at charge {_net_charge(c.smiles):+d}; "
                "the field-interaction tier is closed-shell RHF and is undefined for an odd electron count",
                stage="parity",
            )


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
) -> tuple[RunRecord, dict]:
    """Run the measurement, pair, cut, and build the record (written by `write_outputs`).

    `measure(plan)`, when given, fills `per_pose[QUANTITY]` on every candidate in place of running
    `plan.tier` (the field-interaction SCFs); tests inject fixed numbers, so everything after the SCFs is checked
    without ferric.

    `skip_analyses`: analysis keys the caller computes itself (the Temporal runner summarizes the ones that
    re-measure). `pose_index`: after a pairwise pose drop, each candidate's kept ORIGINAL pose indices (its poses
    and per_pose hold only those); an analogue then pairs with the parent's values at its own kept indices, and
    the record carries `results.pose_index`.
    """
    _STAGE_SECONDS.set(plan.stage_seconds)  # this plan's seconds, whatever was built in this context since
    if measure is None:
        _stage(plan.tier.name, lambda: plan.tier.run(plan.candidates, {"field": plan.field}))
    else:
        _stage(plan.tier.name, lambda: measure(plan))
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
    if plan.structure_fetch is not None:  # a wall-clock time: in results, never in inputs (the digest)
        results["structure_fetch"] = dict(plan.structure_fetch)
    rec = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, results)
    if not partial:  # an analysis reads the whole measurement; a combine of the partials runs it
        for spec in active_hooks("analysis", plan.cfg):
            if spec.key in skip_analyses:
                continue
            _run_analysis(spec, plan, rec, {"measure": measure, "log": plan.stage_log})
    return rec, {"paired": paired, "unpaired": unpaired, "cut": res}


def _run_analysis(spec: MethodSpec, plan: Plan, rec: RunRecord, ctx: dict) -> None:
    """An `analysis` hook runs after the measurement; the dict it returns is merged into `results[spec.key]`."""
    hook = cast(Analysis, spec.hook)
    got = _stage(spec.key, lambda: hook.run(plan, rec, ctx))
    if not isinstance(got, dict):
        raise StageError(
            f"stage {spec.key!r} failed: an analysis returns a dict, got {type(got).__name__}", stage=spec.key
        )
    rec.results[spec.key] = {**(rec.results.get(spec.key) or {}), **got}


def pose_file_name(candidate: str) -> str:
    """The SDF file name for a candidate: every character outside [A-Za-z0-9._-] becomes '_'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", candidate) + ".sdf"


def _out_path(path: str | Path, out_dir: Path) -> str:
    """A path as the record stores it: relative to OUT when inside it, else absolute."""
    p = Path(path).resolve()
    try:
        return p.relative_to(out_dir.resolve()).as_posix()
    except ValueError:
        return str(p)


def _gates_in_out(gates: dict, out_dir: Path) -> dict:
    """A copy of the per-pose gate results with every `receptor` path made relative to OUT when inside it (the
    absolute path of a run directory that is later renamed or moved points at nothing)."""

    def walk(x):
        if isinstance(x, dict):
            return {
                k: _out_path(v, out_dir) if k == "receptor" and isinstance(v, str) and v else walk(v)
                for k, v in x.items()
            }
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(gates)


def pose_mol(c: Candidate) -> Chem.Mol:
    """The candidate as an RDKit mol with one conformer per pose: atoms and bonds from its SMILES with explicit
    H (`Chem.AddHs(Chem.MolFromSmiles(smiles))`), coordinates from `Pose.coords_ang`. A pose whose elements are
    not that atom order is a StageError: writing coordinates onto the wrong atoms would be silently wrong."""
    mol = Chem.AddHs(Chem.MolFromSmiles(c.smiles))
    symbols = tuple(a.GetSymbol() for a in mol.GetAtoms())
    for i, pose in enumerate(c.poses):
        if tuple(pose.symbols) != symbols:
            raise StageError(
                f"stage 'outputs' failed: {c.name} pose {i} atoms {''.join(pose.symbols)} are not its SMILES-with-H "
                f"order {''.join(symbols)}; refusing to write coordinates onto the wrong atoms",
                stage="outputs",
            )
        conf = Chem.Conformer(len(symbols))
        for k, xyz in enumerate(np.asarray(pose.coords_ang, dtype=float)):
            conf.SetAtomPosition(k, tuple(float(v) for v in xyz))
        conf.Set3D(True)
        mol.AddConformer(conf, assignId=True)
    return mol


def write_pose_sdfs(plan: Plan, results: dict, out_dir: Path) -> dict:
    """One SDF per candidate under OUT/poses/ (docs/gui-contract.md section 1); returns `results.poses`.

    Record i is pose i. Properties: smeltery_candidate, smeltery_pose_index, smeltery_parent ("1"/"0"), every
    `results.per_pose` quantity of the candidate and, on analogues, paired_ddE, all numbers as %.10g. A file-name
    collision after sanitising is a StageError, raised before anything is written.
    """
    names = {}
    for c in plan.candidates:
        f = pose_file_name(c.name)
        if f in names:
            raise StageError(
                f"stage 'outputs' failed: candidates {names[f]!r} and {c.name!r} both sanitise to pose file {f!r}; "
                "rename one",
                stage="outputs",
            )
        names[f] = c.name
    mols = {c.name: pose_mol(c) for c in plan.candidates}  # all checked before the first write
    pose_dir = out_dir / POSES_DIR
    pose_dir.mkdir(parents=True, exist_ok=True)
    poses = {}
    for c in plan.candidates:
        path = pose_dir / pose_file_name(c.name)
        values = results["per_pose"][c.name]
        paired = None
        if c is not plan.parent:
            paired_ddE = results.get("paired_ddE", {})
            if c.name in paired_ddE and isinstance(paired_ddE[c.name], dict):
                paired = paired_ddE[c.name].get("per_pose")
        mol = mols[c.name]
        index = (results.get("pose_index") or {}).get(c.name)  # original indices after a pairwise drop
        with Chem.SDWriter(str(path)) as w:
            for i, conf in enumerate(mol.GetConformers()):
                m = Chem.Mol(mol)
                m.RemoveAllConformers()
                m.AddConformer(Chem.Conformer(conf), assignId=True)
                m.SetProp("_Name", f"{c.name} pose {i}")
                m.SetProp("smeltery_candidate", c.name)
                m.SetProp("smeltery_pose_index", str(i if index is None else index[i]))
                m.SetProp("smeltery_parent", "1" if c is plan.parent else "0")
                for q, v in values.items():
                    m.SetProp(q, f"{v[i]:.10g}")
                if paired is not None:
                    m.SetProp("paired_ddE", f"{paired[i]:.10g}")
                w.write(m)
        poses[c.name] = {"file": f"{POSES_DIR}/{path.name}", "sha256": file_digest(path), "n_poses": len(c.poses)}
    return poses


def calc_file_name(candidate: str) -> str:
    """The calc log of a `--only-candidate` partial run: calcs-<candidate>.jsonl, sanitised like the pose SDFs."""
    return "calcs-" + pose_file_name(candidate)[: -len(".sdf")] + ".jsonl"


def merged_partial_calcs(out_dir: Path, partials: list[Path]) -> list[dict]:
    """Every SCF line of the partial runs being combined, read from the calc logs their records name."""
    rows: list[dict] = []
    for p in partials:
        calcs = (json.loads(p.read_text()).get("results") or {}).get("calcs")
        f = Path(out_dir) / calcs["file"] if calcs else None
        if f is not None and f.is_file():
            rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return rows


def write_outputs(
    plan: Plan,
    rec: RunRecord,
    out_dir: Path,
    calc_file: str = "calcs.jsonl",
    calc_rows: list[dict] | None = None,
) -> Path:
    """Write everything a run leaves under OUT and return the record's path: the pose SDFs (`results.poses`), the
    receptor and decisions paths made relative to OUT when inside it, then OUT/run-<digest12>.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = rec.results
    results["poses"] = write_pose_sdfs(plan, results, out_dir)
    if results.get("receptor") is not None:
        results["receptor"]["file"] = _out_path(results["receptor"]["file"], out_dir)
    if "decisions_in" in results:
        results["decisions_in"]["file"] = _out_path(results["decisions_in"]["file"], out_dir)
    if plan.gate_results:  # only the gates that ran
        results["gates"] = _gates_in_out(plan.gate_results, out_dir)
    if calc_rows is not None and not calc_rows:
        results["calcs"] = None  # a combine of partials with no calc logs: no SCF count is known, never "0"
    else:
        base = list(calc_rows) if calc_rows is not None else list(getattr(plan.tier, "calc_log", None) or [])
        rows = write_calc_log(plan, out_dir, calc_file, base + _analysis_calcs(plan, results))
        results["calcs"] = {
            "file": calc_file,
            "n": rows,
            "primary": primary_model(plan.field),
            "by_model": _count_models(out_dir / calc_file),
        }
    path = out_dir / f"run-{rec.input_digest[:12]}.json"
    path.write_text(rec.to_json())
    return path


def primary_model(field) -> str:
    """The name of the plan's own field model: the force field pdb2pqr charged it with, else "primary" (sites, a
    .pqr)."""
    prov = getattr(field, "provenance", None) or {}
    return str(prov.get("pdb2pqr_ff") or "primary")


def _tag(row: dict, model: str) -> dict:
    """An SCF line as calcs.jsonl holds it: every line names its field model and its rung (0 without a ladder)."""
    if "outcome" in row:  # a Temporal outcome line: already names its model
        return row
    return {**row, "field_model": row.get("field_model", model), "rung": row.get("rung", 0)}


def _analysis_calcs(plan: Plan, results: dict) -> list[dict]:
    """Move every active analysis's per-model SCF lines (`results[key][model]["calcs"]`) out of the record, which
    then keeps `{"n": count}`: calcs.jsonl is the one per-SCF log."""
    rows: list[dict] = []
    for spec in active_hooks("analysis", plan.cfg):
        val = results.get(spec.key)
        if not isinstance(val, dict):
            continue
        for model, entry in val.items():
            if isinstance(entry, dict) and isinstance(entry.get("calcs"), list):
                rows += [_tag(r, str(model)) for r in entry["calcs"]]
                entry["calcs"] = {"n": len(entry["calcs"])}
    return rows


def _count_models(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in path.read_text().splitlines():
        if x.strip():
            r = json.loads(x)
            if "outcome" not in r:  # a --temporal vacuum leg has no model: it is shared by every model
                k = "shared_vacuum" if r.get("field_model") is None else str(r["field_model"])
                out[k] = out.get(k, 0) + 1
    return out


def write_calc_log(plan: Plan, out_dir: Path, name: str = "calcs.jsonl", rows: list[dict] | None = None) -> int:
    """Write OUT/<name> (one line per SCF: `rows`, else the ones the tier ran) and return how many lines it has.
    A line without a field model is the primary field's."""
    primary = primary_model(plan.field)
    rows = list(rows) if rows is not None else list(getattr(plan.tier, "calc_log", None) or [])
    calc_log = [_tag(r, primary) for r in rows]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (name + ".tmp")  # written whole, then renamed: a crash never leaves a half-written log
    tmp.write_text("".join(json.dumps(e) + "\n" for e in calc_log))
    os.replace(tmp, out_dir / name)
    return len(calc_log)


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
    return {
        "tier": tier.name,
        "estimate": mem,
        "free_gb": available_memory_gb(),
        "require_free_gb": getattr(tier, "require_free_gb", None),
        "check_memory": bool(getattr(tier, "check_memory", True)),
        "verdict": verdict,
        "basis": basis,
    }


def memory_cannot_say(tier, basis: str) -> dict:
    """`memory_estimate`'s shape for when there are no poses to size (or no tier): the same keys, `estimate` None."""
    return {
        "tier": getattr(tier, "name", None),
        "estimate": None,
        "free_gb": available_memory_gb(),
        "require_free_gb": getattr(tier, "require_free_gb", None),
        "check_memory": bool(getattr(tier, "check_memory", True)),
        "verdict": "cannot say",
        "basis": basis,
    }


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


def pose_sections() -> tuple[str, ...]:
    """The config sections that decide the poses; an edit outside them leaves every pose where it was: the sections
    of the methods on the input and geometry rungs (`smeltery.methods.registry()`), and the pipeline's own
    candidates, parent and decisions."""
    own = tuple(s.section for s in registry().values() if s.section is not None and s.rung in ("input", "geometry"))
    return (*own, "candidates", "parent", "decisions")


def poses_key(cfg: dict, base: Path) -> str:
    """A hash of everything upstream of the poses in a raw config (`tomllib` output). A file the config names is
    hashed by content, so a run's staged copy (`inputs/<sha>/name`) and the original path give the same key."""
    from .run_inputs import input_paths

    parts: dict = {}
    for sec in pose_sections():
        if sec in cfg:
            parts[sec] = json.loads(json.dumps(cfg[sec], sort_keys=True, default=str))
    for table, key in input_paths():
        v = parts.get(table, {}).get(key) if isinstance(parts.get(table), dict) else None
        if isinstance(v, str):
            f = Path(v) if Path(v).is_absolute() else Path(base) / v
            if f.is_file():
                parts[table][key] = {"sha256": hashlib.sha256(f.read_bytes()).hexdigest()}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def write_plan_preview(plan: Plan, digest: str, out_dir: Path, config: Path) -> None:
    """`--plan --out OUT`: OUT/plan.json (`digest`, `seconds` = the measurement tier's `estimate_cost`, `memory`,
    `poses_key`, `poses`) and the pose SDFs under OUT/poses/. No SCF ran; Bench's impact reads these."""
    poses = write_pose_sdfs(plan, {"per_pose": {c.name: {} for c in plan.candidates}}, out_dir)
    raw = tomllib.loads(Path(config).read_text())  # the config as written: before decisions drop candidates
    rec = {
        "digest": digest,
        "seconds": plan.tier.estimate_cost(plan.candidates),
        "memory": memory_estimate(plan),
        "poses_key": poses_key(raw, Path(config).resolve().parent),
        "poses": poses,
    }
    rec = {"status": "ok", **rec}
    (out_dir / "plan.json").write_text(json.dumps(rec, indent=2, sort_keys=True, default=str) + "\n")


def write_plan_error(out_dir: Path, message: str, stage: str | None = None) -> None:
    """`--plan --out OUT` that failed: OUT/plan.json says so (`status` "error", the failing `stage` or null, the
    `message`) instead of leaving the last good preview or nothing. `input_digest` is always null: a failure leaves
    no finished plan to digest (the key is there so readers of an ok plan.json find the same keys)."""
    rec = {"status": "error", "stage": stage, "message": message, "input_digest": None}
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "plan.json").write_text(json.dumps(rec, indent=2, sort_keys=True) + "\n")
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
    "bench": "smeltery.bench.app",
    "verify-run": "smeltery.run_tools:verify_run",
    "inspect": "smeltery.run_tools:inspect_run",
    "diff-run": "smeltery.run_tools:diff_run",
}


def _stage_config(config: Path, out_dir: Path, temporal: bool = False) -> tuple[Path, dict]:
    """Write OUT/config.toml with every input file copied under OUT/inputs/, and return its path and the record's
    `results.config` entry; the run then reads only from OUT. Refused (ConfigError) while a Temporal run is live in
    OUT, unless this is `--temporal` with the same staged config (it attaches)."""
    from .run_inputs import stage_inputs

    config = Path(config)
    try:
        text = config.read_text()
    except OSError as e:
        raise ConfigError(f"cannot read {config}: {e}") from e
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"cannot read {config}: {e}") from e
    out_dir = Path(out_dir)
    # the guard first: nothing is written into an OUT whose Temporal run is live
    _guard_out(out_dir, lambda: staged_sha256(text, config.resolve().parent), temporal)
    out_dir.mkdir(parents=True, exist_ok=True)
    staged, inputs = stage_inputs(text, config.resolve().parent, out_dir)
    dest = out_dir / "config.toml"
    if not dest.is_file() or dest.read_text() != staged:  # an unchanged config is not rewritten (a live run reads it)
        dest.write_text(staged)
    return dest, {"file": "config.toml", "sha256": hashlib.sha256(staged.encode()).hexdigest(), "inputs": inputs}


def staged_sha256(text: str, config_dir: Path) -> str:
    """The sha256 of the config `stage_inputs` would write, computed by staging into a scratch directory: the staged
    text names each input by content (`inputs/<sha>/<name>`), not by OUT, so it is the same text OUT would get."""
    import tempfile

    from .run_inputs import stage_inputs

    with tempfile.TemporaryDirectory(prefix="smeltery-stage-") as tmp:
        staged, _ = stage_inputs(text, Path(config_dir), Path(tmp))
    return hashlib.sha256(staged.encode()).hexdigest()


def _guard_out(out_dir: Path, staged_sha, temporal: bool = False) -> None:
    from .temporal_run import guard_out

    why = guard_out(Path(out_dir), staged_sha, temporal)
    if why:
        raise ConfigError(why)


def partial_digest(plan: Plan, parent: str, name: str) -> str:
    """The input digest `--only-candidate name` records for this config: the plan's inputs with only the parent and
    `name` as candidates (a partial plan is built from exactly those), and the same tiers."""
    from .record import digest

    keep = {k: v for k, v in plan.inputs["candidates"].items() if k in (parent, name)}
    return digest({"inputs": {**plan.inputs, "candidates": keep}, "tiers": plan.tiers})


def _check_partials(plan: Plan, parent: str, recs: dict[Path, dict]) -> None:
    """A combine joins partials of THIS config only: each partial's input digest is the one this plan gives its
    candidate, and every partial staged the same config (results.config sha256). Else ConfigError."""
    for p, r in recs.items():
        name = p.name[len("partial-") : -len(".json")]
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
    sub.add_parser("bench", help="Bench web app (smeltery bench ROOT --help)")
    sub.add_parser("verify-run", help="check a run record's self-hash and file hashes (accidental edits only)")
    sub.add_parser("inspect", help="plain-text summary of a run record")
    sub.add_parser("diff-run", help="compare two runs' inputs and tiers")
    tw = sub.add_parser("task-worker", help="run a Temporal worker for smeltery tasks")
    tw.add_argument("--host", default="127.0.0.1:7233")
    tw.add_argument("--namespace", default="default")
    tw.add_argument("--task-queue", default="smeltery-tasks")
    tw.add_argument("--concurrency", type=int, default=2, help="max concurrent activities")
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
    run.add_argument("--force", action="store_true", help="run into an OUT that already holds a run record")
    run.add_argument("--combine", action="store_true", help="combine partial results into the full record")
    run.add_argument(
        "--temporal",
        action="store_true",
        help="run as a durable Temporal workflow (needs --out, a server and `smeltery temporal-worker`)",
    )
    run.add_argument("--workers", type=int, default=2, help="with --temporal: scores run at once")
    run.add_argument("--host", default="127.0.0.1:7233", help="with --temporal: the Temporal server")
    run.add_argument("--task-queue", dest="task_queue", default="smeltery-pipeline", help="with --temporal")
    me = sub.add_parser("methods", help="every method's config section, knobs and defaults, as TOML")
    me.add_argument("--out", help="write one <key>.toml per method plus index.toml into this directory")
    me.add_argument("--only", help="one method key, e.g. field_interaction")
    pw = sub.add_parser("temporal-worker", help="run a Temporal worker for `smeltery run --temporal`")
    pw.add_argument("--workers", type=int, default=2, help="SCFs at once, one child process each (memory-bound)")
    pw.add_argument(
        "--threads-per-worker", dest="threads_per_worker", type=int, default=1, help="RAYON_NUM_THREADS per SCF"
    )
    pw.add_argument("--host", default="127.0.0.1:7233")
    pw.add_argument("--task-queue", dest="task_queue", default="smeltery-pipeline")
    args = ap.parse_args(argv)
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
        from smeltery.tasks import TEMPORAL_AVAILABLE, worker_activities, worker_workflows

        if not TEMPORAL_AVAILABLE:
            print("temporalio is not installed", file=sys.stderr)
            return 2

        async def _run_worker():
            Client = __import__("temporalio.client", fromlist=["Client"]).Client  # noqa: E501
            UnsandboxedWorkflowRunner = __import__(
                "temporalio.worker", fromlist=["UnsandboxedWorkflowRunner"]
            ).UnsandboxedWorkflowRunner  # noqa: E501
            Worker = __import__("temporalio.worker", fromlist=["Worker"]).Worker

            client = await Client.connect(args.host, namespace=args.namespace)
            async with Worker(
                client,
                task_queue=args.task_queue,
                workflows=worker_workflows(),
                activities=worker_activities(),
                workflow_runner=UnsandboxedWorkflowRunner(),
                max_concurrent_activities=args.concurrency,
            ):
                print(f"smeltery task worker up on {args.task_queue} (Ctrl+C stops)", flush=True)
                await asyncio.Future()

        import asyncio

        try:
            asyncio.run(_run_worker())
        except KeyboardInterrupt:
            return 0
        return 0
    if args.command == "temporal-worker":
        from .temporal_run import worker_cli

        return worker_cli(args.host, args.task_queue, args.workers, args.threads_per_worker)
    explicit_out = args.out is not None
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

            return run_cli(args.config, args.out, args.workers, args.host, args.task_queue, config_info)
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
                print(_describe_plan(plan, RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest))
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
            new_path = p.parent / f"partial-{args.only_candidate}.json"
            data = json.loads(p.read_text())
            data.get("results", {}).pop("cut", None)
            data.get("results", {}).pop("paired_ddE", None)
            if "record_sha256" in data:  # the pops changed the record: restamp it, or verify-run reports an edit
                from .record import _canonical_sha256

                data["record_sha256"] = _canonical_sha256(data)
            new_path.write_text(json.dumps(data, indent=2, sort_keys=True, default=str))
            try:
                p.unlink()
            except OSError:
                pass
            print(f"\npartial record: {new_path}\ninput_digest {rec.input_digest}")
            return 0
        if args.combine:
            out_dir = Path(args.out)
            partials = sorted(out_dir.glob("partial-*.json"))
            names = {p.name[len("partial-") : -len(".json")] for p in partials}
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
                name = p.name[len("partial-") : -len(".json")]
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
            print(format_table(plan, out))
            print(f"\nrun record: {path}\ninput_digest {rec.input_digest}")
            return 4 if args.fail_on_unranked and out["cut"].unranked_at_boundary else 0
        plan = build_plan(cfg, out_dir=args.out)
        if args.plan:
            digest = RunRecord(plan.cfg["campaign"], plan.inputs, plan.tiers, {}).input_digest
            print(_describe_plan(plan, digest))
            if explicit_out:
                write_plan_preview(plan, digest, args.out, args.config)
            return 0
        rec, out = run_plan(plan)
        if config_info is not None:
            rec.results["config"] = config_info
        path = _stage("outputs", lambda: write_outputs(plan, rec, args.out))
    except ConfigError as e:
        print(f"config error: {e}", file=sys.stderr)
        if explicit_out and args.plan:
            write_plan_error(args.out, str(e))
        return 2
    except StageError as e:
        print(f"error: {e}", file=sys.stderr)
        if explicit_out and args.plan:
            write_plan_error(args.out, str(e), getattr(e, "stage", None))
        if explicit_out and e.gates:  # a rejected gate still leaves every pose's result
            try:
                args.out.mkdir(parents=True, exist_ok=True)
                (args.out / "gates.json").write_text(
                    json.dumps(_gates_in_out(e.gates, args.out), indent=2, sort_keys=True) + "\n"
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
    return 4 if args.fail_on_unranked and out["cut"].unranked_at_boundary else 0


if __name__ == "__main__":
    sys.exit(main())
