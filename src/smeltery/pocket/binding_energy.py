"""Ligand-in-pocket electrostatic binding energy: field-vs-vacuum QM.

    E_int = E(ligand, QM, embedded in the pocket's point-charge field)
          - E(ligand, QM, vacuum)

The pocket is always classical point charges; only the ligand is a QM
`Molecule`, so there is no supermolecular interaction energy and no BSSE.
The stages are reusable on their own (load the pocket once, embed per
geometry): `load_pocket` -> `embed_ligand` -> `compute_energy` (twice) ->
`compute_charges`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..model import HARTREE_TO_KCAL
from .embedding import embed_ligand
from .energy import compute_charges, compute_energy
from .loader import PocketField, load_pocket

# Measured on the danuglipron campaign (experiments/danuglipron/RESULTS.md, M4-M14),
# in ferric. The best protocol for a ddE from a pose ensemble (average over n poses)
# has SEM*sqrt(2) = 4.07 kcal/mol, against substituent effects of 1-2 kcal/mol.
# Exposed as data, not only prose, so a caller (e.g. a heatmap `noise_floor=`) can use it.
DDE_NOISE_FLOOR_KCAL_MOL = 4.07
# This call does not rank analogues. A machine-readable form of the docstring's prohibition.
RANKS_ANALOGUES = False


def _available_gb() -> float | None:
    """MemAvailable from /proc/meminfo in GB, or None where that file does not exist."""
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / (1024**2)
    except (OSError, ValueError, IndexError):
        pass
    return None


def check_available_memory(min_available_gb: float) -> None:
    """Refuse to launch an SCF job when free memory is below `min_available_gb`.

    ferric's Fock builders do not budget against system RAM (OPENBLAS_NUM_THREADS=1
    limits threads, not peak working set), so a job on a pressured box can be
    OOM-killed. A pre-flight guard, not a cap. Where free memory cannot be read
    (no /proc/meminfo) the check is skipped, not failed.
    """
    available = _available_gb()
    if available is not None and available < min_available_gb:
        raise MemoryError(
            f"Only {available:.1f} GB available (need >= {min_available_gb:.1f} GB). "
            "Free up memory or lower min_available_gb before running this job."
        )


@dataclass
class BindingEnergyResult:
    e_vacuum: float  # Hartree, SCF 1 (no field)
    e_field: float  # Hartree, SCF 2 (with the pocket field)
    delta_e_hartree: float
    delta_e_kcal_mol: float
    charges_vacuum: dict
    charges_field: dict
    n_pocket_charges: int  # charges actually applied, after ligand-overlap filtering
    # ferric's `converged` for each SCF. Both are True unless `require_converged=False` was passed,
    # in which case a False here means that energy (and delta_e) is not an SCF solution.
    vacuum_converged: bool
    field_converged: bool
    noise_floor_kcal_mol: float = DDE_NOISE_FLOOR_KCAL_MOL
    ranks_analogues: bool = RANKS_ANALOGUES


def compute_binding_energy(
    ligand_xyz: str | Path,
    pocket: str | Path | PocketField,
    basis: str = "def2-svp",
    method: str = "rhf",
    xc: str | None = None,
    ff: str = "AMBER",
    min_available_gb: float = 2.0,
    df_j_aux: str | None = None,
    df_k_aux: str | None = None,
    max_iter: int | None = None,
    energy_conv: float | None = None,
    density_conv: float | None = None,
    require_converged: bool = True,
) -> BindingEnergyResult:
    """Field-vs-vacuum electrostatic binding energy for a ligand in a pocket, with
    Hirshfeld/Lowdin charges in both states. Two SCFs, reported separately as
    `e_vacuum` and `e_field`.

    `pocket` is a `PocketField` or a .pqr/.pdb path (loaded with NO cutoff: every
    charge is kept; truncation is not monotone, so pass a pre-cut `PocketField`
    from `load_pocket(..., cutoff_ang=, center_ang=)` if you want one). A .pdb needs
    `pdb2pqr30` on PATH. Raises MemoryError up front if fewer than
    `min_available_gb` GB are free (0 disables the check).

    An unconverged SCF raises RuntimeError (the same rule the funnel tiers apply),
    because a delta_e built from a non-stationary energy is not a binding energy.
    `require_converged=False` returns instead, with `vacuum_converged` /
    `field_converged` saying which SCF did not converge; check them before using
    the numbers. `max_iter` / `energy_conv` / `density_conv` pass through to both
    SCFs; ferric's `run_rhf` default `max_iter` is 100, too few for some ligands
    (smeltery #110), so raising it is the usual fix for that error.

    `df_j_aux` / `df_k_aux` pass through to `ferric.run_rhf` / `run_dft` (both
    SCFs). The default `None` leaves ferric's own choice: exact J/K for
    `method="rhf"`; for `method="dft"`, RI-J (def2-universal-jkfit), plus RI-K for
    hybrids only. A DFT caller asks for exact J/K with `df_j_aux=""` /
    `df_k_aux=""` (ferric's opt-out spelling; `None` means "ferric's default", not
    "off"). For RHF on a drug-sized ligand the exact J/K build dominates the cost;
    passing `"def2-universal-jkfit"` for both is much faster but adds the usual RI
    fitting error, which is why it is not the default here.

    WHAT THIS NUMBER CANNOT DO: RANK ANALOGUES.

    The value is well defined and reproducible FOR ONE POSE. The pose is the
    problem. MEASURED on the danuglipron campaign (`experiments/danuglipron/
    RESULTS.md`, M4-M14), five protocols for getting a ddE out of a pose
    ensemble were tried and all five closed:

        average over n poses    SEM*sqrt(2) = 4.07 kcal/mol   16x the gap
        select one pose (M13)   sd*sqrt(2)  = 40.66           163x
        a real pose search      1% improvement, 32.5x short
        a different scorer      none available is less pose-sensitive
        a better tier           the error is in the ENSEMBLE, not the SCF

    Substituent effects are 1-2 kcal/mol, so the BEST available ddE noise (the
    4.07 kcal/mol floor, `DDE_NOISE_FLOOR_KCAL_MOL`) is ~2-4x the signal. No
    amount of tier-4 DFT fixes this: it is pose variance, not electronic-
    structure error, and averaging more poses only buys sqrt(n).

    So: quote this for ONE ligand in ONE pose, or as a component of a larger
    model. Do not order two analogues by it (`RANKS_ANALOGUES` is False, and
    the result carries `noise_floor_kcal_mol`).
    """
    if min_available_gb > 0:
        check_available_memory(min_available_gb)

    field = pocket if isinstance(pocket, PocketField) else load_pocket(pocket, ff=ff)
    embedded = embed_ligand(ligand_xyz, pocket=field, basis=basis)

    # `is not None`, not truthiness: "" is a real value (exact J/K), not "unset".
    passthrough = {
        "df_j_aux": df_j_aux,
        "df_k_aux": df_k_aux,
        "max_iter": max_iter,
        "energy_conv": energy_conv,
        "density_conv": density_conv,
    }
    scf_kwargs = {k: v for k, v in passthrough.items() if v is not None}
    e_vac = compute_energy(embedded, method=method, xc=xc, use_field=False, **scf_kwargs)
    e_field = compute_energy(embedded, method=method, xc=xc, use_field=True, **scf_kwargs)
    if require_converged and not (e_vac.converged and e_field.converged):
        failed = [name for name, e in (("vacuum", e_vac), ("field", e_field)) if not e.converged]
        raise RuntimeError(
            f"{ligand_xyz}: {' and '.join(failed)} SCF did not converge (ferric's run_rhf default max_iter is 100; "
            "pass max_iter=..., or require_converged=False to inspect)"
        )
    charges_vacuum = compute_charges(embedded, e_vac)
    charges_field = compute_charges(embedded, e_field)

    delta_e = e_field.energy - e_vac.energy
    return BindingEnergyResult(
        e_vacuum=e_vac.energy,
        e_field=e_field.energy,
        delta_e_hartree=delta_e,
        delta_e_kcal_mol=delta_e * HARTREE_TO_KCAL,
        charges_vacuum=charges_vacuum,
        charges_field=charges_field,
        n_pocket_charges=len(embedded.charges or []),
        vacuum_converged=e_vac.converged,
        field_converged=e_field.converged,
    )
