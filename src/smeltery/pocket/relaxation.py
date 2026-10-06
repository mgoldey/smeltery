"""Relax a ligand pose's GEOMETRY inside a pocket's fixed point-charge field.

`compute_binding_energy` is a single point at whatever geometry the xyz holds.
These wrap `ferric.run_optimize(..., point_charges=...)` and
`ferric.run_optimize_qmmm` so a pose can settle in the pocket field first.

A stalled optimization is a normal outcome for some poses, not an exception:
`converged` reports exactly what ferric reported, and the caller must check it
before trusting `energy` as a settled-pose energy.
"""

from __future__ import annotations

from dataclasses import dataclass

from ._ferric import ferric

from .embedding import EmbeddedLigand

_NEEDS_FIELD = (
    "{fn} requires embedded.charges (embed_ligand must be called with a pocket, and at least one pocket "
    "charge must survive overlap filtering) -- nothing to relax the ligand geometry against. A silent "
    "vacuum fallback would make 'in pocket' a lie; call ferric.run_optimize directly for a vacuum relaxation."
)


@dataclass
class RelaxedPose:
    """Outcome of `relax_pose_in_pocket_field`. `coords_angstrom` is the geometry at which
    `energy` (Hartree, in field) was evaluated whether or not the optimizer converged."""

    energy: float
    converged: bool
    steps: int
    coords_angstrom: list[tuple[float, float, float]]
    symbols: list[str]  # atom order is preserved by the optimizer
    n_pocket_charges: int


def relax_pose_in_pocket_field(embedded: EmbeddedLigand, max_steps: int = 100, e_conv: float = 1e-6) -> RelaxedPose:
    """Optimize the ligand geometry in its (fixed) pocket field via `ferric.run_optimize`."""
    if not embedded.charges:
        raise ValueError(_NEEDS_FIELD.format(fn="relax_pose_in_pocket_field"))
    result = ferric.run_optimize(
        embedded.mol, embedded.basis_name, max_steps=max_steps, e_conv=e_conv,
        point_charges=embedded.point_charges,
    )
    return RelaxedPose(
        energy=result.energy,
        converged=result.converged,
        steps=result.steps,
        coords_angstrom=[tuple(c) for c in result.mol().coords()],
        symbols=list(embedded.symbols),
        n_pocket_charges=len(embedded.charges),
    )


@dataclass
class RelaxedPoseQmmm:
    """Outcome of `relax_pose_in_pocket`. `coords_angstrom` is the ligand's geometry only
    (pocket sites are never QM), converged or not."""

    energy: float
    converged: bool
    steps: int
    coords_angstrom: list[tuple[float, float, float]]
    symbols: list[str]
    n_pocket_charges: int


def relax_pose_in_pocket(
    embedded: EmbeddedLigand,
    move_mm: str | tuple[str, float] | tuple[str, list[int]] = "none",
    mm_topology=None,
    max_steps: int = 100,
    e_conv: float = 1e-6,
) -> RelaxedPoseQmmm:
    """Optimize the ligand via `ferric.run_optimize_qmmm`: ligand atoms are the QM region,
    the overlap-filtered pocket charges the MM region (bare-charge sites, symbol "X").

    `move_mm="none"` keeps the pocket fixed. Otherwise `mm_topology` (a
    `ferric.MmTopology` over ligand atoms first, then pocket charges) is required, and
    a pocket need not be attached. Assumes a neutral singlet ligand.
    """
    if move_mm == "none" and not embedded.charges:
        raise ValueError(_NEEDS_FIELD.format(fn="relax_pose_in_pocket"))
    pocket = embedded.charges or []
    n_lig = len(embedded.symbols)
    system = ferric.QmmmSystem(
        list(embedded.symbols) + ["X"] * len(pocket),
        list(embedded.coords_angstrom) + [tuple(c.xyz_ang) for c in pocket],
        [0.0] * n_lig + [c.q for c in pocket],
        qm_indices=list(range(n_lig)),
    )
    result = ferric.run_optimize_qmmm(
        system, embedded.basis_name, move_mm=move_mm, mm_topology=mm_topology,
        max_steps=max_steps, e_conv=e_conv,
    )
    coords = result.system().qm_molecule().coords()[:n_lig]
    return RelaxedPoseQmmm(
        energy=result.energy,
        converged=result.converged,
        steps=result.steps,
        coords_angstrom=[tuple(c) for c in coords],
        symbols=list(embedded.symbols),
        n_pocket_charges=len(pocket),
    )
