"""A pipeline a GUI edits: an ordered list of stages, checked as data flows through it, compiled to the config
`smeltery run` reads.

The GUI owns the ordering and the knobs; this module owns the rules. `Pipeline.check()` never raises: it returns
every `Issue` at once (errors block a run, warnings and notes are shown), because a form wants all of them, not
the first. Its last check is `smeltery.cli.validate_config` on the compiled config, so a pipeline that checks
clean is one the CLI accepts; the GUI layer adds no second, drifting copy of the config rules.

Stage order is the user's to arrange, but each stage may only use artefacts an EARLIER stage provides
(`Method.needs` / `provides`). `smeltery run` executes in one fixed order (`EXECUTION_ORDER`); a pipeline whose
order differs only where no data flows gets a note saying how it will run, not an error.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field

from .catalog import Method, catalog, method

#: The order `smeltery run` executes stages in (`smeltery.cli.build_plan` then `run_plan`).
EXECUTION_ORDER: tuple[str, ...] = (
    "structure",
    "docking",
    "paired_poses",
    "pocket",
    "parity",
    "posebusters",
    "forcefield",
    "gfn2",
    "field_interaction",
    "cut",
)

#: Measured quantity the cut ranks (`smeltery.cli.QUANTITY`).
QUANTITY = "dE_int"


class PipelineError(ValueError):
    """`Pipeline.compile()` on a pipeline whose `check()` has errors. Carries the issues."""

    def __init__(self, issues: list[Issue]):
        self.issues = issues
        super().__init__("; ".join(f"{i.stage or 'pipeline'}: {i.message}" for i in issues))


@dataclass(frozen=True)
class Issue:
    level: str  # "error" (blocks compile), "warning" (runs, but probably not what was meant), "note"
    stage: str | None  # the method key it is about; None for the pipeline as a whole
    message: str


@dataclass
class Stage:
    """A method placed in a pipeline. `params` holds only what the user SET: unset knobs take the method's default
    at run time, so a config read in and written out again is unchanged."""

    method: str
    params: dict = field(default_factory=dict)

    @property
    def info(self) -> Method:
        return method(self.method)


@dataclass
class Pipeline:
    campaign: str
    parent: str
    candidates: dict[str, str]
    stages: list[Stage] = field(default_factory=list)
    #: The config's `[decisions]` table, carried through unchanged (an input file, not a stage), or None.
    decisions: dict | None = None

    # ------------------------------------------------------------------ building

    @classmethod
    def minimal(cls, campaign: str, parent: str, candidates: dict[str, str]) -> Pipeline:
        """The shortest pipeline `smeltery run` can execute: paired poses, a pocket, the RHF tier, the cut.
        The pocket and the cut still need their knobs (sites or a file; `keep`)."""
        p = cls(campaign, parent, dict(candidates))
        for key in ("paired_poses", "pocket", "field_interaction", "cut"):
            p.add(key)
        return p

    def keys(self) -> list[str]:
        return [s.method for s in self.stages]

    def stage(self, key: str) -> Stage:
        for s in self.stages:
            if s.method == key:
                return s
        raise KeyError(f"no {key!r} stage in this pipeline; stages: {self.keys()}")

    def add(self, key: str, params: dict | None = None, at: int | None = None) -> Stage:
        """Place method `key`. `at` is an index (a drop position); by default the stage goes where
        `smeltery run` would execute it. Refuses an unknown, implicit or duplicate method; anything else
        (unwired, misordered, conflicting) is allowed here and reported by `check()`, so a GUI can show it."""
        m = method(key)
        if m.implicit:
            raise ValueError(f"{key!r} always runs in smeltery run; it is shown, not placed")
        if key in self.keys():
            raise ValueError(f"{key!r} is already in the pipeline")
        if at is None:
            rank = _exec_rank(key)
            at = sum(1 for s in self.stages if _exec_rank(s.method) <= rank)
        s = Stage(key, dict(params or {}))
        self.stages.insert(at, s)
        return s

    def remove(self, key: str) -> None:
        self.stages.remove(self.stage(key))

    def move(self, key: str, to: int) -> None:
        s = self.stage(key)
        self.stages.remove(s)
        self.stages.insert(to, s)

    def set(self, key: str, **params) -> None:
        """Set knobs on a stage; a value of None unsets one (back to the method's default)."""
        s = self.stage(key)
        for k, v in params.items():
            s.info.param(k)  # unknown knob: KeyError naming the ones that exist
            if v is None:
                s.params.pop(k, None)
            else:
                s.params[k] = v

    def addable(self) -> list[str]:
        """Methods the palette may offer now: wired, not implicit, not placed, not excluded by a placed one."""
        here = set(self.keys())
        blocked = {x for k in here for x in method(k).exclusive}
        return [
            k
            for k, m in catalog().items()
            if m.wired and not m.implicit and k not in here and k not in blocked and not set(m.exclusive) & here
        ]

    # ------------------------------------------------------------------ checking

    def check(self) -> list[Issue]:
        issues: list[Issue] = []
        keys = self.keys()
        known = [k for k in keys if k in catalog()]
        for k in keys:
            if k not in catalog():
                issues.append(Issue("error", k, f"unknown method; methods: {sorted(catalog())}"))
        for k in sorted({k for k in known if keys.count(k) > 1}):
            issues.append(Issue("error", k, "placed more than once; smeltery run executes each stage once"))
        for k in known:
            m = method(k)
            if not m.wired:
                issues.append(Issue("error", k, f"{m.label} cannot run in a pipeline yet: {m.why_unwired}"))
            if m.implicit:
                issues.append(Issue("error", k, "always runs; it is not placed"))
            for other in m.exclusive:
                if other in known and keys.index(other) > keys.index(k):
                    issues.append(Issue("error", k, f"cannot be combined with {other!r}"))
        for k, m in catalog().items():
            if m.required and k not in known:
                issues.append(Issue("error", None, f"add {k!r} ({m.label}): every pipeline needs it"))
        if "structure" in known and "docking" not in known:
            issues.append(Issue("error", "structure", "a structure is docked into: add 'docking' after it"))
        if "pocket" not in known and "structure" not in known:
            issues.append(Issue("error", None, "add 'pocket' or 'structure': the field-interaction tier needs a field"))
        issues += self._dataflow(known)
        issues += self._params(known)
        issues += self._advice(known)
        if any(i.level == "error" for i in issues):
            return issues  # the config validator would only repeat the first of these
        return issues + self._validator()

    def _dataflow(self, known: list[str]) -> list[Issue]:
        issues, provided, providers = [], set(), {}
        for k, m in catalog().items():
            for a in m.provides:
                providers.setdefault(a, []).append(k)
        for k in known:
            m = method(k)
            needs = set(m.needs)
            if k == "docking" and {"receptor", "box_center"} <= set(self.stage(k).params):
                needs.discard("receptor")  # a hand-prepared receptor and box stand in for [structure]
            for a in sorted(needs - provided):
                hint = " or ".join(repr(p) for p in providers.get(a, []))
                if k == "docking" and a == "receptor":
                    hint += ", or set this stage's receptor and box_center"
                issues.append(Issue("error", k, f"needs {a} ({_artefact(a)}), which no earlier stage provides: {hint}"))
            provided |= set(m.provides)
            if k == "docking":
                provided.add("parent_poses")
        placed = [k for k in known if k in EXECUTION_ORDER]
        runs = sorted(placed, key=_exec_rank)
        if placed != runs and not issues:
            issues.append(Issue("note", None, "smeltery run executes these in the order " + " -> ".join(runs)))
        return issues

    def _params(self, known: list[str]) -> list[Issue]:
        issues = []
        for k in known:
            m, s = method(k), self.stage(k)
            names = {p.name for p in m.params}
            for name in sorted(set(s.params) - names):
                issues.append(Issue("error", k, f"unknown knob {name!r}; knobs: {sorted(names)}"))
            for p in m.params:
                why = p.check(s.params.get(p.name))
                if why:
                    issues.append(Issue("error", k, why))
        return issues

    def _advice(self, known: list[str]) -> list[Issue]:
        """What runs but is easy to misread. Not errors: each is a choice the config may make."""
        out = []
        poses = self.stage("paired_poses").params if "paired_poses" in known else {}
        if "posebusters" in known and not poses.get("relax_unmapped", False):
            out.append(
                Issue(
                    "warning",
                    "posebusters",
                    "paired analogue poses fail PoseBusters at the core-substituent junction unless "
                    "paired_poses relax_unmapped = true",
                )
            )
        if "forcefield" in known and "max_strain_kcal" not in self.stage("forcefield").params:
            out.append(Issue("note", "forcefield", "strain is recorded; no pose is rejected without max_strain_kcal"))
        if "gfn2" in known:
            out.append(Issue("note", "gfn2", "GFN2 energies are recorded, never ranked on"))
        if "cut" in known and "floor" not in self.stage("cut").params and self._tier_floor() is None:
            out.append(
                Issue(
                    "error",
                    "cut",
                    f"the field-interaction tier has not measured its systematic floor for {QUANTITY}; set the "
                    "cut's floor explicitly (the record will say it came from the config, not a measurement)",
                )
            )
        return out

    def _tier_floor(self) -> float | None:
        from ..tiers import FieldInteraction

        return FieldInteraction().systematic_floor(QUANTITY)

    def _validator(self) -> list[Issue]:
        from ..cli import ConfigError, validate_config

        try:
            validate_config(self.to_config())
        except ConfigError as e:
            return [Issue("error", _stage_of(str(e)), str(e))]
        return []

    @property
    def ok(self) -> bool:
        return not any(i.level == "error" for i in self.check())

    # ------------------------------------------------------------------ the config

    def to_config(self) -> dict:
        """The config dict `smeltery run` reads. A pure translation: run `check()` (or `compile()`) to know whether
        the CLI will accept it."""
        cfg: dict = {"campaign": self.campaign, "parent": self.parent, "candidates": dict(self.candidates)}
        gates: dict = {}
        for s in self.stages:
            m = s.info
            params = {k: _plain(v) for k, v in s.params.items() if v is not None}
            if m.gate_key:
                gates[m.gate_key] = True
                gates.update(params)  # max_strain_kcal lives under [gates]
            elif m.section and (params or m.key in ("pocket", "cut")):  # those two are required tables
                cfg[m.section] = params
        if gates:
            cfg["gates"] = gates
        if self.decisions is not None:
            cfg["decisions"] = dict(self.decisions)
        return cfg

    def compile(self) -> dict:
        """`to_config()`, refusing (PipelineError) if `check()` has any error."""
        errors = [i for i in self.check() if i.level == "error"]
        if errors:
            raise PipelineError(errors)
        return self.to_config()

    def to_toml(self) -> str:
        from ._toml import dumps

        return dumps(self.compile())

    @classmethod
    def from_config(cls, cfg: dict) -> Pipeline:
        """Read a config (as `tomllib` parses it) into stages, in execution order. Validation is `check()`'s job:
        a broken config is read as far as it goes so a GUI can show what is wrong with it."""
        gates = dict(cfg.get("gates", {}))
        by_section = {m.section: m.key for m in catalog().values() if m.section}
        by_gate = {m.gate_key: m.key for m in catalog().values() if m.gate_key}
        p = cls(cfg.get("campaign", ""), cfg.get("parent", ""), dict(cfg.get("candidates", {})))
        if "decisions" in cfg:
            p.decisions = dict(cfg["decisions"])
        for section, key in by_section.items():
            if section in cfg or method(key).required or (key == "docking" and "structure" in cfg):
                p.add(key, dict(cfg.get(section, {})))
        for gate, key in by_gate.items():
            if gates.pop(gate, False):
                p.add(key)
        if "max_strain_kcal" in gates:
            if "forcefield" not in p.keys():
                p.add("forcefield")
            p.set("forcefield", max_strain_kcal=gates.pop("max_strain_kcal"))
        return p

    @classmethod
    def from_toml(cls, text: str) -> Pipeline:
        return cls.from_config(tomllib.loads(text))

    # ------------------------------------------------------------------ drawing

    def view(self) -> list[dict]:
        """One row per stage as `smeltery run` will execute it (implicit stages included), for drawing the funnel:
        rung, role, artefacts, and the candidate count in and out. Gates pass everyone or stop the run, so only the
        cut changes the count, and it counts ANALOGUES: the parent is the paired reference. Its `out` is `keep`,
        unless the cut is unresolved at the boundary, which only the run can say (`may_be_unranked`)."""
        n = len(self.candidates)
        keys = [k for k in self.keys() if k in catalog()]
        if "paired_poses" in keys:
            keys.append("parity")
        keys.sort(key=_exec_rank)
        rows = []
        for k in keys:
            m = method(k)
            n_in, n_out = {
                "prep": (None, None),
                "search": (1, 1),
                "rank": (n - 1, self.stage(k).params.get("keep") if k in self.keys() else None),
            }.get(m.role, (n, n))
            row = {
                "stage": k,
                "label": m.label,
                "rung": m.rung,
                "role": m.role,
                "implicit": m.implicit,
                "needs": list(m.needs),
                "provides": list(m.provides),
                "candidates_in": n_in,
                "candidates_out": n_out,
            }
            if m.role == "rank":
                row["may_be_unranked"] = True
            rows.append(row)
        return rows


def _exec_rank(key: str) -> int:
    return EXECUTION_ORDER.index(key) if key in EXECUTION_ORDER else len(EXECUTION_ORDER)


def _artefact(a: str) -> str:
    from .catalog import ARTEFACTS

    return ARTEFACTS[a]


def _plain(v):
    """Tuples (as a GUI may hold vectors) become lists, as TOML and the config validator want them."""
    if isinstance(v, tuple):
        return [_plain(x) for x in v]
    if isinstance(v, list):
        return [_plain(x) for x in v]
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    return v


def _stage_of(message: str) -> str | None:
    """The stage a config-validator message is about, from the section it names ('[docking] ...')."""
    sections = {m.section: m.key for m in catalog().values() if m.section}
    sections["gates"] = None
    at = {key: message.find(f"[{section}") for section, key in sections.items() if f"[{section}" in message}
    return min(at, key=at.get) if at else None
