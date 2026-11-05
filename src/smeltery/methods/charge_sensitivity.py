"""`charge_sensitivity`: the measurement repeated under a second force field's pocket charges.

The same pocket file is charged again by pdb2pqr with `--ff=<model>` (same file, same cutoff and centre, same water
choice: only the force field differs), every pose of every candidate is measured again with the plan's own tier in
that field, and the disagreement with the primary measurement is reported by `smeltery.gates.charge_sensitivity`:
paired ddE per analogue under both models, sign flips, Spearman. Config: `[charge_sensitivity] models = ["CHARMM"]`.

Interface (the Temporal runner, task D4, calls the first two; `run` is the serial path `smeltery run` takes):

* `extra_fields(plan, ctx) -> dict[str, list[PointCharge]]`: model name -> the pocket charged by `pdb2pqr --ff=<model>`,
  each a `PocketField` whose provenance records the force field.
* `summarize(plan, rec, measured) -> dict`: `measured[model][candidate]` is that candidate's per-pose dE_int under the
  model, by ORIGINAL pose index (None for a failed or dropped pose). Returns `{model: {"paired_ddE", "sign_flips",
  "spearman", ...}}`, paired against the primary per-pose values in `rec.results["per_pose"]`. After a pairwise pose
  drop those hold only each candidate's kept poses; `rec.results["pose_index"]` (each candidate's kept original
  indices) maps them back first, so pose i under one model always pairs with pose i under the other. Pairwise drop: a
  pose missing on either side (or for the parent on either side) is dropped from both, and each model's entry lists
  the dropped poses (original indices) and counts them.
* `extra_models(cfg) -> list[str]`: the models the section asks for, without pdb2pqr (for counting SCFs at --plan).
* `run(plan, rec, ctx)`: per model, its field, then every pose under it (serially; an SCF that does not converge is
  recorded as None with its error, not retried), then `summarize`. Merged into `results.charge_sensitivity`. A field
  that cannot be built (`FIELD_FAILURES`) is recorded as `{"error": ..., "stage": "field"}`; any other exception is
  a bug and propagates.

SCF log contract: each extra-model calc row is the tier's own row (so it keeps a `rung` if the tier wrote one) plus
`"pose"` and `"field_model": <model>`, kept in `results.charge_sensitivity[model]["calcs"]`. The runner's output
writer (D4) moves them into calcs.jsonl, the one per-SCF log, and replaces the list with their count.

`ctx["measure"]` (the measure `run_plan` was given) is NOT used to re-measure: a combine of partial runs passes one that
replays the primary values, which would report a sensitivity of exactly zero. Tests inject `ChargeSensitivity.measure`
instead (same contract as `run_plan`'s: fill `per_pose["dE_int"]` of `plan.candidates`, reading `plan.field`).
"""

from __future__ import annotations

import copy
import dataclasses
import inspect
import math
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from . import MethodSpec, register

#: The failures building an extra field that are outcomes, not bugs: pdb2pqr exiting non-zero (CalledProcessError),
#: pdb2pqr missing or broken (Pdb2PqrUnavailableError, a RuntimeError), the pocket file unreadable or gone (OSError),
#: and the ValueErrors `extra_field` / `load_pocket` raise by design (a changed digest, a field pdb2pqr did not
#: charge, a PQR with no charges). Anything else is our bug and propagates.
FIELD_FAILURES: tuple[type[BaseException], ...] = (subprocess.CalledProcessError, RuntimeError, OSError, ValueError)
#: The failures of one pose's SCF that are outcomes: the tiers raise RuntimeError for an SCF that did not converge
#: (and its subclasses for a refused pose or a memory refusal). Anything else propagates.
POSE_FAILURES: tuple[type[BaseException], ...] = (RuntimeError,)

QUANTITY = "dE_int"
#: The force fields `pdb2pqr --ff` accepts (pdb2pqr 3.x).
PDB2PQR_FORCE_FIELDS = ("AMBER", "CHARMM", "PARSE", "TYL06", "PEOEPB", "SWANSON")


@dataclass
class ChargeSensitivityKnobs:
    models: list[str] = field(default_factory=lambda: ["CHARMM"])


def _primary_ff() -> str:
    """The force field the primary pocket is charged with: `load_pocket`'s own default, read so it cannot drift."""
    from ..pocket import load_pocket

    return str(inspect.signature(load_pocket).parameters["ff"].default)


def validate(cfg: dict) -> None:
    """`[charge_sensitivity]`: models are distinct pdb2pqr force fields other than the primary one, and the pocket is
    one pdb2pqr charges (a `[pocket]` .pdb file, or `[structure]`). Checked before any SCF runs, since the analysis
    itself runs only after the whole measurement."""
    if "charge_sensitivity" not in cfg:
        return
    from ..cli import ConfigError, _check_keys

    s = cfg["charge_sensitivity"]
    if not isinstance(s, dict):
        raise ConfigError("[charge_sensitivity] must be a table")
    _check_keys("[charge_sensitivity]", s, {"models"})
    models = s.get("models", ["CHARMM"])
    if not (isinstance(models, list) and models and all(isinstance(m, str) for m in models)):
        raise ConfigError("[charge_sensitivity] models must be a non-empty list of pdb2pqr force-field names")
    bad = [m for m in models if m not in PDB2PQR_FORCE_FIELDS]
    if bad:
        raise ConfigError(
            f"[charge_sensitivity] models {bad} are not pdb2pqr force fields {list(PDB2PQR_FORCE_FIELDS)}"
        )
    if len(set(models)) != len(models):
        raise ConfigError("[charge_sensitivity] models lists a force field twice")
    primary = _primary_ff()
    if primary in models:
        raise ConfigError(
            f"[charge_sensitivity] models includes {primary}, the force field the primary pocket is already charged "
            "with: a model compared with itself measures nothing"
        )
    pocket = cfg.get("pocket")
    by_pdb2pqr = "structure" in cfg or (
        isinstance(pocket, dict) and isinstance(pocket.get("file"), str) and pocket["file"].lower().endswith(".pdb")
    )
    if not by_pdb2pqr:
        raise ConfigError(
            "[charge_sensitivity] re-charges the pocket with pdb2pqr: it needs [pocket] file = a .pdb "
            "(or [structure]); point-charge sites and a .pqr carry no force field to swap"
        )


def _finite(v: Any) -> float | None:
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _nan_none(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


class ChargeSensitivity:
    """The `analysis` hook. `measure`, when set, replaces the SCFs (tests); see the module docstring."""

    def __init__(self, measure: Callable[[Any], None] | None = None) -> None:
        self.measure = measure

    # ------------------------------------------------------------------------------------------------ the fields

    def extra_fields(self, plan: Any, ctx: dict) -> dict[str, list]:
        """{model: the primary pocket re-charged by pdb2pqr --ff=model}, built from the primary field's provenance:
        the same file (checked by digest), cutoff, centre and water choice."""
        return {model: self.extra_field(plan, model) for model in self.extra_models(plan.cfg)}

    def extra_models(self, cfg: dict) -> list[str]:
        """The extra force fields the config asks for, without running pdb2pqr: a plan counts its SCFs with this
        (poses x candidates x (1 + len(extra_models)))."""
        section = cfg.get("charge_sensitivity")
        if not isinstance(section, dict):
            return []
        return list(section.get("models", ["CHARMM"]))

    def extra_field(self, plan: Any, model: str) -> list:
        """One model's field (see `extra_fields`); raises if the primary field is not a pdb2pqr-charged .pdb, the file
        changed, or pdb2pqr fails."""
        from ..pocket import file_digest, load_pocket

        prov = getattr(plan.field, "provenance", None)
        path = plan.receptor_pdb
        if not prov or prov.get("pdb2pqr_ff") is None or path is None:
            raise ValueError(
                "charge_sensitivity needs the primary field to be a .pdb pocket charged by pdb2pqr "
                "([pocket] file = a .pdb, or [structure]); this plan's field is not"
            )
        if file_digest(path) != prov["input_sha256"]:
            raise ValueError(f"{path.name} no longer has the digest the primary field was built from")
        if model == prov["pdb2pqr_ff"]:
            raise ValueError(f"model {model} is the primary field's force field")
        return load_pocket(
            path,
            cutoff_ang=prov.get("cutoff_ang"),
            center_ang=prov.get("center_ang"),
            ff=model,
            drop_waters=bool(prov.get("drop_waters", False)),
        )

    # ---------------------------------------------------------------------------------------------- the summary

    def summarize(self, plan: Any, rec: Any, measured: dict[str, dict[str, list[float | None]]]) -> dict:
        """{model: paired ddE per analogue under the primary model and this one, sign flips, Spearman, floor}, with
        poses dropped pairwise. Fewer than two analogues left to compare: the entry says why instead of a statistic."""
        from ..funnel import paired_delta
        from ..gates import charge_sensitivity
        from ..model import Candidate

        results = rec.results
        index = results.get("pose_index") or {}

        def by_original(name: str, compact: list | None) -> list | None:
            """The primary values at their original pose indices (None at a dropped pose): per_pose is compacted to the
            kept poses after a pairwise drop, while `measured` keeps every original index."""
            keep = index.get(name)
            if keep is None or compact is None:
                return compact
            full: list = [None] * (max(keep) + 1 if keep else 0)
            for k, i in enumerate(keep):
                full[i] = compact[k] if k < len(compact) else None
            return full

        primary = {k: by_original(k, (v or {}).get(QUANTITY)) for k, v in (results.get("per_pose") or {}).items()}
        order = list(results.get("candidate_order") or [c.name for c in plan.candidates])
        parent = plan.parent.name
        smiles = {c.name: c.smiles for c in plan.candidates}
        primary_ff = (getattr(plan.field, "provenance", None) or {}).get("pdb2pqr_ff")

        def side(src: dict, name: str) -> list[float | None]:
            return [_finite(v) for v in (src.get(name) or [])]

        def at(xs: list[float | None], i: int) -> float | None:
            return xs[i] if i < len(xs) else None

        def ddE(xs: list[float | None], ps: list[float | None], keep: list[int], name: str) -> float:
            p, c = Candidate(parent, smiles.get(parent, "")), Candidate(name, smiles.get(name, ""))
            p.per_pose[QUANTITY] = [float(ps[i]) for i in keep]  # type: ignore[arg-type]
            c.per_pose[QUANTITY] = [float(xs[i]) for i in keep]  # type: ignore[arg-type]
            return float(paired_delta(p, c, QUANTITY).mean)

        out: dict[str, dict] = {}
        for model, values in measured.items():
            par_a, par_b = side(primary, parent), side(values, parent)
            rows: dict[str, dict] = {}
            dropped: dict[str, list[int]] = {}
            pairs: dict[str, tuple[float, float]] = {}
            excluded: list[str] = []
            n_par = max(len(par_a), len(par_b))
            parent_gone = [i for i in range(n_par) if at(par_a, i) is None or at(par_b, i) is None]
            # poses the run itself dropped for the parent (so for every analogue): the parent's, not each analogue's
            by_parent = set(range(n_par)) - set(index[parent]) if parent in index else set()
            for name in order:
                if name == parent:
                    continue
                a, b = side(primary, name), side(values, name)
                n = max(len(a), len(b), len(par_a), len(par_b))
                # pairwise: a pose missing on either side (the analogue's or the parent's) is dropped from both
                keep = [i for i in range(n) if all(at(x, i) is not None for x in (a, b, par_a, par_b))]
                # its own missing poses
                own = [i for i in range(n) if (at(a, i) is None or at(b, i) is None) and i not in by_parent]
                if own:
                    dropped[name] = own
                if not keep:
                    excluded.append(name)
                    rows[name] = {"primary": None, "model": None, "delta": None, "n": 0}
                    continue
                va, vb = ddE(a, par_a, keep, name), ddE(b, par_b, keep, name)
                pairs[name] = (va, vb)
                rows[name] = {"primary": va, "model": vb, "delta": vb - va, "n": len(keep)}
            entry: dict[str, Any] = {
                "primary_model": primary_ff,
                "paired_ddE": rows,
                "dropped_poses": dropped,  # an analogue's own missing poses
                "parent_dropped_poses": parent_gone,  # the parent's: dropped for every analogue
                # distinct (candidate, pose) values missing on either side; a parent pose counts once
                "n_dropped": len(parent_gone) + sum(len(v) for v in dropped.values()),
                "excluded": excluded,
                "per_pose": {k: [_finite(v) for v in vs] for k, vs in values.items()},
            }
            if len(pairs) < 2:
                entry.update(
                    sign_flips=None,
                    spearman=None,
                    floor=None,
                    n=len(pairs),
                    note=f"{len(pairs)} analogue(s) with a pose measured under both models: at least 2 are needed",
                )
            else:
                cands = [Candidate(k, smiles.get(k, "")) for k in pairs]
                rep = charge_sensitivity(
                    cands, QUANTITY, lambda c, q, p=pairs: p[c.name][0], lambda c, q, p=pairs: p[c.name][1]
                )
                entry.update(
                    sign_flips=rep.n_sign_flips, spearman=_nan_none(rep.spearman), floor=_nan_none(rep.floor), n=rep.n
                )
            out[model] = entry
        return out

    # -------------------------------------------------------------------------------------------- the serial path

    def _measure_field(
        self, plan: Any, model: str, fld: list
    ) -> tuple[dict[str, list[float | None]], dict, list[dict]]:
        """Every pose of every candidate in `fld`: ({candidate: per-pose dE_int or None}, failures, SCF log rows)."""
        from ..model import Candidate

        if self.measure is not None:
            cands = [
                Candidate(c.name, c.smiles, list(c.poses), {}, list(c.receptor_flex), c.pose_report)
                for c in plan.candidates
            ]
            parent = next(c for c in cands if c.name == plan.parent.name)
            view = dataclasses.replace(plan, candidates=cands, parent=parent, field=fld, tier=copy.copy(plan.tier))
            self.measure(view)
            return {c.name: [_finite(v) for v in c.per_pose.get(QUANTITY, [])] for c in cands}, {}, []
        tier = copy.copy(plan.tier)
        values: dict[str, list[float | None]] = {}
        failures: dict[str, dict[str, str]] = {}
        calcs: list[dict] = []
        for c in plan.candidates:
            vals: list[float | None] = []
            for i, pose in enumerate(c.poses):
                one = Candidate(c.name, c.smiles, [pose])
                if c.pose_report is not None:  # the gate's verdict for this pose, so the tier's own check still runs
                    one.pose_report = dataclasses.replace(c.pose_report, verdicts=(c.pose_report.verdicts[i],))
                try:
                    tier.run([one], {"field": fld})
                    vals.append(_finite(one.per_pose[QUANTITY][0]))
                except POSE_FAILURES as e:  # an SCF that did not converge: recorded, not retried
                    vals.append(None)
                    failures.setdefault(c.name, {})[str(i)] = f"{type(e).__name__}: {e}"
                # the row keeps any rung the tier wrote; field_model says which pocket it ran in
                calcs.extend(
                    {**row, "pose": i, "field_model": model} for row in (getattr(tier, "calc_log", None) or [])
                )
            values[c.name] = vals
        return values, failures, calcs

    def run(self, plan: Any, rec: Any, ctx: dict) -> dict:
        """The expected failures do not cost the run its record (the primary measurement is done): a field that cannot
        be built (`FIELD_FAILURES`) is recorded as `{model: {"error": ..., "stage": "field"}}`, an SCF that does not
        converge (`POSE_FAILURES`) as a None pose with its error. Anything else is a bug in this code and propagates,
        with its traceback, rather than being recorded as a measured outcome."""
        out: dict[str, dict] = {}
        for model in self.extra_models(plan.cfg):
            try:
                fld = self.extra_field(plan, model)
            except FIELD_FAILURES as e:
                out[model] = {"error": f"{type(e).__name__}: {e}", "stage": "field"}
                continue
            measured, failures, calcs = self._measure_field(plan, model, fld)
            entry = self.summarize(plan, rec, {model: measured})[model]
            entry["field"] = dict(getattr(fld, "provenance", {}) or {})
            entry["failures"] = failures
            if calcs:
                entry["calcs"] = calcs
            out[model] = entry
        return out


HOOK = ChargeSensitivity()


def register_methods() -> None:
    from ..gui.catalog import Param

    register(
        MethodSpec(
            "charge_sensitivity",
            "Charge-model sensitivity (pdb2pqr)",
            "ab_initio",
            "measure",
            step="measure",
            slot="analysis",
            section="charge_sensitivity",
            knobs=ChargeSensitivityKnobs,
            order=150,
            needs=("poses", "field", "dE_int"),
            hook=HOOK,
            validate=validate,
            params=(
                Param(
                    "models",
                    "str_list",
                    ["CHARMM"],
                    help="pdb2pqr force fields to re-charge the pocket with; each repeats every pose's measurement",
                ),
            ),
            summary=(
                "The measurement repeated with the pocket charged by a second force field; reports how far the "
                "paired ddE, their signs and their order move."
            ),
        )
    )
