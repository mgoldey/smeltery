"""smeltery: a tiered, measurement-disciplined funnel for biochemical systems."""

from importlib.metadata import PackageNotFoundError, version as _version

from .funnel import (
    CutResult, IncomparableError, UnmeasuredFloorError, cut, paired_delta, require_same_formula,
    tier_floor, unpaired_delta,
)
from .gates import SensitivityReport, charge_sensitivity, spearman
from .model import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL, Candidate, Measurement, PointCharge, Pose
from .pocket import PocketField, load_pocket
from .record import RunRecord, ferric_identity

try:
    __version__ = _version("smeltery")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "__version__",
    "ANGSTROM_TO_BOHR", "HARTREE_TO_KCAL", "SensitivityReport", "charge_sensitivity", "spearman", "Candidate", "CutResult", "IncomparableError",
    "Measurement", "PocketField", "PointCharge", "Pose", "RunRecord", "UnmeasuredFloorError", "cut", "ferric_identity", "load_pocket",
    "paired_delta", "require_same_formula", "tier_floor", "unpaired_delta",
]
