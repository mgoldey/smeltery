"""A `PotentialProvider` on OpenMM-ML with the ANI-2x neural-network potential (issue #22).

Optional: needs the `ml-potential` extra (`pip install 'smeltery[ml-potential]'`). Importing this
module never imports torch/openmm; a missing backend raises `MissingBackend` when the provider
is constructed, naming the extra.

What the flags say, and where each comes from (measurements are in docs/potentials.md):

* `supports_external_charges = False`. ANI-2x's inputs are species and coordinates only; it has no
  electrostatic embedding, so it cannot see pocket point charges. `evaluate` therefore refuses an
  in-pocket request (`PocketContextError`) rather than return a vacuum energy.
* `supported_charge_states = {0}`. ANI-2x was trained on neutral molecules (Devereux et al., J. Chem.
  Theory Comput. 2020, 16, 4192, doi:10.1021/acs.jctc.0c00121), and torchani's own model asserts
  "Model only supports neutral molecules" for a nonzero charge. OpenMM-ML does NOT pass that
  charge: `MLPotential.createSystem(..., charge=-1)` is accepted and ignored, so an anion silently
  gets the neutral-model answer. That is why the refusal lives here, in the provider's flags.
* `license_id = "MIT"`: the Hugging Face model card of the weights (roitberg-group/ani2x) declares
  `license: mit`, and torchani is MIT. The original ANI-2x results repository declares no licence.

openmmml 1.8's ANI path returns forces 10x too small: it differentiates with respect to positions in
Angstrom and hands kJ/mol/Angstrom to OpenMM's `PythonForce`, which reads kJ/mol/nm (its AIMNet2 and MACE
paths multiply by 10; ANI does not). Measured: provider/true force ratio 0.1000000 against torchani's own
autograd. `FORCE_SCALE_BY_OPENMMML_VERSION` applies the measured correction for the verified version only;
an unverified openmmml version raises rather than guess (pass `force_scale=` once you have measured it).

OpenMM-ML's ANI implementation computes in float32 (hard-coded in `openmmml.models.anipotential`),
which limits finite-difference force checks; see docs/potentials.md for the measured value.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ..model import PointCharge, Pose
from ..structure import MissingBackend
from .potential import PocketContextError, PotentialResult, UnsupportedChargeStateError

#: kJ/mol -> kcal/mol (thermochemical calorie, 4.184 J).
_KJ_TO_KCAL = 1.0 / 4.184

#: Elements ANI-2x was parameterized for (torchani `ANI2x` docstring: "HCNOFSCl exclusively").
ANI2X_ELEMENTS = frozenset({"H", "C", "N", "O", "F", "S", "Cl"})

#: Multiplier that turns openmmml's ANI forces into true kJ/mol/nm, per openmmml version MEASURED (docs/potentials.md).
FORCE_SCALE_BY_OPENMMML_VERSION = {"1.8": 10.0}

WEIGHTS_LICENSE_SOURCE = "https://huggingface.co/roitberg-group/ani2x (model card metadata: license: mit)"


class OpenMMMLAni2x:
    """ANI-2x energy and forces through OpenMM-ML, evaluated on the OpenMM Reference platform.

    The OpenMM `System`/`Context` for a given element sequence is built once and cached; each call
    only sets positions. `device` is the torch device for the network (default "cpu").
    """

    name = "openmmml-ani2x"
    supports_external_charges = False
    supported_charge_states = frozenset({0})
    license_id = "MIT"
    #: kcal/mol. float32 spacing at ANI total energies (~1.4e5 kcal/mol for acetic acid) is 0.0156; `relax` uses this.
    energy_noise = 0.05

    def __init__(self, device: str = "cpu", model_index: int | None = None, force_scale: float | None = None) -> None:
        try:
            import openmm
            import openmmml
            import torchani
        except ImportError as exc:
            raise MissingBackend("the ANI-2x potential", "openmmml/torchani", "ml-potential") from exc
        if force_scale is None:
            from importlib.metadata import version

            ver = version("openmmml")
            if ver not in FORCE_SCALE_BY_OPENMMML_VERSION:
                raise RuntimeError(
                    f"openmmml {ver} has not been measured: 1.8 returns ANI forces 10x too small (a unit slip), and "
                    f"other versions are unverified. Measure the provider/true force ratio, then pass force_scale=."
                )
            force_scale = FORCE_SCALE_BY_OPENMMML_VERSION[ver]
        self.force_scale = force_scale
        self._openmm = openmm
        self._mlpotential = openmmml.MLPotential("ani2x")
        self.device = device
        self.model_index = model_index
        self._versions = {"openmm": openmm.__version__, "torchani": torchani.__version__}
        self._contexts: dict[tuple[str, ...], object] = {}

    def _context(self, symbols: tuple[str, ...]):
        bad = sorted(set(symbols) - ANI2X_ELEMENTS)
        if bad:
            raise ValueError(f"ANI-2x covers {sorted(ANI2X_ELEMENTS)}; got unsupported element(s) {bad}")
        if symbols not in self._contexts:
            from openmm import app

            top = app.Topology()
            res = top.addResidue("MOL", top.addChain())
            for i, s in enumerate(symbols):
                top.addAtom(f"{s}{i}", app.Element.getBySymbol(s), res)
            kwargs = {"device": self.device}
            if self.model_index is not None:
                kwargs["modelIndex"] = self.model_index
            system = self._mlpotential.createSystem(top, **kwargs)
            ctx = self._openmm.Context(
                system, self._openmm.VerletIntegrator(1e-3), self._openmm.Platform.getPlatformByName("Reference")
            )
            self._contexts[symbols] = ctx
        return self._contexts[symbols]

    def energy_and_forces(
        self, pose: Pose, point_charges: Sequence[PointCharge] | None = None, charge: int = 0
    ) -> PotentialResult:
        # Direct callers bypassing `evaluate` still get no silent fallback.
        if point_charges is not None:
            raise PocketContextError(f"{self.name} cannot see external point charges")
        if charge != 0:
            raise UnsupportedChargeStateError(f"{self.name} supports net charge 0 only, not {charge}")
        ctx = self._context(tuple(pose.symbols))
        ctx.setPositions(np.asarray(pose.coords_ang, dtype=float) / 10.0)  # Å -> nm
        state = ctx.getState(getEnergy=True, getForces=True)
        unit = self._openmm.unit
        energy = state.getPotentialEnergy().value_in_unit(unit.kilojoule_per_mole) * _KJ_TO_KCAL
        forces = state.getForces(asNumpy=True).value_in_unit(unit.kilojoule_per_mole / unit.angstrom)
        return PotentialResult(float(energy), np.asarray(forces, dtype=float) * (self.force_scale * _KJ_TO_KCAL))

    def settings(self) -> dict:
        return {
            "provider": self.name,
            "model": "ani2x",
            "device": self.device,
            "model_index": self.model_index,
            "force_scale": self.force_scale,
            "precision": "float32 (hard-coded in openmmml.models.anipotential)",
            "license_id": self.license_id,
            "weights_license_source": WEIGHTS_LICENSE_SOURCE,
            **self._versions,
        }
