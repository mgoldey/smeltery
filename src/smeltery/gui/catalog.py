"""The method hierarchy as data a GUI can draw: every stage `smeltery run` knows, and every tier, by rung.

A GUI builds a pipeline out of METHODS. Each `Method` says where it sits in the hierarchy (its `rung`, from
inputs through geometry and empirical models to ab-initio and embedded QM), what it does to the candidate list
(its `role`, the same kinds `smeltery run` logs per stage), which artefacts it NEEDS from earlier stages and
PROVIDES to later ones, and the knobs it takes, typed so a form can be generated from them.

Nothing here invents a capability. A tier `smeltery run` cannot run yet (`surface_esp`, `qmmm`, `rescoring`,
and `docking` used as a ranker) is in the catalog so the hierarchy is complete, with `wired=False` and the
reason; `smeltery.gui.pipeline` refuses to place it. Knob names are the config's own keys.

No method is listed here: `catalog()` is one `Method` per registration in `smeltery.methods.registry()` (today's
methods are `smeltery.methods.builtin`), and the CLI validates each section against the same registration.
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
    ("designable", "Would a chemist make it? Properties and synthesizability, from the SMILES alone"),
    ("decision", "The paired cut: ties and UNRANKED are answers"),
)
RUNG_INDEX = {name: i for i, (name, _) in enumerate(RUNGS)}

# ---------------------------------------------------------------------------- what a rung was conflating
#
# `rung` carries two orderings that are not the same thing, and the seam shows wherever a method is
# not an interaction energy. PHASE is when a stage runs: a real total order over the pipeline, and
# `input` is not "less rigorous" than `geometry`. RIGOUR orders methods by how good the physics is,
# and it is only meaningful BETWEEN METHODS COMPUTING THE SAME QUANTITY -- GFN2 is a cheaper estimate
# of the same dE_int as PBE, while a synthesizability heuristic is not a cheaper anything. Ranking
# those two against each other is a category error, and before this decomposition the only thing
# preventing it was a four-line comment apologising for where `designable` sat in the tuple.
#
# This matters beyond tidiness: the funnel's goal is candidates worth synthesising, which needs
# affinity AND buildability AND safety. Those are different QUANTITIES. A single ladder forces them
# into one order and invites "scores better" to mean "is better", which is exactly the confusion that
# let an unnormalised dE_int reward a generator for adding greasy mass.

#: When a stage runs. A real total order: a pipeline executes these in sequence.
PHASES: tuple[tuple[str, str], ...] = (
    ("input", "Where the target, receptor and field come from"),
    ("geometry", "Poses: searched or built"),
    ("check", "Hard refusals on what the next phase needs"),
    ("score", "Everything that measures a candidate, by any method and of any quantity"),
    ("decision", "The paired cut: ties and UNRANKED are answers"),
)
PHASE_INDEX = {name: i for i, (name, _) in enumerate(PHASES)}

#: Rigour levels, cheapest first. ONLY comparable within one quantity: `rigour_rank` is defined
#: between two methods when `quantity` matches, and undefined otherwise.
RIGOUR: tuple[str, ...] = ("heuristic", "empirical", "semiempirical", "ab_initio", "embedded_qm")
RIGOUR_INDEX = {name: i for i, name in enumerate(RIGOUR)}

#: Which rung each (phase, rigour) pair presents as, so `rung` stays a view of the decomposition
#: rather than a second source of truth.
_RUNG_FROM = {
    ("input", None): "input",
    ("geometry", None): "geometry",
    ("check", None): "check",
    ("score", "empirical"): "empirical",
    ("score", "semiempirical"): "semiempirical",
    ("score", "ab_initio"): "ab_initio",
    ("score", "embedded_qm"): "embedded_qm",
    ("score", "heuristic"): "designable",
    ("decision", None): "decision",
}
_PHASE_OF_RUNG = {rung: phase for (phase, _), rung in _RUNG_FROM.items()}
_RIGOUR_OF_RUNG = {rung: rigour for (_, rigour), rung in _RUNG_FROM.items()}

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
    cannot share a pipeline with this one. A `required` method is satisfied by a placed method it is exclusive
    with: that one is its alternative (RHF or KS-DFT for dE_int), so `smeltery run` gets exactly one of them.
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

    @property
    def phase(self) -> str:
        """When this runs. A real total order over the pipeline; see `PHASES`."""
        return _PHASE_OF_RUNG[self.rung]

    @property
    def rigour(self) -> str | None:
        """How good the physics is, or None for a stage that is not a measurement.

        Comparable ONLY against a method of the same `quantity`. See `comparable_rigour`.
        """
        return _RIGOUR_OF_RUNG[self.rung]

    @property
    def quantity(self) -> str | None:
        """The quantity this method measures, or None when it measures nothing.

        Two methods are rigour-comparable when and only when this matches. `dE_int` from GFN2 and
        from PBE are the same quantity at different rigour; `designability` is a different question
        and is not a cheaper or worse anything.
        """
        from ..methods import registry

        spec = registry().get(self.key)
        return spec.quantity if spec is not None else None

    def comparable_rigour(self, other: Method) -> bool:
        """Can these two be ordered by rigour at all?

        False when either measures nothing, or when they measure DIFFERENT quantities. Asking which
        of `field_dft_interaction` and `designability` is more rigorous is a category error: one is
        an interaction energy, the other a synthesizability heuristic, and a funnel aiming at
        candidates worth making needs both rather than the better of them.
        """
        if self.rigour is None or other.rigour is None:
            return False
        return self.quantity is not None and self.quantity == other.quantity

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
    "remedies",  # a list of tables (the SCF remedy ladder): set in the config, no form widget
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


def cost_model(key: str) -> str:
    """How `key`'s wall time is estimated (its registration's `cost_model`), or 'cannot say: ...'."""
    from ..methods import registry

    spec = registry().get(key)
    f = spec.cost_model if spec is not None else None
    return f() if f else "cannot say: no cost model for this stage"


def memory_model(key: str) -> str:
    """How `key`'s memory is estimated (its registration's `memory_model`), or 'cannot say: ...'."""
    from ..methods import registry

    spec = registry().get(key)
    f = spec.memory_model if spec is not None else None
    return f() if f else "cannot say: no memory model for this stage"


def measured_floor(m: Method) -> float | None:
    """The tier's measured systematic floor for the quantity it produces, or None while it is unmeasured."""
    values = [v for v in _tier_facts(m.tier)["floors"].values() if v is not None]
    return float(values[0]) if values else None


def method_from_spec(spec, also_exclusive: tuple[str, ...] = ()) -> Method:
    """The catalog's view of a registration (`smeltery.methods.MethodSpec`). `also_exclusive` are the methods that
    name this one as exclusive (exclusion is symmetric; each registration states only its own side)."""
    params = tuple(spec.params) or (params_from_dataclass(spec.knobs) if spec.knobs is not None else ())
    exclusive = tuple(spec.exclusive) + tuple(k for k in also_exclusive if k not in spec.exclusive)
    return Method(
        spec.key,
        spec.label,
        spec.rung,
        spec.role,
        needs=tuple(spec.needs),
        provides=tuple(spec.provides),
        params=params,
        section=spec.section,
        gate_key=spec.gate_key,
        tier=spec.tier,
        required=spec.required,
        implicit=spec.implicit,
        wired=spec.wired,
        why_unwired=spec.why_unwired,
        extras=tuple(spec.extras),
        exclusive=exclusive,
        summary=spec.summary,
    )


def _build() -> dict[str, Method]:
    from ..methods import registry

    specs = registry()
    out = {}
    for k, spec in specs.items():
        also = tuple(o.key for o in specs.values() if k in o.exclusive)
        out[k] = method_from_spec(spec, also)
    return out


_CATALOG: tuple[int, dict[str, Method]] | None = None  # (registry version, catalog)


def catalog() -> dict[str, Method]:
    """{key: Method}, in hierarchy order (by rung, then the order `smeltery run` executes them): one per
    registration in `smeltery.methods.registry()`."""
    global _CATALOG
    from ..methods import version

    if _CATALOG is None or _CATALOG[0] != version():
        _CATALOG = (version(), _build())
    return dict(_CATALOG[1])


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
