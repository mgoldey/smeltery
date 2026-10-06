"""Paired ddE: hold the scaffold pose FIXED and swap only the substituent.

## The problem this addresses

Five routes to a usable substituent ranking are closed (RESULTS.md M4-M14).
Every one of them computed

    ddE = mean(E_A over ensemble_A) - mean(E_B over ensemble_B)

where `ensemble_A` and `ensemble_B` were embedded INDEPENDENTLY. The per-pose
scatter is ~28.75 kcal/mol, so that difference carries `sd*sqrt(2) = 40.66`
kcal/mol, and averaging 100 poses only gets it to **4.07** -- against
substituent effects of 1-2 kcal/mol. Hence `noise_floor` greys out every cell
of the heatmap.

**That is an UNPAIRED design.** The dominant variance is pose-conformational:
it is a property of the scaffold sitting in the pocket, and a substitution
changes a handful of atoms while ~68 others stay where they were. Variance
common to both molecules cancels in a paired difference:

    var(ddE_paired) = 2*sd^2*(1 - rho)       vs      2*sd^2 unpaired

For rho = 0.9 that is a 3.2x reduction in sd; for rho = 0.99, 10x. This module
constructs the pairing so rho can be large: pose k of the analogue is BUILT FROM
pose k of the parent, sharing the scaffold coordinates exactly.

## What "paired" means here, precisely

Not "the same random seed". ETKDG with a shared seed on two different molecular
graphs produces UNCORRELATED conformers -- the seed indexes a random stream, not
a geometry, and the graphs differ. Pairing has to be geometric: take the parent
pose, keep the MCS scaffold atoms at exactly their parent coordinates, and place
only the substituent atoms.

## THE GUARD THAT MATTERS

Pairing changes the VARIANCE of an estimator, never its EXPECTATION. If the
paired mean differs from the unpaired mean by more than sampling error, the
construction is wrong -- it is not a variance reduction, it is a different
quantity. `paired_ddE` reports both, and `PairedResult.mean_shift_is_suspicious`
flags it.

## One pairing implementation

ferric's `morph.paired` shipped its own pairing (`pair_poses_by_scaffold`:
constrained RE-embedding of the analogue, then a restrained MMFF relaxation).
smeltery's `PairedPoses` (see `smeltery.tiers`) COPIES every MCS-mapped atom
from the parent pose. The copy has the stronger anchor: pairing a molecule with
itself gives exactly 0.0 per pose, whereas the re-embedding construction charged
+13.8 kcal/mol for nothing unrestrained and +0.004 after relaxation, and its
drift check could only be a tolerance. `pair_poses_by_scaffold` and
`relax_substituent` were therefore deleted, not ported.

## Scope

This module keeps only the STATISTICS: `paired_ddE` over `PairedPose` records,
and `pairs_from_candidates`, which views the poses `PairedPoses` built as such
records. It does not score: the caller supplies an energy function. So it is
testable without xtb, DFT, or a pocket, and its exactness anchor runs in
milliseconds.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Callable, Sequence

from ..model import Candidate

__all__ = [
    "PairedPose",
    "PairedResult",
    "paired_ddE",
    "pairs_from_candidates",
]

Coords = Sequence[tuple[float, float, float]]


@dataclass
class PairedPose:
    """One pose of A and the corresponding pose of B, sharing a scaffold."""

    index: int
    symbols_a: list[str]
    coords_a: list[tuple[float, float, float]]
    symbols_b: list[str]
    coords_b: list[tuple[float, float, float]]
    #: Indices into A and B of the atoms held at identical coordinates.
    scaffold_map: list[tuple[int, int]]
    #: Largest deviation over the scaffold pairs, in Angstrom. This is the
    #: measurement that says whether the pairing is REAL. It must be ~0.
    scaffold_max_dev: float = 0.0
    error: str | None = None

    @property
    def usable(self) -> bool:
        return self.error is None


@dataclass
class PairedResult:
    """The paired and unpaired estimates side by side, so they can disagree."""

    n_pairs: int
    #: The paired per-pose differences. Their sd is the number the whole
    #: exercise is about.
    differences: list[float] = field(default_factory=list)
    ddE_paired: float = float("nan")
    sd_paired: float = float("nan")
    sem_paired: float = float("nan")
    #: What the SAME data give when the pairing is ignored -- the status quo.
    ddE_unpaired: float = float("nan")
    sd_unpaired: float = float("nan")
    sem_unpaired: float = float("nan")
    #: Pearson correlation between the paired energies. The mechanism.
    rho: float = float("nan")
    notes: list[str] = field(default_factory=list)

    @property
    def sem_ratio(self) -> float:
        """`SEM_unpaired / SEM_paired`. 1.0 = pairing bought nothing.

        **This is a STANDARD-ERROR ratio, not a variance ratio**, and it was
        called `variance_reduction` until review caught it. The corresponding
        variance reduction is its SQUARE: 2.42x on the SEM is ~5.9x on the
        variance. The old name understated the variance effect while
        overstating what was measured -- the measurement is two SEMs on the
        same data, and turning that into a variance claim takes an extra step
        the name was silently making.

        `inf` when the paired SEM is exactly zero: the pairing removed ALL the
        spread, which is a result rather than a failure to compute one.
        """
        if not math.isfinite(self.sem_unpaired):
            return float("nan")
        if self.sem_paired == 0.0:
            # The paired differences are IDENTICAL, so the pairing removed all
            # of the variance. That is a real, and the best possible, outcome --
            # the self-anchor hits it exactly. Returning NaN would report it as
            # "could not be computed" and a caller filtering on isfinite would
            # silently drop the strongest result in the set.
            return float("inf") if self.sem_unpaired > 0 else float("nan")
        if self.sem_paired < 0 or not math.isfinite(self.sem_paired):
            return float("nan")
        return self.sem_unpaired / self.sem_paired

    #: ddE of the parent paired with ITSELF through the same construction, when
    #: the caller measured it. Not None is what makes `reembedding_bias` real.
    self_anchor_ddE: float | None = None

    @property
    def reembedding_bias(self) -> float | None:
        """The self-anchor offset: what this construction charges for NOTHING.

        THE GUARD THAT ACTUALLY CATCHES THIS CONSTRUCTION'S FAILURE. Pairing the
        parent with itself must give ddE == 0: same molecule, same scaffold, no
        substitution. It does NOT, because the removed ferric `pair_poses_by_scaffold` re-embedded
        the B side, and a constrained re-embedding lands above the relaxed
        geometry it came from. MEASURED at +13.8 kcal/mol on paracetamol-like
        with MMFF (probe of 2026-09-19). `PairedPoses` copies the scaffold and
        does not re-embed it, so its self-anchor is exactly 0.0; this field
        still guards any externally built pairing.

        Subtracting it is NOT a fix: the penalty is substituent-DEPENDENT
        (F +9.5, Cl +17.2, N-methyl +31.1 above the self value), so it does not
        cancel, and it is an order of magnitude above the 1-2 kcal/mol effect.
        """
        if self.self_anchor_ddE is None or not math.isfinite(self.ddE_paired):
            return None
        return self.ddE_paired - self.self_anchor_ddE

    @property
    def mean_shift_is_suspicious(self) -> bool:
        """True when the self-anchor says this construction charges for nothing.

        AN EARLIER VERSION COMPARED `ddE_paired` AGAINST `ddE_unpaired` AND WAS
        INERT: both are `mean(E_B) - mean(E_A)` over the same data, so they are
        algebraically equal and the difference is always 0 (MEASURED: identical
        to 3 decimals in all four rows of the probe). Pairing changes the
        VARIANCE of the estimator, never its value -- so a mean shift between
        them is not merely unlikely, it is impossible, and the guard could never
        fire. The real bias is against ZERO via the self-anchor.
        """
        bias = self.reembedding_bias
        if bias is None:
            return False
        if not math.isfinite(self.sem_paired) or self.sem_paired <= 0:
            return abs(self.self_anchor_ddE or 0.0) > 0.0
        # The anchor itself carries sampling error; flag only a bias larger
        # than 2 sem of the estimate it would contaminate.
        return abs(self.self_anchor_ddE or 0.0) > 2.0 * self.sem_paired


def pairs_from_candidates(
    parent: Candidate,
    analogue: Candidate,
    scaffold_map: Sequence[tuple[int, int]],
    *,
    scaffold_tolerance: float = 0.5,
) -> list[PairedPose]:
    """View the poses `PairedPoses` built as `PairedPose` records for `paired_ddE`.

    There is ONE pairing implementation, `smeltery.tiers.PairedPoses`: mapped
    atoms are copied exactly from the parent pose, so the self-pair gives
    exactly 0.0 on every pose. This adapter only re-labels its output; it builds
    nothing. `scaffold_map` is `PairedPoses.scaffold_maps[analogue.name]`
    as (analogue atom, parent atom) pairs; it is NOT inferred from coordinates.
    Side A of the result is the parent and side B the analogue, so a ddE is
    E(analogue) - E(parent).

    `scaffold_max_dev` is MEASURED from the coordinates. A pose whose scaffold
    has drifted past `scaffold_tolerance` (Angstrom) comes back with `error`
    set rather than dropped, so the ensemble is never silently shortened.
    """
    if len(parent.poses) != len(analogue.poses):
        raise ValueError(
            f"{analogue.name}: {len(analogue.poses)} poses vs parent's {len(parent.poses)}; cannot pair"
        )
    out: list[PairedPose] = []
    for i, (pp, ap) in enumerate(zip(parent.poses, analogue.poses, strict=True)):
        pairs = [(int(p), int(a)) for a, p in scaffold_map]  # (parent atom, analogue atom)
        dev = max((math.dist(pp.coords_ang[p], ap.coords_ang[a]) for p, a in pairs), default=0.0)
        rec = PairedPose(
            i,
            list(pp.symbols),
            [tuple(float(v) for v in c) for c in pp.coords_ang],
            list(ap.symbols),
            [tuple(float(v) for v in c) for c in ap.coords_ang],
            pairs,
            scaffold_max_dev=dev,
        )
        if dev > scaffold_tolerance:
            rec.error = (
                f"scaffold drifted {dev:.3f} A from the parent pose (tolerance "
                f"{scaffold_tolerance:g}); the pairing is not real"
            )
        out.append(rec)
    return out


def paired_ddE(
    pairs: Sequence[PairedPose],
    energy: Callable[[Sequence[str], Coords], float | None],
    *,
    self_anchor_ddE: float | None = None,
) -> PairedResult:
    """Compute ddE both ways over the same poses, so they can be compared.

    `energy` returns a number per (symbols, coords), or None for a pose it could
    not score. A pair is used only when BOTH sides score -- dropping one side
    would silently unbalance the means.
    """
    usable = [p for p in pairs if p.usable]
    ea: list[float] = []
    eb: list[float] = []
    dropped = 0
    for p in usable:
        va = energy(p.symbols_a, p.coords_a)
        vb = energy(p.symbols_b, p.coords_b)
        if va is None or vb is None or not math.isfinite(va) or not math.isfinite(vb):
            dropped += 1
            continue
        ea.append(float(va))
        eb.append(float(vb))

    res = PairedResult(n_pairs=len(ea))
    if dropped:
        res.notes.append(f"{dropped} pair(s) dropped: at least one side did not score")
    failed = len(pairs) - len(usable)
    if failed:
        res.notes.append(f"{failed} pose(s) could not be paired (see PairedPose.error)")

    if len(ea) < 2:
        res.notes.append(
            f"only {len(ea)} usable pair(s); a variance needs at least 2. "
            "No estimate is reported rather than a zero-width one."
        )
        return res

    res.differences = [b - a for a, b in zip(ea, eb)]
    res.ddE_paired = statistics.fmean(res.differences)
    res.sd_paired = statistics.stdev(res.differences)
    res.sem_paired = res.sd_paired / math.sqrt(len(res.differences))

    # The UNPAIRED estimate from the SAME numbers: difference of means, with the
    # variance the independent-ensembles protocol would carry.
    ma, mb_ = statistics.fmean(ea), statistics.fmean(eb)
    sa, sb = statistics.stdev(ea), statistics.stdev(eb)
    res.ddE_unpaired = mb_ - ma
    res.sd_unpaired = math.hypot(sa, sb)
    res.sem_unpaired = res.sd_unpaired / math.sqrt(len(ea))

    # rho is the MECHANISM: the SEM ratio is 1/sqrt(1-rho) when the two
    # sds are equal, so reporting it says WHY the pairing did or did not help.
    if sa > 0 and sb > 0:
        cov = (
            statistics.fmean((a - ma) * (b - mb_) for a, b in zip(ea, eb))
            * len(ea)
            / (len(ea) - 1)
        )
        res.rho = max(-1.0, min(1.0, cov / (sa * sb)))
    else:
        res.notes.append(
            "one side has zero variance across poses, so rho is undefined "
            "(this is the self-pairing anchor, or a constant energy function)"
        )

    if self_anchor_ddE is not None:
        res.self_anchor_ddE = float(self_anchor_ddE)
        if res.mean_shift_is_suspicious:
            res.notes.append(
                f"SELF-ANCHOR IS NONZERO ({self_anchor_ddE:.3f}): pairing the "
                "parent with ITSELF through this construction charges an energy "
                "for no substitution at all. The construction re-embeds the B "
                "side, and a constrained re-embedding sits above the relaxed "
                "geometry. Subtracting it does not fix this -- the penalty is "
                "substituent-dependent, so it does not cancel in a difference."
            )
    else:
        res.notes.append(
            "no self-anchor supplied, so the re-embedding bias is UNMEASURED. "
            "Pass self_anchor_ddE=paired_ddE(<the parent paired with itself>, energy).ddE_paired -- on the one system measured it was "
            "+13.8 kcal/mol, which is larger than any substituent effect."
        )
    return res
