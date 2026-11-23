"""The `smeltery run` config: reading it, and every rule it must satisfy before anything is planned.

`load_config` reads a TOML file and `validate_config` checks it (as `tomllib` parses it), raising `ConfigError` naming
the first problem. The top-level keys and each method section's rules come from the method registry
(`smeltery.methods`): a registration's own `validate`, or the generic section check (`_validate_section`) for one that
declares only its knobs. The builtin methods' validators are the `_validate_*` functions here.

`smeltery.cli` re-exports every name, so `cli.ConfigError`, `cli.load_config` and the derived key sets
(`cli.TOP_KEYS`, ...) are these objects. `StageError` (exit 3) lives here beside `ConfigError` (exit 2) so that code
below the CLI can raise and catch both without importing it.
"""

from __future__ import annotations

import dataclasses
import tomllib
from pathlib import Path

from rdkit import Chem

from .methods import MethodSpec, active_hooks, by_slot, registry
from .tiers import FieldDftInteraction, FieldInteraction, check_remedies

#: Top-level keys that are inputs of the pipeline, not a method's section (the Ligands step, and the gate switches).
OWN_TOP_KEYS = {"campaign", "parent", "candidates", "gates", "decisions", "run"}
#: `[run]`: how the run executes, not what it measures (not an input: it never moves the digest).
RUN_KEYS = {"scf_timeout_s", "allow_mixed_builds"}
#: `[run] scf_timeout_s` default: one SCF leg (vacuum or field, one rung) is ended after this long under `--temporal`.
SCF_TIMEOUT_S = 14400
DECISIONS_KEYS = {"file"}
SITE_KEYS = {"q", "atom", "from_atom", "distance"}


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
}


def __getattr__(name: str):
    if name in _DERIVED:
        return _DERIVED[name]()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


class ConfigError(ValueError):
    """The config is malformed or names something that does not exist. Exit code 2."""


class OutGuardRefused(ConfigError):
    """The live-run guard refused OUT: nothing may be written there, not even an error plan.json."""


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
