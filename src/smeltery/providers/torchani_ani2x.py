"""A float64 `PotentialProvider` on torchani's ANI-2x, without OpenMM-ML (issue #22).

`OpenMMMLAni2x` goes through OpenMM-ML, whose ANI path is hard-coded float32: a finite difference of its own
energy cannot reach `FORCE_FD_RELATIVE_BAR` (docs/potentials.md). This provider evaluates torchani's ensemble
model in float64 and takes forces by autograd, so energy and forces come from one double-precision graph and
no OpenMM unit slip is involved. The flags are the same as for the OpenMM-ML provider and for the same reasons:
`supports_external_charges = False` (species and coordinates are the only inputs) and `supported_charge_states
= {0}` (torchani asserts neutral-only).

Optional: needs the `ml-potential` extra. A missing backend raises `MissingBackend` at construction.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..model import PointCharge, Pose
from ..structure import MissingBackend
from .openmm_ml import ANI2X_ELEMENTS, WEIGHTS_LICENSE_SOURCE
from .potential import PocketContextError, PotentialResult, UnsupportedChargeStateError

_ATOMIC_NUMBER = {"H": 1, "C": 6, "N": 7, "O": 8, "F": 9, "S": 16, "Cl": 17}


class TorchaniAni2x:
    """ANI-2x (8-network ensemble mean) energy and autograd forces in float64, on `device`."""

    name = "torchani-ani2x-f64"
    supports_external_charges = False
    supported_charge_states = frozenset({0})
    license_id = "MIT"
    #: kcal/mol. float64 energies: roundoff is far below any threshold `relax` uses.
    energy_noise = 1e-8

    def __init__(self, device: str = "cpu") -> None:
        try:
            import torch
            import torchani
        except ImportError as exc:
            raise MissingBackend("the float64 ANI-2x potential", "torchani", "ml-potential") from exc
        self._torch = torch
        self.device = device
        self._model = torchani.models.ANI2x(periodic_table_index=True, dtype=torch.float64).to(device)
        self._hartree_to_kcal = float(torchani.units.hartree2kcalmol(1))
        self._versions = {"torchani": torchani.__version__, "torch": torch.__version__}

    def energy_and_forces(
        self, pose: Pose, point_charges: Sequence[PointCharge] | None = None, charge: int = 0
    ) -> PotentialResult:
        # Direct callers bypassing `evaluate` still get no silent fallback.
        if point_charges is not None:
            raise PocketContextError(f"{self.name} cannot see external point charges")
        if charge != 0:
            raise UnsupportedChargeStateError(f"{self.name} supports net charge 0 only, not {charge}")
        bad = sorted(set(pose.symbols) - ANI2X_ELEMENTS)
        if bad:
            raise ValueError(f"ANI-2x covers {sorted(ANI2X_ELEMENTS)}; got unsupported element(s) {bad}")
        torch = self._torch
        species = torch.tensor([[_ATOMIC_NUMBER[s] for s in pose.symbols]], device=self.device)
        xyz = torch.tensor(np.asarray(pose.coords_ang, dtype=float), dtype=torch.float64, device=self.device)
        xyz = xyz.unsqueeze(0).requires_grad_(True)
        energy_ha = self._model((species, xyz)).energies
        (grad,) = torch.autograd.grad(energy_ha.sum(), xyz)
        forces = -grad.squeeze(0).detach().cpu().numpy() * self._hartree_to_kcal
        return PotentialResult(float(energy_ha.item()) * self._hartree_to_kcal, np.asarray(forces, dtype=float))

    def settings(self) -> dict:
        return {
            "provider": self.name,
            "model": "ani2x (ensemble mean)",
            "device": self.device,
            "precision": "float64",
            "license_id": self.license_id,
            "weights_license_source": WEIGHTS_LICENSE_SOURCE,
            **self._versions,
        }
