"""A charge-aware `PotentialProvider` on AIMNet2 (issue #22 (b)).

Unlike the ANI-2x backends, AIMNet2 takes the net charge as an input, so `energy_and_forces(..., charge=q)` is
a different calculation for q = -1 and q = 0. Which charges the model is *valid* for is a measurement, not a
property of the input signature: `supported_charge_states` is the set the pair/isodesmic errors of
`docs/benchmarks/aimnet2_anion.md` met the plan's bar for. AIMNet2 has no electrostatic-embedding input, so
`supports_external_charges = False`: an in-pocket request raises (`evaluate`) and is never run in vacuum.

The calculator casts its inputs to float32. On CPU this provider converts the model and its external modules to
float64 and widens the input dtypes; on CUDA the package's kernel is float32-only, so the device stays float32.
The finite-difference agreement actually reached is recorded in the benchmark doc, not assumed.

Optional: needs the `ml-potential` extra. A missing backend raises `MissingBackend` at construction.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..model import PointCharge, Pose
from ..structure import MissingBackend
from .potential import PocketContextError, PotentialResult, UnsupportedChargeStateError

#: Elements of the `aimnet2` (wB97M-D3) model, from the package's README.
AIMNET2_ELEMENTS = frozenset({"H", "B", "C", "N", "O", "F", "Si", "P", "S", "Cl", "As", "Se", "Br", "I"})
_ATOMIC_NUMBER = {
    "H": 1,
    "B": 5,
    "C": 6,
    "N": 7,
    "O": 8,
    "F": 9,
    "Si": 14,
    "P": 15,
    "S": 16,
    "Cl": 17,
    "As": 33,
    "Se": 34,
    "Br": 35,
    "I": 53,
}
EV_TO_KCAL_MOL = 23.060548  # thermochemical kcal, CODATA 2018 eV
WEIGHTS_LICENSE_SOURCE = "https://huggingface.co/isayevlab/aimnet2-wb97m-d3 (model card metadata: license: mit)"
#: Charge states whose measured errors met the plan's 5 kcal/mol bar (docs/benchmarks/aimnet2_anion.md): 0 (control
#: pair) and +1 (one ammonium pair). -1, the carboxylate's charge, is NOT in the set: the methoxide pair missed the bar.
MEASURED_CHARGE_STATES: frozenset[int] = frozenset({0, 1})


class Aimnet2:
    """AIMNet2 (wB97M-D3, ensemble member 0) energy and autograd forces, float64 where the package allows."""

    name = "aimnet2-wb97m-d3"
    supports_external_charges = False
    supported_charge_states = MEASURED_CHARGE_STATES
    license_id = "MIT"
    #: kcal/mol. Measured float round-off floor of the energy, from the benchmark doc.
    energy_noise = 1e-4

    def __init__(
        self,
        device: str = "cpu",
        model: str = "aimnet2",
        ensemble_member: int = 0,
        supported_charge_states: frozenset[int] | None = None,
    ) -> None:
        try:
            import aimnet
            import torch
            from aimnet.calculators import AIMNet2Calculator
        except ImportError as exc:
            raise MissingBackend("the AIMNet2 potential", "aimnet", "aimnet2") from exc
        self._torch = torch
        self.device = device
        self.model_name = model
        self.ensemble_member = ensemble_member
        #: Widening this beyond the measured set is for the measurement itself; it is recorded in `settings`.
        if supported_charge_states is not None:
            self.supported_charge_states = frozenset(supported_charge_states)
        calc = AIMNet2Calculator(model, device=device, ensemble_member=ensemble_member)
        # The package's CUDA convolution kernel accepts float32 only (measured: TypeError at float64), so a GPU
        # device stays float32; the precision actually used is recorded in `settings`.
        self.precision = "float32" if str(device).startswith("cuda") else "float64"
        if self.precision == "float64":
            calc.model.double()
            for attr in ("external_coulomb", "external_dftd3"):
                mod = getattr(calc, attr, None)
                if mod is not None:
                    mod.double()
            calc.keys_in = {**calc.keys_in, "coord": torch.float64, "charge": torch.float64}
        self._calc = calc
        self._versions = {"aimnet": getattr(aimnet, "__version__", "unknown"), "torch": torch.__version__}

    def energy_and_forces(
        self, pose: Pose, point_charges: Sequence[PointCharge] | None = None, charge: int = 0
    ) -> PotentialResult:
        # Direct callers bypassing `evaluate` still get no silent fallback.
        if point_charges is not None:
            raise PocketContextError(f"{self.name} cannot see external point charges")
        if charge not in self.supported_charge_states:
            raise UnsupportedChargeStateError(
                f"{self.name} supports net charges {sorted(self.supported_charge_states)}, not {charge}"
            )
        bad = sorted(set(pose.symbols) - AIMNET2_ELEMENTS)
        if bad:
            raise ValueError(f"AIMNet2 covers {sorted(AIMNET2_ELEMENTS)}; got unsupported element(s) {bad}")
        data = {
            "coord": np.asarray(pose.coords_ang, dtype=float),
            "numbers": [_ATOMIC_NUMBER[s] for s in pose.symbols],
            "charge": float(charge),
        }
        out = self._calc(data, forces=True)
        energy = float(out["energy"].detach().reshape(-1)[0]) * EV_TO_KCAL_MOL
        forces = out["forces"].detach().cpu().numpy().astype(float).reshape(-1, 3) * EV_TO_KCAL_MOL
        return PotentialResult(energy, forces)

    def settings(self) -> dict:
        return {
            "provider": self.name,
            "model": f"{self.model_name} (ensemble member {self.ensemble_member})",
            "device": self.device,
            "precision": f"{self.precision} (model and external modules)",
            "supported_charge_states": sorted(self.supported_charge_states),
            "license_id": self.license_id,
            "weights_license_source": WEIGHTS_LICENSE_SOURCE,
            **self._versions,
        }
