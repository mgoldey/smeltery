"""Conformance suite: what any smeltery provider must satisfy, runnable against itself.

A provider author describes ONE provider with a per-kind `*Case` (how to build it, one input it
can answer, one input it cannot, a documented anchor answer) and calls

    assert_conforms("property", PropertyCase(...))          # raises AssertionError listing every failure

or, to get one pytest test per check,

    @pytest.mark.parametrize("check", check_names("property"))
    def test_conforms(check):
        run_check("property", check, CASE)

The same five requirements apply to every kind, each made precise per kind below.

1. Declares a licence. `license_id` is an SPDX licence expression for the CODE that produces the
   answers (the provider; wrapped engines are listed in the optional `engine_licenses` dict, name ->
   licence). The licence of the DATA the answers derive from is a different thing and goes in the
   optional `data_license_id` (e.g. RCSB PDB entries are CC0-1.0, AlphaFold DB is CC-BY-4.0); absent
   means "no external data". `data_license_id = "UNVERIFIED"` is accepted for data only, as an
   explicit statement that the licence was not checked. The check is syntactic: the expression must
   parse as SPDX syntax; it is not looked up in the SPDX list.
2. Declares its unit. structure: coordinates are PDB-format (Angstrom) and pLDDT, when present, lies
   in [0, 100]. scoring: every `Score.unit` is a non-empty string, one unit and one `is_delta_g`
   (a bool) for all poses. docking: `score_unit()` is a non-empty string. property: `endpoint_units()`
   has exactly the keys of `endpoint_notes()`, each a non-empty string, and every note says
   "rank-only". potential: `energy_unit == "kcal/mol"`, the unit `PotentialResult` is documented in.
3. Returns "cannot answer" rather than fabricating. What that looks like is per kind:
   structure: `predict` returns None and sets a non-empty `last_error`.
   property: `applicability` is False and `predict`/`assess` give None for EVERY endpoint, never 0.0
   and never NaN (None means no answer, 0.0 means "no liability").
   scoring: `score` raises (a `Score` cannot hold a non-finite value, and a made-up number is worse).
   docking: `dock` returns a `DockResult` with `error` set, no poses and `ok` False; it does not raise
   for a missing receptor and does not return a neutral-looking score.
   potential: `evaluate` raises `UnsupportedChargeStateError` for a charge outside
   `supported_charge_states`, and `PocketContextError` for an in-pocket request when
   `supports_external_charges` is False; it never answers in vacuum instead.
4. Round-trips through `RunRecord`: `settings()` is strictly JSON-serialisable (no `default=str`
   rescue, no NaN), unchanged by a JSON round trip, identical across calls and across fresh
   instances, and a `RunRecord` built from it keeps its `input_digest` through `to_json`/`from_json`.
5. Passes its own trivial-limit anchor, one that can be checked without a reference implementation:
   structure: the case's `anchor(result)` (e.g. a committed fixture comes back byte for byte);
   property: the case's `anchors` (SMILES -> documented answers; include one molecule with no alerts
   whose answer is a real 0.0, and one positive control, so the provider is not a constant);
   scoring: the case's `expected` values come back and a second call is identical;
   docking: every pose has the input molecule's atoms in order, scores are finite and best-first,
   and the same seed gives the same scores;
   potential: a known minimum (`minimum`) is a fixed point of `relax` from the displaced pose and
   has ~zero forces, and/or the energy is translation-invariant in vacuum, with the net force ~0.
   A potential case that declares neither is rejected: there is no anchor to pass.

Checks marked `engine=True` actually run the provider's computation (a docking search). Pass
`engine=False` to run the rest only (a CI job without the engine installed); skipped checks are
REPORTED as SKIP by `run_conformance`, never silently dropped.

This module imports pytest never, and no third-party package except numpy (already core).
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from ..model import PointCharge, Pose
from ..record import RunRecord
from .potential import (
    FORCE_FD_RELATIVE_BAR,
    PocketContextError,
    UnsupportedChargeStateError,
    check_forces,
    evaluate,
    relax,
)

__all__ = [
    "FAIL",
    "PASS",
    "SKIP",
    "CheckResult",
    "DockingCase",
    "PotentialCase",
    "PropertyCase",
    "ScoringCase",
    "StructureCase",
    "assert_conforms",
    "check_names",
    "is_spdx_expression",
    "run_check",
    "run_conformance",
]

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"


def _require(cond: object, msg: str) -> None:
    """`assert`, but not stripped by `python -O`."""
    if not cond:
        raise AssertionError(msg)


# --------------------------------------------------------------------------- SPDX syntax

_ID = r"[A-Za-z0-9][A-Za-z0-9.\-]*\+?"
_TOKEN = re.compile(rf"\s*(\(|\)|{_ID})")


def is_spdx_expression(text: object) -> bool:
    """Is `text` a syntactically valid SPDX licence expression (ids, AND/OR/WITH, parentheses)?

    Syntax only: `Foo-1.0` passes. `NONE`, `NOASSERTION` and the empty string are not licences and fail.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    tokens: list[str] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            return False
        tokens.append(m.group(1))
        pos = m.end()
        if text[pos:].strip() == "":
            break
    if text[pos:].strip():
        return False
    i = 0

    def simple() -> bool:
        nonlocal i
        if i >= len(tokens):
            return False
        t = tokens[i]
        if t == "(":
            i += 1
            if not expr():
                return False
            if i >= len(tokens) or tokens[i] != ")":
                return False
            i += 1
            return True
        if t in (")", "AND", "OR", "WITH") or t.upper() in ("NONE", "NOASSERTION"):
            return False
        i += 1
        if i < len(tokens) and tokens[i] == "WITH":
            i += 1
            if i >= len(tokens) or tokens[i] in ("(", ")", "AND", "OR", "WITH"):
                return False
            i += 1
        return True

    def expr() -> bool:
        nonlocal i
        if not simple():
            return False
        while i < len(tokens) and tokens[i] in ("AND", "OR"):
            i += 1
            if not simple():
                return False
        return True

    return expr() and i == len(tokens)


# --------------------------------------------------------------------------- cases


@dataclass
class StructureCase:
    """`make()` builds a fresh provider; it must work offline (inject a fetch)."""

    make: Callable[[], Any]
    answerable: str  # a spec the provider can answer
    unanswerable: str  # a spec it cannot (malformed, absent, unreachable)
    expected_kind: str  # "EXPERIMENTAL" or "PREDICTED"
    anchor: Callable[[Any], None]  # raises AssertionError unless the result matches its documented answer


@dataclass
class PropertyCase:
    make: Callable[[], Any]
    unanswerable: str  # an input outside the provider's applicability
    #: (smiles, {endpoint: expected}); expected is a number (abs tol 1e-9) or a predicate on the value.
    anchors: list[tuple[str, dict[str, Any]]]
    allow_network: bool = False


@dataclass
class ScoringCase:
    make: Callable[[], Any]
    poses: list[Pose]
    expected: list[float]  # the documented score of each pose
    unanswerable_poses: list[Pose]  # an input the provider cannot score (it must raise)
    receptor: Any = None
    expected_tol: float = 1e-9


@dataclass
class DockingCase:
    make: Callable[[], Any]
    mol: Any  # RDKit mol with hydrogens and 3-D coordinates
    receptor: Any
    box: Any
    unanswerable_receptor: Any  # e.g. a path that does not exist
    seed: int = 7
    score_upper_bound: float | None = None  # e.g. 0.0: a real pocket must give a binding score


@dataclass
class PotentialCase:
    make: Callable[[], Any]
    pose: Pose  # a displaced, non-stationary pose (forces nonzero)
    charge: int = 0
    minimum: Pose | None = None  # a known minimum of the potential, if the case has one
    minimum_tol_ang: float = 1e-4
    translation_invariant: bool = True  # False only for a potential tied to absolute coordinates; say why
    energy_atol: float = 1e-6  # kcal/mol; loosen for float32 models, visibly
    force_atol: float = 1e-6  # kcal/mol/A
    fd_bar: float = FORCE_FD_RELATIVE_BAR
    point_charges: list[PointCharge] = field(default_factory=lambda: [PointCharge(0.5, (30.0, 0.0, 0.0))])


# --------------------------------------------------------------------------- shared checks


def _c_name(case, p) -> None:
    _require(
        isinstance(getattr(p, "name", None), str) and p.name.strip(), "provider must declare a non-empty str `name`"
    )


def _c_license(case, p) -> None:
    lic = getattr(p, "license_id", None)
    _require(
        is_spdx_expression(lic),
        f"license_id must be an SPDX licence expression for the provider's code, got {lic!r} "
        "(see smeltery.providers.conformance: code licence vs data licence)",
    )
    data = getattr(p, "data_license_id", None)
    _require(
        data is None or data == "UNVERIFIED" or is_spdx_expression(data),
        f"data_license_id must be None, 'UNVERIFIED' or an SPDX expression, got {data!r}",
    )
    eng = getattr(p, "engine_licenses", {})
    _require(
        isinstance(eng, dict) and all(isinstance(k, str) and isinstance(v, str) and v.strip() for k, v in eng.items()),
        f"engine_licenses must be a {{name: licence}} dict of non-empty strings, got {eng!r}",
    )


def _strict_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, allow_nan=False)  # no default=: a non-JSON value must fail here


def _c_settings(case, p) -> None:
    s = p.settings()
    _require(isinstance(s, dict), f"settings() must return a dict, got {type(s).__name__}")
    try:
        text = _strict_json(s)
    except (TypeError, ValueError) as e:
        raise AssertionError(f"settings() is not strictly JSON-serialisable: {e}") from e
    _require(json.loads(text) == s, "settings() changes under a JSON round trip (tuples? numpy values? int keys?)")
    _require(_strict_json(p.settings()) == text, "settings() differs between two calls on one instance")
    _require(_strict_json(case.make().settings()) == text, "settings() differs between two fresh instances")
    tiers = [{"name": f"{p.name}", "provider": p.name, "settings": s}]
    rec = RunRecord("conformance", {"provider": p.name}, tiers, {}, smeltery_version="conformance", ferric={}, host={})
    back = RunRecord.from_json(rec.to_json())
    _require(back.input_digest == rec.input_digest, "RunRecord input_digest changed through to_json/from_json")
    _require(back.tiers == rec.tiers, "RunRecord tiers changed through to_json/from_json")
    other = RunRecord("conformance", {"provider": p.name}, [{**tiers[0], "settings": {**s, "_x": 1}}], {})
    _require(other.input_digest != rec.input_digest, "RunRecord digest does not depend on the provider's settings")


# --------------------------------------------------------------------------- structure


def _s_unit(case, p) -> None:
    res = p.predict(case.answerable)
    _require(res is not None, f"answerable spec {case.answerable!r} returned None; last_error={p.last_error!r}")
    for line in res.text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            try:
                [float(line[30:38]), float(line[38:46]), float(line[46:54])]
            except ValueError as e:
                raise AssertionError(f"coordinates are not PDB-format floats (Angstrom): {line!r}") from e
            break
    else:
        raise AssertionError("structure text has no ATOM/HETATM record")
    if res.plddt is not None:
        _require(all(0.0 <= v <= 100.0 for _, v in res.plddt), "pLDDT outside [0, 100]")


def _s_none(case, p) -> None:
    res = p.predict(case.unanswerable)
    _require(res is None, f"cannot-answer spec {case.unanswerable!r} returned {type(res).__name__}, not None")
    _require(
        isinstance(p.last_error, str) and p.last_error.strip(),
        "returned None without saying why: set a non-empty `last_error`",
    )


def _s_labels(case, p) -> None:
    from .structure import StructureResult

    res = p.predict(case.answerable)
    _require(isinstance(res, StructureResult), f"expected a StructureResult, got {type(res).__name__}")
    _require(res.kind == case.expected_kind, f"kind is {res.kind!r}, case expects {case.expected_kind!r}")
    data = getattr(p, "data_license_id", None)
    if res.kind == "PREDICTED":
        _require(res.plddt and res.weights_source, "a prediction must carry pLDDT and a weights source")
        _require(res.license_id == data, f"result licence {res.license_id!r} != provider data_license_id {data!r}")
    elif res.license_id is not None:
        _require(res.license_id == data, f"result licence {res.license_id!r} != provider data_license_id {data!r}")
    d = res.to_dict()
    _strict_json(d)
    import hashlib

    _require(d["sha256"] == hashlib.sha256(res.text.encode()).hexdigest(), "to_dict sha256 does not match the text")


def _s_anchor(case, p) -> None:
    res = p.predict(case.answerable)
    _require(res is not None, f"anchor spec returned None; last_error={p.last_error!r}")
    case.anchor(res)


# --------------------------------------------------------------------------- property


def _p_units(case, p) -> None:
    notes = p.endpoint_notes()
    _require(isinstance(notes, dict) and notes, "endpoint_notes() must be a non-empty dict")
    _require(
        all("rank-only" in str(v) for v in notes.values()),
        "every endpoint note must state that the value is rank-only",
    )
    units = getattr(p, "endpoint_units", None)
    _require(callable(units), "provider must declare `endpoint_units() -> {endpoint: unit}`")
    u = units()
    _require(set(u) == set(notes), f"endpoint_units keys {sorted(u)} != endpoint_notes keys {sorted(notes)}")
    _require(all(isinstance(v, str) and v.strip() for v in u.values()), "every endpoint unit must be a non-empty str")
    _require(isinstance(p.requires_network, bool), "requires_network must be a bool")


def _p_none(case, p) -> None:
    from ..properties import NetworkNotAllowedError, assess

    keys = set(p.endpoint_notes())
    _require(p.applicability(case.unanswerable) is False, f"applicability({case.unanswerable!r}) must be False")
    for label, out in (
        ("predict", p.predict(case.unanswerable)),
        ("assess", assess(p, case.unanswerable, allow_network=case.allow_network)),
    ):
        _require(set(out) == keys, f"{label} keys {sorted(out)} != endpoints {sorted(keys)}")
        bad = {k: v for k, v in out.items() if v is not None}
        _require(not bad, f"{label} fabricated answers for an input it cannot answer (None != 0.0): {bad}")
    if p.requires_network:
        try:
            assess(p, case.unanswerable)
        except NetworkNotAllowedError:
            pass
        else:
            raise AssertionError("a network provider must be refused without allow_network=True")


def _p_anchor(case, p) -> None:
    from ..properties import assess

    _require(case.anchors, "PropertyCase.anchors is empty: there is no anchor to pass")
    keys = set(p.endpoint_notes())
    for smiles, expected in case.anchors:
        out = assess(p, smiles, allow_network=case.allow_network)
        _require(set(out) == keys, f"{smiles!r}: keys {sorted(out)} != {sorted(keys)}")
        for k, v in out.items():
            _require(
                v is None or (isinstance(v, (int, float)) and math.isfinite(v)),
                f"{smiles!r}/{k}: {v!r} is not a finite number or None",
            )
        for k, want in expected.items():
            got = out[k]
            _require(got is not None, f"{smiles!r}/{k}: documented answer exists but provider gave None")
            if callable(want):
                _require(want(got), f"{smiles!r}/{k}: {got!r} fails the anchor predicate")
            else:
                _require(abs(got - want) <= 1e-9, f"{smiles!r}/{k}: got {got!r}, documented {want!r}")


# --------------------------------------------------------------------------- scoring


def _sc_unit(case, p) -> None:
    out = p.score(case.poses, case.receptor)
    _require(len(out) == len(case.poses), f"{len(out)} scores for {len(case.poses)} poses")
    _require(out, "case has no poses")
    _require(all(isinstance(s.unit, str) and s.unit.strip() for s in out), "every Score.unit must be a non-empty str")
    _require(len({s.unit for s in out}) == 1, "one unit for all scores of a call")
    _require(all(isinstance(s.is_delta_g, bool) for s in out), "Score.is_delta_g must be a bool")
    _require(len({s.is_delta_g for s in out}) == 1, "one is_delta_g for all scores of a call")


def _sc_none(case, p) -> None:
    try:
        out = p.score(case.unanswerable_poses, case.receptor)
    except Exception:  # noqa: BLE001 - any refusal is the right behaviour
        return
    raise AssertionError(f"scoring an input it cannot score returned {out!r} instead of raising")


def _sc_anchor(case, p) -> None:
    a = p.score(case.poses, case.receptor)
    got = [s.value for s in a]
    _require(len(got) == len(case.expected), f"{len(got)} scores, {len(case.expected)} documented")
    _require(
        all(abs(g - w) <= case.expected_tol for g, w in zip(got, case.expected)),
        f"scores {got} != documented {case.expected}",
    )
    b = p.score(case.poses, case.receptor)
    _require([s.value for s in b] == got, "a second identical call gave different scores")


# --------------------------------------------------------------------------- docking


def _d_unit(case, p) -> None:
    u = p.score_unit()
    _require(isinstance(u, str) and u.strip(), f"score_unit() must be a non-empty str, got {u!r}")


def _d_none(case, p) -> None:
    from ..docking.base import DockResult

    res = p.dock(case.mol, case.unanswerable_receptor, case.box, case.seed)
    _require(isinstance(res, DockResult), f"dock returned {type(res).__name__}, not DockResult")
    _require(not res.ok and not res.poses and not res.scores, "an impossible search returned poses or scores")
    _require(isinstance(res.error, str) and res.error.strip(), "an impossible search must set a non-empty `error`")


def _d_anchor(case, p) -> None:
    res = p.dock(case.mol, case.receptor, case.box, case.seed)
    _require(res.ok, f"anchor dock failed: {res.error}")
    want = tuple(a.GetSymbol() for a in case.mol.GetAtoms())
    for pose in res.poses:
        _require(tuple(pose.symbols) == want, "pose atoms differ from the input molecule's (order, hydrogens)")
        _require(np.isfinite(np.asarray(pose.coords_ang)).all(), "non-finite pose coordinates")
    _require(len(res.scores) == len(res.poses), "one score per pose")
    _require(all(math.isfinite(s) for s in res.scores), "non-finite score")
    _require(list(res.scores) == sorted(res.scores), "scores must be best (lowest) first")
    if case.score_upper_bound is not None:
        _require(res.scores[0] < case.score_upper_bound, f"best score {res.scores[0]} not below the case's bound")
    again = p.dock(case.mol, case.receptor, case.box, case.seed)
    _require(again.ok and list(again.scores) == list(res.scores), "same seed gave different scores: not reproducible")


# --------------------------------------------------------------------------- potential


def _pot_unit(case, p) -> None:
    _require(
        getattr(p, "energy_unit", None) == "kcal/mol",
        f"energy_unit must be 'kcal/mol', got {getattr(p, 'energy_unit', None)!r}",
    )
    _require(isinstance(p.supports_external_charges, bool), "supports_external_charges must be a bool")
    st = p.supported_charge_states
    _require(
        isinstance(st, frozenset) and st and all(isinstance(c, int) for c in st),
        "supported_charge_states must be a non-empty frozenset[int]",
    )
    _require(case.charge in st, f"case charge {case.charge} is not in supported_charge_states {sorted(st)}")


def _pot_none(case, p) -> None:
    bad = max(p.supported_charge_states) + 1000
    try:
        evaluate(p, case.pose, None, bad)
    except UnsupportedChargeStateError:
        pass
    else:
        raise AssertionError(f"charge {bad} (unsupported) was not refused with UnsupportedChargeStateError")
    if p.supports_external_charges:
        r = evaluate(p, case.pose, case.point_charges, case.charge)
        _require(math.isfinite(r.energy), "non-finite in-pocket energy")
    else:
        for pc in (case.point_charges, []):
            try:
                evaluate(p, case.pose, pc, case.charge)
            except PocketContextError:
                pass
            else:
                raise AssertionError("a blind potential answered an in-pocket request instead of raising")


def _pot_shape(case, p) -> None:
    r = evaluate(p, case.pose, None, case.charge)
    _require(math.isfinite(r.energy), "non-finite energy")
    _require(np.isfinite(r.forces).all(), "non-finite forces")
    _require(r.forces.shape == (len(case.pose.symbols), 3), f"forces shape {r.forces.shape}")
    _require(np.abs(r.forces).max() > 0, "case pose is stationary: pick a displaced pose")


def _pot_fd(case, p) -> None:
    err = check_forces(p, case.pose, None, case.charge)
    _require(err <= case.fd_bar, f"forces disagree with a finite difference of the energy: {err:.3g} > {case.fd_bar:g}")


def _pot_nomutate(case, p) -> None:
    before = np.array(case.pose.coords_ang, dtype=float)
    evaluate(p, case.pose, None, case.charge)
    _require(np.array_equal(np.asarray(case.pose.coords_ang), before), "energy_and_forces modified the input pose")
    res = relax(p, case.pose, None, case.charge)
    _require(np.array_equal(np.asarray(case.pose.coords_ang), before), "relax modified the input pose")
    _require(res.converged, f"relax did not converge in {res.steps} steps (max force {res.max_force:.3g})")


def _pot_anchor(case, p) -> None:
    _require(
        case.minimum is not None or case.translation_invariant,
        "PotentialCase declares neither a known minimum nor translation invariance: no anchor to pass",
    )
    if case.minimum is not None:
        at_min = evaluate(p, case.minimum, None, case.charge)
        _require(
            np.abs(at_min.forces).max() <= case.force_atol,
            f"forces at the known minimum are {np.abs(at_min.forces).max():.3g}",
        )
        res = relax(p, case.pose, None, case.charge)
        err = float(np.abs(res.coords_ang - np.asarray(case.minimum.coords_ang)).max())
        _require(
            err <= case.minimum_tol_ang,
            f"relax ended {err:.3g} A from the known minimum (tol {case.minimum_tol_ang:g})",
        )
    if case.translation_invariant:
        x = np.asarray(case.pose.coords_ang, dtype=float)
        a = evaluate(p, case.pose, None, case.charge)
        b = evaluate(p, Pose(case.pose.symbols, x + np.array([1.7, -2.3, 0.9])), None, case.charge)
        _require(
            abs(a.energy - b.energy) <= case.energy_atol,
            f"energy changed {abs(a.energy - b.energy):.3g} under translation",
        )
        net = np.abs(a.forces.sum(axis=0)).max()
        _require(net <= case.force_atol, f"net force in vacuum is {net:.3g}, not 0")


# --------------------------------------------------------------------------- driver

_COMMON = [("name", False, _c_name), ("license", False, _c_license), ("settings_round_trip", False, _c_settings)]

_CHECKS: dict[str, list[tuple[str, bool, Callable]]] = {
    "structure": [
        *_COMMON,
        ("unit", False, _s_unit),
        ("none_not_fabricated", False, _s_none),
        ("labels_and_provenance", False, _s_labels),
        ("anchor", False, _s_anchor),
    ],
    "property": [
        *_COMMON,
        ("units_and_notes", False, _p_units),
        ("none_not_fabricated", False, _p_none),
        ("anchor", False, _p_anchor),
    ],
    "scoring": [
        *_COMMON,
        ("unit", False, _sc_unit),
        ("none_not_fabricated", False, _sc_none),
        ("anchor", False, _sc_anchor),
    ],
    "docking": [
        *_COMMON,
        ("unit", False, _d_unit),
        ("none_not_fabricated", True, _d_none),
        ("anchor", True, _d_anchor),
    ],
    "potential": [
        *_COMMON,
        ("unit", False, _pot_unit),
        ("none_not_fabricated", False, _pot_none),
        ("result_shape", False, _pot_shape),
        ("forces_match_finite_difference", False, _pot_fd),
        ("relax_leaves_input_alone", False, _pot_nomutate),
        ("anchor", False, _pot_anchor),
    ],
}


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


def _kind_checks(kind: str) -> list[tuple[str, bool, Callable]]:
    if kind not in _CHECKS:
        raise ValueError(f"unknown provider kind {kind!r}; kinds are {sorted(_CHECKS)}")
    return _CHECKS[kind]


def check_names(kind: str) -> list[str]:
    """Names of the checks for `kind`, for `pytest.mark.parametrize`."""
    return [n for n, _, _ in _kind_checks(kind)]


def run_check(kind: str, name: str, case: Any) -> None:
    """Run one check on a fresh provider from `case.make()`. Raises AssertionError on failure."""
    fns = {n: fn for n, _, fn in _kind_checks(kind)}
    if name not in fns:
        raise KeyError(f"no check {name!r} for kind {kind!r}; have {sorted(fns)}")
    fns[name](case, case.make())


def run_conformance(kind: str, case: Any, *, engine: bool = True) -> list[CheckResult]:
    """Run every check; never raises for a provider failure. `engine=False` reports engine checks as SKIP."""
    out = []
    for name, needs_engine, _ in _kind_checks(kind):
        if needs_engine and not engine:
            out.append(CheckResult(name, SKIP, "needs the provider's engine; run with engine=True"))
            continue
        try:
            run_check(kind, name, case)
        except AssertionError as e:
            out.append(CheckResult(name, FAIL, str(e)))
        except Exception as e:  # noqa: BLE001 - a provider that raises where it should answer fails the check
            out.append(CheckResult(name, FAIL, f"{type(e).__name__}: {e}"))
        else:
            out.append(CheckResult(name, PASS))
    return out


def assert_conforms(kind: str, case: Any, *, engine: bool = True) -> list[CheckResult]:
    """`run_conformance`, raising one AssertionError that lists every failed check. Returns the results."""
    results = run_conformance(kind, case, engine=engine)
    failed = [r for r in results if r.status == FAIL]
    if failed:
        raise AssertionError(
            f"{kind} provider failed conformance:\n" + "\n".join(f"  {r.name}: {r.detail}" for r in failed)
        )
    return results
