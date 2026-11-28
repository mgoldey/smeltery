"""Measure the FieldInteraction charge-model sensitivity floor from stored per-pose energies, and pool the evidence.

Nothing here runs quantum chemistry. It reads PLB-style results files (`smeltery-plb-run/1`, see
`smeltery.plb_measure`) that hold per-pose `dE_int` under two or more pocket charge models on IDENTICAL geometries,
and for each system computes what `gates.charge_sensitivity` reports on the paired ddE: the range of the
per-analogue shifts (the floor, a bound on how far swapping models can change the gap between ANY two analogues).

What a pooled number is, and is not
-----------------------------------
* Each system's floor is a RANGE over its n analogues, so it grows with n and is one draw. Pooling reports the
  per-system floors, their max (the conservative figure), min and median, a df-weighted pooled SD of the shifts, and
  the max/min ratio. It does not average away a disagreement.
* It is a LOWER bound on a systematic floor: one model pair (AMBER ff14SB vs CHARMM36) cannot see error that both
  models share (basis, RHF, no screening, ligand charges, pose prep).
* `provisional_default` refuses unless the evidence meets a stated POLICY (>= `MIN_SYSTEMS` systems, spread within
  `MAX_SPREAD_RATIO`). The thresholds are a decision, not a measurement. Even when it returns, the result is a
  `ProvisionalFloor`: `measured` is False, its `Floor.source` begins "PROVISIONAL, NOT MEASURED", and it lists every
  system behind it. `FieldInteraction.systematic_floor` stays None; nothing here is wired into a cut by itself.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from .floor import ABSOLUTE, Floor
from .funnel import paired_delta
from .gates import charge_sensitivity
from .model import Candidate
from .run_view import QUANTITY

SCHEMA = "smeltery-floor-evidence/1"
#: Policy, not measurement: fewer systems than this cannot support a default.
MIN_SYSTEMS = 3
#: Policy, not measurement: if the largest per-system floor exceeds the smallest by more than this, the systems
#: disagree too much for one number to stand for them.
MAX_SPREAD_RATIO = 1.5


class FloorEvidenceError(ValueError):
    """The evidence cannot be computed or cannot support what was asked of it."""


@dataclass(frozen=True)
class SystemFloor:
    system: str
    models: tuple[str, str]
    n_analogues: int
    n_poses: int
    floor: float  # range of per-analogue shifts, kcal/mol
    shift_sd: float
    common_mode: float
    n_sign_flips: int
    n_pairs: int
    n_pair_order_flips: int
    max_shift_pose_sem: float  # largest per-analogue SEM (over poses) of the shift itself
    source: str
    source_sha256: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["models"] = list(self.models)
        return d


def system_floor(
    system: str,
    reference: str,
    ligands: list[str],
    dE_a: np.ndarray,
    dE_b: np.ndarray,
    models: tuple[str, str],
    *,
    source: str,
    source_sha256: str | None = None,
) -> SystemFloor:
    """The sensitivity of one system from `(n_ligands, n_poses)` dE_int under two models on the same geometries.

    `ligands` includes `reference` (the parent the ddE is taken against). Poses are paired by index.
    """
    a, b = np.asarray(dE_a, float), np.asarray(dE_b, float)
    if a.shape != b.shape or a.ndim != 2 or a.shape[0] != len(ligands):
        raise FloorEvidenceError(f"{system}: energies {a.shape} and {b.shape} do not match {len(ligands)} ligands")
    if not (np.isfinite(a).all() and np.isfinite(b).all()):
        raise FloorEvidenceError(f"{system}: non-finite energies")
    if reference not in ligands:
        raise FloorEvidenceError(f"{system}: reference {reference!r} is not among the ligands")
    analogues = [n for n in ligands if n != reference]
    if len(analogues) < 2:
        raise FloorEvidenceError(f"{system}: need at least 2 analogues besides the reference, got {len(analogues)}")

    def cands(arr: np.ndarray) -> dict[str, Candidate]:
        return {n: Candidate(n, "C", per_pose={QUANTITY: [float(v) for v in arr[i]]}) for i, n in enumerate(ligands)}

    ca, cb = cands(a), cands(b)
    ma = {n: paired_delta(ca[reference], ca[n], QUANTITY) for n in analogues}
    mb = {n: paired_delta(cb[reference], cb[n], QUANTITY) for n in analogues}
    rep = charge_sensitivity(
        [Candidate(n, "C") for n in analogues], "ddE", lambda c, q: ma[c.name].mean, lambda c, q: mb[c.name].mean
    )
    va, vb = np.array(rep.values_a), np.array(rep.values_b)
    iu = np.triu_indices(len(va), 1)
    sa, sb = np.sign(va[:, None] - va[None, :])[iu], np.sign(vb[:, None] - vb[None, :])[iu]
    k = a.shape[1]
    idx = {n: i for i, n in enumerate(ligands)}
    r = idx[reference]
    sems = [
        float(np.std((b[idx[n]] - b[r]) - (a[idx[n]] - a[r]), ddof=1) / math.sqrt(k)) if k > 1 else float("nan")
        for n in analogues
    ]
    return SystemFloor(
        system=system,
        models=models,
        n_analogues=len(analogues),
        n_poses=k,
        floor=rep.floor,
        shift_sd=rep.shift_sd,
        common_mode=rep.common_mode,
        n_sign_flips=rep.n_sign_flips,
        n_pairs=int(len(sa)),
        n_pair_order_flips=int((sa * sb < 0).sum()),
        max_shift_pose_sem=float(np.nanmax(sems)) if k > 1 else float("nan"),
        source=source,
        source_sha256=source_sha256,
    )


def measure_file(path: str | Path, models: tuple[str, str] | None = None) -> SystemFloor:
    """`system_floor` for one validated PLB results file. `models` defaults to its first two (gas-phase) models.

    Solvent (PCM) and distance-dielectric models are refused: they are treatments, not alternative charge models.
    """
    from .plb_measure import load_results

    p = Path(path)
    res = load_results(p)
    gas = [m for m in res.models if m not in res.solvent and m not in res.distance_dielectric]
    if models is None:
        if len(gas) < 2:
            raise FloorEvidenceError(f"{p.name}: needs two gas-phase charge models, found {gas}")
        models = (gas[0], gas[1])
    for m in models:
        if m not in gas:
            raise FloorEvidenceError(f"{p.name}: model {m!r} is not a gas-phase model of this file ({gas})")
    n_c = min(res.dE[m].shape[1] for m in models)  # the poses both completed: the same geometries
    return system_floor(
        res.target,
        res.reference,
        list(res.ligands),
        res.dE[models[0]][:, :n_c],
        res.dE[models[1]][:, :n_c],
        models,
        source=p.as_posix(),
        source_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    )


@dataclass(frozen=True)
class PooledFloor:
    systems: tuple[SystemFloor, ...]

    def __post_init__(self) -> None:
        if not self.systems:
            raise FloorEvidenceError("no systems to pool")
        names = [s.system for s in self.systems]
        if len(set(names)) != len(names):
            raise FloorEvidenceError(f"a system appears twice: {names}; pooling it would count one measurement twice")

    @property
    def floors(self) -> list[float]:
        return [s.floor for s in self.systems]

    @property
    def max_floor(self) -> float:
        return max(self.floors)

    @property
    def min_floor(self) -> float:
        return min(self.floors)

    @property
    def median_floor(self) -> float:
        return float(np.median(self.floors))

    @property
    def spread_ratio(self) -> float:
        return self.max_floor / self.min_floor if self.min_floor > 0 else float("inf")

    @property
    def pooled_shift_sd(self) -> float:
        """df-weighted pooled SD of the per-analogue shifts: sqrt(sum((n-1) sd^2) / sum(n-1))."""
        df = [s.n_analogues - 1 for s in self.systems]
        return math.sqrt(sum(d * s.shift_sd**2 for d, s in zip(df, self.systems, strict=True)) / sum(df))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "quantity": QUANTITY,
            "kind": ABSOLUTE,
            "unit": "kcal/mol",
            "n_systems": len(self.systems),
            "systems": [s.to_dict() for s in self.systems],
            "max_floor": self.max_floor,
            "min_floor": self.min_floor,
            "median_floor": self.median_floor,
            "spread_ratio": self.spread_ratio,
            "pooled_shift_sd": self.pooled_shift_sd,
            "caveats": [
                "each floor is a range over n analogues and grows with n; it is one draw per system",
                "one charge-model pair per system: a LOWER bound, blind to error both models share",
                "an ABSOLUTE-kind sensitivity of the paired ddE; not a difference floor",
            ],
        }

    def __str__(self) -> str:
        return render(self)


def pool(systems: list[SystemFloor]) -> PooledFloor:
    return PooledFloor(tuple(systems))


@dataclass(frozen=True)
class ProvisionalFloor:
    """A default that says it is not measured. `measured` is always False; `floor` carries the label in its source."""

    value: float
    unit: str
    evidence: PooledFloor
    rule: str
    measured: bool = field(default=False, init=False)

    @property
    def floor(self) -> Floor:
        names = ", ".join(f"{s.system} {s.floor:.3g} (n={s.n_analogues})" for s in self.evidence.systems)
        return Floor(
            value=self.value,
            unit=self.unit,
            applies_to=ABSOLUTE,
            source=(
                f"PROVISIONAL, NOT MEASURED: {self.rule} over {len(self.evidence.systems)} systems [{names}]; "
                "charge-model sensitivity only, a lower bound"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "measured": False,
            "value": self.value,
            "unit": self.unit,
            "rule": self.rule,
            "source": self.floor.source,
            "evidence": self.evidence.to_dict(),
        }


def provisional_default(
    pooled: PooledFloor, *, min_systems: int = MIN_SYSTEMS, max_spread_ratio: float = MAX_SPREAD_RATIO
) -> ProvisionalFloor:
    """A clearly labelled provisional default, or `FloorEvidenceError` saying why the evidence does not support one.

    The value is the MAX per-system floor (the conservative end). The thresholds are policy, stated here and in the
    returned `rule`; see the module docstring.
    """
    n = len(pooled.systems)
    if n < min_systems:
        raise FloorEvidenceError(
            f"no provisional default: {n} system(s) measured, policy needs at least {min_systems}. "
            f"Floors so far: {', '.join(f'{s.system} {s.floor:.3g}' for s in pooled.systems)} kcal/mol."
        )
    if pooled.spread_ratio > max_spread_ratio:
        raise FloorEvidenceError(
            f"no provisional default: the systems disagree by {pooled.spread_ratio:.2f}x (max {pooled.max_floor:.3g} "
            f"/ min {pooled.min_floor:.3g}), policy allows {max_spread_ratio:g}x. One number would hide that."
        )
    return ProvisionalFloor(
        value=pooled.max_floor,
        unit="kcal/mol",
        evidence=pooled,
        rule=f"max per-system sensitivity floor, policy >= {min_systems} systems within {max_spread_ratio:g}x",
    )


def render(pooled: PooledFloor) -> str:
    lines = ["system  models  n_analogues  n_poses  floor  shift_sd  sign_flips  pair_order_flips  max_shift_pose_sem"]
    for s in pooled.systems:
        lines.append(
            f"{s.system}  {s.models[0]}/{s.models[1]}  {s.n_analogues}  {s.n_poses}  {s.floor:.3f}  "
            f"{s.shift_sd:.3f}  {s.n_sign_flips}/{s.n_analogues}  {s.n_pair_order_flips}/{s.n_pairs}  "
            f"{s.max_shift_pose_sem:.3f}"
        )
    lines.append(
        f"pooled over {len(pooled.systems)} system(s): max {pooled.max_floor:.3f}, median {pooled.median_floor:.3f}, "
        f"min {pooled.min_floor:.3f} kcal/mol; spread {pooled.spread_ratio:.2f}x; "
        f"pooled shift sd {pooled.pooled_shift_sd:.3f}"
    )
    lines.append("each floor is a range over n analogues and one charge-model pair: a LOWER bound, one draw per system")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`smeltery floor measure RESULTS... [--models A B] [--json OUT] [--provisional]`."""
    import argparse
    import sys

    ap = argparse.ArgumentParser(prog="smeltery floor", description="FieldInteraction systematic floor evidence.")
    sub = ap.add_subparsers(dest="what", required=True)
    m = sub.add_parser("measure", help="charge-model sensitivity floor from stored PLB results files, pooled")
    m.add_argument("results", nargs="+", type=Path, help="smeltery-plb-run/1 JSON, one per system")
    m.add_argument("--models", nargs=2, metavar=("A", "B"), help="the two models to compare (default: first two)")
    m.add_argument("--json", type=Path, dest="json_out", help="write the evidence record here")
    m.add_argument(
        "--provisional", action="store_true", help="also ask for a provisional default (refused if unsupported)"
    )
    args = ap.parse_args(argv)
    try:
        pooled = pool([measure_file(p, tuple(args.models) if args.models else None) for p in args.results])
    except (FloorEvidenceError, ValueError, OSError) as e:
        print(f"smeltery floor: {e}", file=sys.stderr)
        return 2
    print(pooled)
    record: dict[str, Any] = pooled.to_dict()
    code = 0
    if args.provisional:
        try:
            prov = provisional_default(pooled)
        except FloorEvidenceError as e:
            print(f"smeltery floor: {e}", file=sys.stderr)
            record["provisional_default"] = None
            record["provisional_refused"] = str(e)
            code = 3
        else:
            print(f"PROVISIONAL (not measured): floor = {prov.value:.3g} kcal/mol -- {prov.floor.source}")
            record["provisional_default"] = prov.to_dict()
    if args.json_out:
        args.json_out.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    return code
