"""Potential providers: an energy-and-forces surface a pose can be relaxed on.

A `PotentialProvider` returns energy and forces for a pose. Two flags make its
limits structural instead of documented-and-forgotten:

* `supports_external_charges`: can the potential SEE the pocket's point charges?
  A provider that cannot, asked for an in-pocket result, would silently return a
  vacuum answer and call it "in pocket" (the ferric #324 failure class).
  `evaluate` refuses that request with `PocketContextError`; it never falls back.
* `supported_charge_states`: the net charges the potential is valid for. The wrong
  ionization state has already cost ~143 kcal/mol in this project, so an
  unsupported state raises `UnsupportedChargeStateError`; it is never run anyway.

Units are the project's: energy in kcal/mol, coordinates in Å, forces in
kcal/mol/Å (force = minus the gradient of the energy).

`relax` minimizes on a COPY of the pose's coordinates and returns them; it never
writes into the `Pose` it was given, so a downstream tier scores the pose it
was handed (the ferric #325 defect, same rule as `smeltery.tiers.ForceField`).

`check_forces` is the finite-difference validation every provider should pass:
its bar, `FORCE_FD_RELATIVE_BAR`, is recorded here, not chosen per test.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np

from ..model import PointCharge, Pose

#: Largest acceptable ||F_fd - F|| / ||F|| for a provider's own forces vs a finite difference of its energy.
FORCE_FD_RELATIVE_BAR = 1e-4


class PocketContextError(RuntimeError):
    """An in-pocket result was requested from a potential that cannot see external charges."""


class UnsupportedChargeStateError(ValueError):
    """The net charge requested is outside the potential's supported charge states."""


@dataclass(frozen=True)
class PotentialResult:
    energy: float  # kcal/mol
    forces: np.ndarray  # (n_atoms, 3), kcal/mol/Å


@runtime_checkable
class PotentialProvider(Protocol):
    name: str
    supports_external_charges: bool
    supported_charge_states: frozenset[int]

    def energy_and_forces(
        self, pose: Pose, point_charges: Sequence[PointCharge] | None = None, charge: int = 0
    ) -> PotentialResult:
        """Energy and forces at `pose`. `point_charges` is None for vacuum; a sequence (even empty)
        is an in-pocket request. Callers go through `evaluate`, which enforces both flags."""
        ...

    def settings(self) -> dict: ...


def evaluate(
    provider: PotentialProvider, pose: Pose, point_charges: Sequence[PointCharge] | None = None, charge: int = 0
) -> PotentialResult:
    """`provider.energy_and_forces`, after refusing what the provider's flags say it cannot do."""
    if point_charges is not None and not provider.supports_external_charges:
        raise PocketContextError(
            f"potential {provider.name!r} has supports_external_charges=False: it cannot see the pocket, so an "
            "in-pocket result would be a vacuum result. Use a provider that supports external charges, or pass "
            "point_charges=None to ask for vacuum explicitly."
        )
    if charge not in provider.supported_charge_states:
        raise UnsupportedChargeStateError(
            f"potential {provider.name!r} supports net charges {sorted(provider.supported_charge_states)}, not {charge}"
        )
    result = provider.energy_and_forces(pose, point_charges, charge)
    if result.forces.shape != (len(pose.symbols), 3):
        raise ValueError(f"potential {provider.name!r} returned forces of shape {result.forces.shape}")
    return result


def _with_coords(pose: Pose, xyz: np.ndarray) -> Pose:
    return Pose(pose.symbols, np.asarray(xyz, dtype=float).reshape(-1, 3))


@dataclass(frozen=True)
class RelaxResult:
    coords_ang: np.ndarray
    energy: float
    converged: bool
    steps: int
    max_force: float  # kcal/mol/Å, largest force component at `coords_ang`


def relax(
    provider: PotentialProvider,
    pose: Pose,
    point_charges: Sequence[PointCharge] | None = None,
    charge: int = 0,
    fmax: float = 1e-8,
    max_steps: int = 500,
) -> RelaxResult:
    """BFGS minimization of `provider`'s energy, on a copy. `converged` is False if `max_steps` ran out.

    Convergence is the largest force component below `fmax`, so a positional error is about
    `fmax / k` for a locally harmonic potential of curvature k. The input `pose` is not modified.
    """
    x = np.array(pose.coords_ang, dtype=float).ravel()
    res = evaluate(provider, _with_coords(pose, x), point_charges, charge)
    g = -res.forces.ravel()
    h = np.eye(x.size)
    for step in range(max_steps):
        if np.abs(g).max() < fmax:
            return RelaxResult(x.reshape(-1, 3), res.energy, True, step, float(np.abs(g).max()))
        d = -h @ g
        if d @ g >= 0:  # not a descent direction: reset the curvature estimate
            h, d = np.eye(x.size), -g
        t = 1.0
        while True:  # Armijo backtracking
            x_new = x + t * d
            new = evaluate(provider, _with_coords(pose, x_new), point_charges, charge)
            if new.energy <= res.energy + 1e-4 * t * (g @ d) or t < 1e-12:
                break
            t *= 0.5
        g_new = -new.forces.ravel()
        s, y = x_new - x, g_new - g
        sy = s @ y
        if sy > 1e-12:
            rho = 1.0 / sy
            i = np.eye(x.size)
            h = (i - rho * np.outer(s, y)) @ h @ (i - rho * np.outer(y, s)) + rho * np.outer(s, s)
        x, g, res = x_new, g_new, new
    return RelaxResult(x.reshape(-1, 3), res.energy, False, max_steps, float(np.abs(g).max()))


def check_forces(
    provider: PotentialProvider,
    pose: Pose,
    point_charges: Sequence[PointCharge] | None = None,
    charge: int = 0,
    step_ang: float = 1e-4,
) -> float:
    """||F_fd - F|| / ||F||, with F_fd a central finite difference of the provider's own energy.

    Compare to `FORCE_FD_RELATIVE_BAR`. Undefined where ||F|| is 0 (a stationary point), so
    evaluate it at a displaced geometry; that case raises instead of returning a meaningless number.
    """
    x0 = np.array(pose.coords_ang, dtype=float).ravel()
    analytic = evaluate(provider, pose, point_charges, charge).forces.ravel()
    norm = float(np.linalg.norm(analytic))
    if norm == 0.0:
        raise ValueError("forces are exactly zero here; check them at a displaced geometry")
    fd = np.empty_like(x0)
    for i in range(x0.size):
        up, dn = x0.copy(), x0.copy()
        up[i] += step_ang
        dn[i] -= step_ang
        e_up = evaluate(provider, _with_coords(pose, up), point_charges, charge).energy
        e_dn = evaluate(provider, _with_coords(pose, dn), point_charges, charge).energy
        fd[i] = -(e_up - e_dn) / (2 * step_ang)
    return float(np.linalg.norm(fd - analytic) / norm)
