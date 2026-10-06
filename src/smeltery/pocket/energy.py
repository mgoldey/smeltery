"""Single-point energies and electronic properties for an `EmbeddedLigand`: vacuum or field."""

from __future__ import annotations

from dataclasses import dataclass

from ._ferric import ferric
import numpy as np

from .embedding import EmbeddedLigand


@dataclass
class EnergyResult:
    energy: float
    converged: bool
    raw: object  # underlying ferric RhfResult/DftResult (keeps .density(), etc.)
    method: str
    field: bool  # whether point charges were actually applied


def compute_energy(
    embedded: EmbeddedLigand,
    method: str = "rhf",
    xc: str | None = None,
    use_field: bool = True,
    **scf_kwargs,
) -> EnergyResult:
    """One SCF energy for `embedded`.

    `use_field=False` forces vacuum even when a pocket is attached, so one
    `EmbeddedLigand` serves both the vacuum and the field evaluation. With no
    pocket (or every charge filtered out) the run is vacuum and `field` is False.
    `scf_kwargs` pass through to `ferric.run_rhf` / `ferric.run_dft`.
    """
    pcs = embedded.point_charges if use_field else None
    pcs = pcs or None
    if method == "rhf":
        raw = ferric.run_rhf(embedded.mol, embedded.basis_set, point_charges=pcs, **scf_kwargs)
    elif method == "dft":
        if xc is None:
            raise ValueError("method='dft' requires xc=<functional name>")
        raw = ferric.run_dft(embedded.mol, embedded.basis_set, functional=xc, point_charges=pcs, **scf_kwargs)
    else:
        raise ValueError(f"unknown method {method!r}; expected 'rhf' or 'dft'")
    return EnergyResult(
        energy=raw.energy if hasattr(raw, "energy") else raw.total_energy,
        converged=raw.converged,
        raw=raw,
        method=method,
        field=pcs is not None,
    )


def compute_charges(embedded: EmbeddedLigand, energy: EnergyResult) -> dict:
    """Hirshfeld and Lowdin partial charges for `energy`'s converged density (RHF or DFT)."""
    return {
        "hirshfeld": ferric.hirshfeld_charges(embedded.mol, embedded.basis_set, energy.raw),
        "lowdin": ferric.lowdin_charges(embedded.mol, embedded.basis_set, energy.raw),
    }


# Bundled RI-fit aux basis paired with each supported orbital basis.
_DEFAULT_RIFIT_AUXBASIS = {
    "def2-svp": "def2-svp-rifit",
    "def2-tzvp": "def2-tzvp-rifit",
    "def2-qzvp": "def2-qzvp-rifit",
    "aug-cc-pvdz": "aug-cc-pvdz-rifit",
    "aug-cc-pvtz": "aug-cc-pvtz-rifit",
}


def compute_alpha_atomic(
    embedded: EmbeddedLigand,
    energy: EnergyResult,
    auxbasis: str | None = None,
    memory_budget_gb: float | None = None,
) -> np.ndarray:
    """Per-atom Hirshfeld-partitioned static polarizability tensors (Bohr^3), shape (N, 3, 3). Closed shell only.

    Much more expensive than `compute_charges`. `auxbasis` defaults to the RI-fit
    set paired with `embedded.basis_name`; pass one explicitly for other bases.
    """
    if auxbasis is None:
        auxbasis = _DEFAULT_RIFIT_AUXBASIS.get(embedded.basis_name)
        if auxbasis is None:
            raise ValueError(f"no default RI-fit auxbasis for '{embedded.basis_name}' -- pass auxbasis explicitly")
    return ferric.hirshfeld_polarizability(
        embedded.mol, embedded.basis_set, ferric.BasisSet.bundled(auxbasis), energy.raw,
        memory_budget_gb=memory_budget_gb,
    )
