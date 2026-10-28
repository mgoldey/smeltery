"""The method hierarchy as data a GUI can draw: every stage `smeltery run` knows, and every tier, by rung.

A GUI builds a pipeline out of METHODS. Each `Method` says where it sits in the hierarchy (its `rung`, from
inputs through geometry and empirical models to ab-initio and embedded QM), what it does to the candidate list
(its `role`, the same kinds `smeltery run` logs per stage), which artefacts it NEEDS from earlier stages and
PROVIDES to later ones, and the knobs it takes, typed so a form can be generated from them.

Nothing here invents a capability. A tier `smeltery run` cannot run yet (`surface_esp`, `qmmm`, `rescoring`,
and `docking` used as a ranker) is in the catalog so the hierarchy is complete, with `wired=False` and the
reason; `smeltery.gui.pipeline` refuses to place it. Knob names are the config's own keys, and
`tests/test_gui_catalog.py` fails if a section's knobs drift from what `smeltery.cli` accepts.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field

#: Rungs of the method hierarchy, cheapest and least physical first. A pipeline climbs them; it never descends
#: to rank on a cheaper method than one it already ran, because nothing cheaper is wired as a ranker.
RUNGS: tuple[tuple[str, str], ...] = (
    ("input", "Where the target, receptor and field come from"),
    ("geometry", "Poses: searched (docking) or built (ETKDG, paired to the parent)"),
    ("check", "Hard refusals on what the next rung needs (electron parity, pose validity)"),
    ("empirical", "Force fields and empirical scores"),
    ("semiempirical", "Tight-binding QM (GFN2-xTB)"),
    ("ab_initio", "ferric wavefunction and density-functional methods in the pocket field"),
    ("embedded_qm", "QM/MM with a covalent QM-MM boundary"),
    # Not a step up the rigour ladder: a different question. The rungs above ask how strongly a pose
    # interacts; this one asks whether a chemist would make the molecule at all. It is placed after them
    # because an interaction energy is what makes the question worth asking, and before `decision`
    # because the answer belongs in front of whoever reads the cut.
    ("designable", "Would a chemist make it? Properties and synthesizability, from the SMILES alone"),
    ("decision", "The paired cut: ties and UNRANKED are answers"),
)
RUNG_INDEX = {name: i for i, (name, _) in enumerate(RUNGS)}

#: What a stage does to the candidate list. Matches the `kind` of `smeltery.cli` stage-log entries, plus
#: `measure` (adds a quantity, removes no one) and `rank` (the one stage that removes candidates).
ROLES = ("prep", "search", "pair", "field", "gate", "recorded", "measure", "rank")

#: Artefacts passed between stages. A stage may run only once everything it needs has been provided upstream.
ARTEFACTS = {
    "receptor": "a prepared receptor (PDBQT) and a box centre in its frame",
    "field": "the pocket's point charges, in the poses' frame",
    "parent_poses": "the parent's poses, searched rather than built",
    "poses": "a paired pose ensemble for every candidate",
    "dE_int": "per-pose field interaction energy, kcal/mol",
    "survivors": "the analogues the cut keeps, or UNRANKED",
}

#: Param kinds a form maps to a widget. `vec3` is three numbers (Angstrom); `sites` is a list of point-charge
#: tables (`SITE_FIELDS`); `path` is a file path, relative to the config's directory.
KINDS = ("int", "float", "bool", "str", "path", "choice", "vec3", "int_list", "str_list", "sites")
SITE_FIELDS = (("q", "float"), ("atom", "int"), ("from_atom", "int"), ("distance", "float"))


@dataclass(frozen=True)
class Param:
    """One knob: its config key, widget kind, default (None = no default) and whether it must be set."""

    name: str
    kind: str
    default: object = None
    required: bool = False
    help: str = ""
    choices: tuple = ()
    optional: bool = False  # the value may be left unset (None) even though it is not required

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"param {self.name!r}: unknown kind {self.kind!r}; one of {KINDS}")
        if (self.kind == "choice") != bool(self.choices):
            raise ValueError(f"param {self.name!r}: choices go with kind 'choice' and only with it")

    def check(self, value) -> str | None:
        """None if `value` is acceptable for this kind, else why not. Semantics are the config validator's job."""
        if value is None:
            return f"{self.name} is required" if self.required else None
        num = (int, float)
        ok = {
            "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "float": lambda v: isinstance(v, num) and not isinstance(v, bool),
            "bool": lambda v: isinstance(v, bool),
            "str": lambda v: isinstance(v, str),
            "path": lambda v: isinstance(v, str) and bool(v),
            "choice": lambda v: v in self.choices,
            "vec3": lambda v: (
                isinstance(v, (list, tuple))
                and len(v) == 3
                and all(isinstance(x, num) and not isinstance(x, bool) for x in v)
            ),
            "int_list": lambda v: (
                isinstance(v, (list, tuple))
                and bool(v)
                and all(isinstance(x, int) and not isinstance(x, bool) for x in v)
            ),
            "str_list": lambda v: isinstance(v, (list, tuple)) and bool(v) and all(isinstance(x, str) for x in v),
            "sites": _sites_ok,
        }[self.kind]
        if ok(value):
            return None
        want = (
            f"one of {list(self.choices)}"
            if self.kind == "choice"
            else f"a{'n' * (self.kind[0] in 'aeiou')} {self.kind}"
        )
        return f"{self.name} must be {want}, got {value!r}"

    def as_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["choices"] = list(self.choices)
        return d


def _sites_ok(v) -> bool:
    if not isinstance(v, (list, tuple)) or not v:
        return False
    for s in v:
        if not isinstance(s, dict) or set(s) != {k for k, _ in SITE_FIELDS}:
            return False
        for k, kind in SITE_FIELDS:
            x = s[k]
            if isinstance(x, bool) or not isinstance(x, int if kind == "int" else (int, float)):
                return False
    return True


@dataclass(frozen=True)
class Method:
    """One stage a pipeline can hold.

    `section` is the config table its knobs live in (None: no knobs of its own); `gate_key` is its switch under
    `[gates]` for the gates that are switched on there. `tier` is the registered tier name behind it, if any.
    `implicit` stages always run in `smeltery run` (they are shown, not placed); `exclusive` names methods that
    cannot share a pipeline with this one.
    """

    key: str
    label: str
    rung: str
    role: str
    needs: tuple[str, ...] = ()
    provides: tuple[str, ...] = ()
    params: tuple[Param, ...] = ()
    section: str | None = None
    gate_key: str | None = None
    tier: str | None = None
    required: bool = False
    implicit: bool = False
    wired: bool = True
    why_unwired: str = ""
    extras: tuple[str, ...] = ()
    exclusive: tuple[str, ...] = ()
    summary: str = ""

    def __post_init__(self) -> None:
        if self.rung not in RUNG_INDEX:
            raise ValueError(f"method {self.key!r}: unknown rung {self.rung!r}")
        if self.role not in ROLES:
            raise ValueError(f"method {self.key!r}: unknown role {self.role!r}")
        unknown = sorted(set(self.needs + self.provides) - set(ARTEFACTS))
        if unknown:
            raise ValueError(f"method {self.key!r}: unknown artefact(s) {unknown}")
        if self.wired == bool(self.why_unwired):
            raise ValueError(f"method {self.key!r}: an unwired method states why, a wired one does not")

    @property
    def rung_index(self) -> int:
        return RUNG_INDEX[self.rung]

    def param(self, name: str) -> Param:
        for p in self.params:
            if p.name == name:
                return p
        raise KeyError(f"method {self.key!r} has no param {name!r}; params: {[p.name for p in self.params]}")

    def defaults(self) -> dict:
        """The knobs with a default, at that default. Knobs without one are left out, never guessed."""
        return {p.name: p.default for p in self.params if p.default is not None}

    def as_dict(self) -> dict:
        """JSON-ready, for a GUI that is not written in Python."""
        d = {f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != "params"}
        d.update({k: list(d[k]) for k in ("needs", "provides", "extras", "exclusive")})
        d["params"] = [p.as_dict() for p in self.params]
        d["rung_index"] = self.rung_index
        d.update(_tier_facts(self.tier))
        return d


# Knobs a tier dataclass has that a config must not set: state the tier fills in itself, values the pipeline
# decides (`parent_poses_given` follows from docking), or inputs a TOML file cannot carry.
_NOT_KNOBS = {
    "name",
    "field_provenance",
    "scaffold_maps",
    "unvalidated_cores",
    "parent_poses_given",
    "analogue_poses_given",
    "known_cores",
}
_TYPE_KIND = {"int": "int", "float": "float", "bool": "bool", "str": "str"}


def params_from_dataclass(cls: type, helps: dict[str, str] | None = None) -> tuple[Param, ...]:
    """Typed params for a tier dataclass's scalar knobs, at the class's own defaults (so they cannot drift)."""
    out = []
    for f in dataclasses.fields(cls):
        if not f.init or f.name in _NOT_KNOBS:
            continue
        t = f.type if isinstance(f.type, str) else getattr(f.type, "__name__", str(f.type))
        base, _, rest = t.partition(" | ")
        if base not in _TYPE_KIND or rest not in ("", "None"):
            raise TypeError(f"{cls.__name__}.{f.name}: type {t!r} has no form widget; add it to _NOT_KNOBS or KINDS")
        default = None if f.default is dataclasses.MISSING else f.default
        out.append(
            Param(f.name, _TYPE_KIND[base], default, optional=rest == "None", help=(helps or {}).get(f.name, ""))
        )
    return tuple(out)


def _tier_facts(tier: str | None) -> dict:
    """What the tier itself declares, read from the code: produced quantities, units, floors, is_delta_g.

    A tier that cannot be built without inputs (docking needs a provider, qmmm a partition) reports only what its
    class states. Floors are 'measured' / 'UNMEASURED' exactly as `docs/tiers/index.md` derives them.
    """
    if tier is None:
        return {"produces": {}, "floors": {}, "is_delta_g": None}
    from ..tier_registry import registered_tiers

    cls = registered_tiers()[tier]
    flag = getattr(cls, "is_delta_g", True)  # a property on some tiers: then only an instance can say
    facts: dict = {"produces": {}, "floors": {}, "is_delta_g": flag if isinstance(flag, bool) else None}
    try:
        inst = cls()
    except Exception:  # needs constructor inputs (or an optional backend); the class attributes are all we know
        return facts
    facts["produces"] = dict(inst.produces())
    facts["floors"] = {q: inst.systematic_floor(q) for q in facts["produces"]}
    return facts


def floor_status(method: Method) -> str:
    """'n/a' (no tier, or no quantity), 'measured' (some quantity has a floor), 'UNMEASURED', or 'unknown'."""
    if method.tier is None:
        return "n/a"
    facts = _tier_facts(method.tier)
    if not facts["produces"]:
        return "unknown" if method.tier in ("docking", "rescoring", "qmmm") else "n/a"
    return "measured" if any(v is not None for v in facts["floors"].values()) else "UNMEASURED"


def _build() -> dict[str, Method]:
    from ..tiers import FieldInteraction, PairedPoses

    methods = [
        Method(
            "structure",
            "Target structure",
            "input",
            "prep",
            provides=("receptor", "field"),
            section="structure",
            exclusive=("pocket",),
            extras=("docking",),
            summary="A .pdb, PDB id or AFDB accession, prepared into a receptor and a point-charge field.",
            params=(
                Param("spec", "str", required=True, help="a .pdb path, a PDB id or (afdb) a UniProt accession"),
                Param("provider", "choice", "pdb", choices=("pdb", "afdb"), help="afdb is PREDICTED: recorded"),
                Param("allow_network", "bool", False, help="fetch an id or accession; a local file never is"),
                Param("center", "vec3", optional=True, help="box centre, Angstrom; or reference_ligand"),
                Param("reference_ligand", "str", optional=True, help="HETATM RES[:CHAIN[:RESNUM]]; or center"),
                Param("pocket_cutoff", "float", required=True, help="Angstrom; no default: truncation is not monotone"),
                Param("chains", "str_list", optional=True, help="protein chains to keep (default all)"),
            ),
        ),
        Method(
            "pocket",
            "Pocket field",
            "input",
            "field",
            needs=("poses",),
            provides=("field",),
            section="pocket",
            exclusive=("structure",),
            summary="Point charges: a .pdb/.pqr file in the receptor's frame, or sites placed on the parent's pose 0.",
            params=(
                Param("file", "path", optional=True, help="a .pdb (via pdb2pqr30) or .pqr; or site"),
                Param("site", "sites", optional=True, help="charges `distance` A beyond parent atom `atom`"),
            ),
        ),
        Method(
            "docking",
            "Dock the parent (Vina)",
            "geometry",
            "search",
            needs=("receptor",),
            provides=("parent_poses",),
            section="docking",
            tier="docking",
            extras=("docking",),
            summary="Vina search for the PARENT only; analogues are paired to its poses, never docked.",
            params=(
                Param("receptor", "path", optional=True, help="prepared .pdbqt; comes from [structure] if present"),
                Param("box_center", "vec3", optional=True, help="A, receptor frame; from [structure] if present"),
                Param("box_size", "vec3", (24.0, 24.0, 24.0), help="Angstrom"),
                Param("seeds", "int_list", help="independent ETKDG embeddings; the budget lever", optional=True),
                Param("exhaustiveness", "int", optional=True),
                Param("n_poses", "int", optional=True),
            ),
        ),
        Method(
            "paired_poses",
            "Paired poses",
            "geometry",
            "pair",
            provides=("poses",),
            section="poses",
            tier="paired_poses",
            required=True,
            summary="Analogue pose i copies parent pose i's common core exactly, so pose noise cancels in ddE.",
            params=params_from_dataclass(
                PairedPoses,
                {
                    "n_poses": "ETKDG poses of the parent; not used when the parent is docked",
                    "relax_unmapped": "MMFF-relax only the substituent; needed to pass PoseBusters",
                },
            ),
        ),
        Method(
            "parity",
            "Electron parity",
            "check",
            "gate",
            needs=("poses",),
            implicit=True,
            summary="Refuses an odd electron count: the field-interaction tier is closed-shell RHF.",
        ),
        Method(
            "posebusters",
            "PoseBusters",
            "check",
            "gate",
            needs=("poses",),
            gate_key="posebusters",
            extras=("posebusters",),
            summary="Every pose must pass; a failure stops the run, it never drops a candidate silently.",
        ),
        Method(
            "forcefield",
            "MMFF94 energy and strain",
            "empirical",
            "recorded",
            needs=("poses",),
            gate_key="forcefield",
            tier="forcefield",
            summary="Recorded per pose; a gate only when max_strain_kcal is set.",
            params=(Param("max_strain_kcal", "float", optional=True, help="kcal/mol; reject any pose above it"),),
        ),
        Method(
            "rescoring",
            "Rescoring",
            "empirical",
            "measure",
            needs=("poses",),
            tier="rescoring",
            wired=False,
            why_unwired="rank-only scores (is_delta_g=False) cannot be differenced; smeltery run has no stage for it",
        ),
        Method(
            "gfn2",
            "GFN2-xTB in the field",
            "semiempirical",
            "recorded",
            needs=("poses", "field"),
            gate_key="xtb",
            tier="gfn2",
            summary="A gate on ionization state and gross failure, never a ranker (xtb-vs-DFT Spearman 0.011).",
        ),
        Method(
            "field_interaction",
            "Field interaction (ferric RHF)",
            "ab_initio",
            "measure",
            needs=("poses", "field"),
            provides=("dE_int",),
            section="field_interaction",
            tier="field_interaction",
            required=True,
            summary="dE_int = E(in field) - E(vacuum) per pose: a difference, so it compares across formulas.",
            params=params_from_dataclass(FieldInteraction, {"basis": "ferric basis set name"}),
        ),
        Method(
            "field_dft_interaction",
            "Field interaction (ferric PBE + D3)",
            "ab_initio",
            "measure",
            needs=("poses", "field"),
            provides=("dE_int",),
            tier="field_dft_interaction",
            wired=False,
            why_unwired="a library tier; smeltery run's [field_interaction] section selects RHF, with no key for a functional",
            summary="The same dE_int as field_interaction, from KS-DFT instead of RHF. D3(BJ) is reported separately and cancels in dE_int at fixed geometry.",
            exclusive=("field_interaction",),
        ),
        Method(
            "surface_esp",
            "Surface ESP",
            "ab_initio",
            "measure",
            needs=("poses",),
            tier="surface_esp",
            wired=False,
            why_unwired="a library tier; smeltery run has no stage for it",
        ),
        Method(
            "qmmm",
            "QM/MM single point",
            "embedded_qm",
            "measure",
            needs=("poses",),
            tier="qmmm",
            wired=False,
            why_unwired="needs MM parameters and a QM partition of a whole-structure pose; not in smeltery run",
        ),
        Method(
            "designability",
            "Designability (property drift, SA)",
            "designable",
            "measure",
            # Descriptors of the SMILES, so it needs no pose and no field: the parent comes from the run itself.
            tier="designability",
            wired=False,
            why_unwired="a library tier; smeltery run has no stage for it",
            summary="Drift from the parent in MW, logP, TPSA, QED and synthetic accessibility. A counterweight to dE_int, which rewards added atoms; it gates nothing.",
        ),
        Method(
            "cut",
            "Paired cut",
            "decision",
            "rank",
            needs=("dE_int",),
            provides=("survivors",),
            section="cut",
            required=True,
            summary="Ranks paired ddE against the parent; never orders what the uncertainty cannot separate.",
            params=(
                Param("keep", "int", required=True, help="analogues to keep (1..number of analogues)"),
                Param("z", "float", 2.0, help="resolution in standard errors"),
                Param("floor", "float", optional=True, help="kcal/mol; required while the tier's floor is unmeasured"),
            ),
        ),
    ]
    return {m.key: m for m in methods}


_CATALOG: dict[str, Method] | None = None


def catalog() -> dict[str, Method]:
    """{key: Method}, in hierarchy order (by rung, then the order `smeltery run` executes them)."""
    global _CATALOG
    if _CATALOG is None:
        _CATALOG = _build()
    return dict(_CATALOG)


def method(key: str) -> Method:
    try:
        return catalog()[key]
    except KeyError:
        raise KeyError(f"no method {key!r}; methods: {sorted(catalog())}") from None


@dataclass(frozen=True)
class Rung:
    name: str
    description: str
    methods: tuple[Method, ...] = field(default_factory=tuple)


def hierarchy(include_unwired: bool = True) -> list[Rung]:
    """The methods grouped by rung, cheapest first: the ladder a GUI draws as its palette."""
    ms = [m for m in catalog().values() if include_unwired or m.wired]
    return [Rung(name, desc, tuple(m for m in ms if m.rung == name)) for name, desc in RUNGS]


def catalog_json(include_unwired: bool = True) -> dict:
    """Everything a non-Python GUI needs to draw the palette and build forms, as plain JSON types."""
    return {
        "rungs": [
            {"name": r.name, "description": r.description, "methods": [m.key for m in r.methods]}
            for r in hierarchy(include_unwired)
        ],
        "roles": list(ROLES),
        "artefacts": dict(ARTEFACTS),
        "kinds": list(KINDS),
        "site_fields": [{"name": k, "kind": v} for k, v in SITE_FIELDS],
        "methods": {
            k: {**m.as_dict(), "floor_status": floor_status(m)}
            for k, m in catalog().items()
            if include_unwired or m.wired
        },
    }
