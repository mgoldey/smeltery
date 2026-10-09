"""smeltery: a tiered, measurement-disciplined funnel for biochemical systems."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _version

from .funnel import (
    CutResult,
    IncomparableError,
    UnmeasuredFloorError,
    cut,
    paired_delta,
    require_same_formula,
    tier_floor,
    unpaired_delta,
)
from .gates import (
    PoseGateError,
    PoseReport,
    SensitivityReport,
    charge_sensitivity,
    check_candidate_poses,
    posebusters_check,
    require_passing_poses,
    spearman,
)
from .model import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL, Candidate, Measurement, PointCharge, Pose
from .pocket import PocketField, load_pocket
from .record import RunRecord, ferric_identity
from .scoring import Rescoring, Score, ScoringProvider, VinaScoreProvider

try:
    __version__ = _version("smeltery")
except PackageNotFoundError:  # running from a source tree that was never installed
    __version__ = "0+unknown"

__all__ = [
    "PoseGateError",
    "PoseReport",
    "check_candidate_poses",
    "posebusters_check",
    "require_passing_poses",
    "__version__",
    "Rescoring",
    "Score",
    "ScoringProvider",
    "VinaScoreProvider",
    "ANGSTROM_TO_BOHR",
    "HARTREE_TO_KCAL",
    "SensitivityReport",
    "charge_sensitivity",
    "spearman",
    "Candidate",
    "CutResult",
    "IncomparableError",
    "Measurement",
    "PocketField",
    "PointCharge",
    "Pose",
    "RunRecord",
    "UnmeasuredFloorError",
    "cut",
    "ferric_identity",
    "load_pocket",
    "paired_delta",
    "require_same_formula",
    "tier_floor",
    "unpaired_delta",
]
