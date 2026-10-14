"""Compare smeltery's paired ddE with the experimental PLB ddG: loader, validator and trend statistics.

This is the analysis half of the measurement in `docs/benchmarks/plb_plan.md` (issues #27 and #29, part 1). It
reads ONE committed results file (a `RunRecord` JSON written by `scripts/run_plb_ddE.py assemble`), refuses it
unless its provenance is complete and its numbers are self-consistent, and computes every statistic the plan
declared, from the per-pose values alone. Nothing here runs quantum chemistry.

Why trend statistics and not pairwise ranking: the benchmark's own power analysis says no pair in cdk2's series
differs by the 4.07 kcal/mol noise floor, so the right question is whether ddE tracks ddG ACROSS ligands,
with a confidence interval and a permutation null. Conventions: x = experimental ddG (kcal/mol, lower = tighter),
y = smeltery's mean paired ddE (kcal/mol, lower = more favourable pocket interaction); both are differences from
the same parent ligand, so a faithful ddE is POSITIVELY correlated with ddG. n counts ligands, never poses.

No SciPy: ranks, Kendall tau-b, the exact permutation null and the bootstrap are written out (and tested against
SciPy where it is installed). Every random choice takes an explicit seed.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from itertools import combinations, permutations
from pathlib import Path
from typing import Any

import numpy as np

from .funnel import paired_delta, resolved, unpaired_delta
from .gates import charge_sensitivity, spearman
from .model import ANGSTROM_TO_BOHR, HARTREE_TO_KCAL, Candidate, Measurement
from .pocket import DDE_NOISE_FLOOR_KCAL_MOL

SCHEMA = "smeltery-plb-run/1"
QUANTITY = "dE_int"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
HEX40 = re.compile(r"^[0-9a-f]{40}$")
# The 8 provenance keys every results file must carry in `inputs` (the plan names each).
REQUIRED_INPUTS = (
    "target",
    "reference",
    "pivot_ang",
    "n_poses",
    "n_poses_by_model",
    "excluded_ligands",
    "pose_indices",
    "dropped_poses",
    "seed",
    "jitter_deg",
    "jitter_ang",
    "basis",
    "cutoff_ang",
    "models",
    "field_provenance",
    "prep_provenance",
    "smeltery_commit",
    "pose_geometry_sha256",
)
SPEARMAN_STATS = ("spearman", "kendall", "pearson", "slope")


class PlbResultsError(ValueError):
    """A results file is incomplete, inconsistent with itself, or does not recompute."""


# ------------------------------------------------------------------------------------------------ loading


@dataclass(frozen=True)
class PlbResults:
    target: str
    reference: str
    models: tuple[str, ...]
    ligands: tuple[str, ...]  # reference first
    n_poses: int  # the design's poses per ligand
    n_poses_by_model: dict[str, int]  # poses actually completed for every ligand, per model (a prefix 0..n-1)
    dE: dict[str, np.ndarray]  # model -> (n_ligands, n_poses), kcal/mol
    min_dist_ang: dict[str, np.ndarray]  # model -> (n_ligands, n_poses)
    inputs: dict[str, Any]
    ferric: dict[str, Any]
    total_cpu_s: float
    # model -> implicit-solvent setting (a dielectric constant or a name) of the models run with IEF-PCM; the others are
    # gas phase. A solvent model's reference leg is the ligand alone in the same solvent (`E_alone_hartree`).
    solvent: dict[str, Any] = field(default_factory=dict)
    # model -> slope s (1/Angstrom) of the distance-dependent dielectric eps(d) = max(1, s d) on that model's field,
    # d being each charge's distance to the nearest atom of the scored ligand pose (`distance_dielectric_field`).
    distance_dielectric: dict[str, float] = field(default_factory=dict)

    @property
    def analogues(self) -> tuple[str, ...]:
        return tuple(n for n in self.ligands if n != self.reference)


def _need(d: dict, key: str, where: str):
    if key not in d or d[key] is None or d[key] == "":
        raise PlbResultsError(f"{where}: missing or empty {key!r}")
    return d[key]


def _array(raw: dict, ligands: list[str], n_poses: int, where: str) -> np.ndarray:
    if sorted(raw) != sorted(ligands):
        raise PlbResultsError(f"{where}: ligands {sorted(raw)} differ from {sorted(ligands)}")
    try:
        a = np.array([raw[n] for n in ligands], dtype=float)
    except (ValueError, TypeError) as e:
        raise PlbResultsError(f"{where}: ragged or non-numeric values ({e})") from e
    if a.shape != (len(ligands), n_poses):
        raise PlbResultsError(f"{where}: shape {a.shape}, expected {(len(ligands), n_poses)}")
    if not np.isfinite(a).all():
        raise PlbResultsError(f"{where}: non-finite values")
    return a


def validate_results(rec: dict) -> PlbResults:
    """Parse a RunRecord dict strictly; raise `PlbResultsError` on anything incomplete or inconsistent."""
    from .record import digest

    for k in ("campaign", "inputs", "tiers", "results", "ferric", "smeltery_version", "input_digest"):
        _need(rec, k, "record")
    if digest({"inputs": rec["inputs"], "tiers": rec["tiers"]}) != rec["input_digest"]:
        raise PlbResultsError("input_digest does not match the inputs and tiers (the file was edited)")
    inp, res, fer = rec["inputs"], rec["results"], rec["ferric"]
    if res.get("schema") != SCHEMA:
        raise PlbResultsError(f"results.schema is {res.get('schema')!r}, expected {SCHEMA!r}")
    for k in REQUIRED_INPUTS:
        _need(inp, k, "inputs")
    if not HEX40.match(str(inp["smeltery_commit"])):
        raise PlbResultsError("inputs.smeltery_commit is not a 40-hex git commit")
    if not (fer.get("version") and str(fer.get("provenance", "")).startswith(("VERIFIED", "INFERRED"))):
        raise PlbResultsError(f"ferric identity is not recorded with a provenance label: {fer}")
    models = tuple(inp["models"])
    if not models or len(set(models)) != len(models):
        raise PlbResultsError("inputs.models must be a non-empty list of distinct names")
    n_poses = int(inp["n_poses"])
    if n_poses < 2:
        raise PlbResultsError("n_poses < 2: no pose SEM exists")
    for m in models:
        fp = _need(inp["field_provenance"], m, "inputs.field_provenance")
        pp = _need(inp["prep_provenance"], m, "inputs.prep_provenance")
        for k in ("input_sha256", "cutoff_ang", "center_ang", "n_charges"):
            _need(fp, k, f"field_provenance[{m}]")
        if not HEX64.match(fp["input_sha256"]):
            raise PlbResultsError(f"field_provenance[{m}].input_sha256 is not a sha256")
        if pp.get("output_sha256") != fp["input_sha256"]:
            raise PlbResultsError(f"{m}: the PQR the SCFs used is not the PQR the prep tool wrote (sha256 differs)")
        for k in ("input_sha256", "openmm_version", "force_field_files", "deleted_residues_min_distance_to_center_ang"):
            _need(pp, k, f"prep_provenance[{m}]")
        if float(fp["cutoff_ang"]) != float(inp["cutoff_ang"]):
            raise PlbResultsError(f"{m}: field cutoff differs from inputs.cutoff_ang")
    pidx = inp["pose_indices"]
    for m in models:
        if len(pidx.get(m, ())) != int(inp["n_poses_by_model"].get(m, -1)) or len(set(pidx[m])) != len(pidx[m]):
            raise PlbResultsError(f"pose_indices[{m}] does not match n_poses_by_model")
        if set(pidx[m]) & {int(k) for k in inp["dropped_poses"]}:
            raise PlbResultsError(f"{m} uses a pose that was dropped")
    undeclared = {r["pose"] for r in res.get("dropped_failures", [])} - {int(k) for k in inp["dropped_poses"]}
    if undeclared:
        raise PlbResultsError(f"failures at poses {sorted(undeclared)} were dropped without a declared reason")
    if res.get("failures"):
        raise PlbResultsError(f"{len(res['failures'])} unresolved SCF failures are recorded; the run is incomplete")
    ligands = list(res["E_vac_hartree"])
    if inp["reference"] not in ligands:
        raise PlbResultsError("the reference ligand is not among the results")
    ligands = [inp["reference"], *sorted(n for n in ligands if n != inp["reference"])]
    if len(ligands) < 3:
        raise PlbResultsError("fewer than 3 ligands")
    if sorted(inp["pose_geometry_sha256"]) != sorted(ligands):
        raise PlbResultsError("pose_geometry_sha256 does not cover exactly the ligands")
    dE, dist = {}, {}
    npm = _need(inp, "n_poses_by_model", "inputs")
    if sorted(npm) != sorted(models):
        raise PlbResultsError("inputs.n_poses_by_model must name exactly the models")
    n_vac = len(next(iter(res["E_vac_hartree"].values())))
    if n_vac > n_poses or n_vac < max(int(v) for v in npm.values()):
        raise PlbResultsError("E_vac_hartree does not cover every pose of every model")
    e_vac = _array(res["E_vac_hartree"], ligands, n_vac, "E_vac_hartree")
    solvent = {m: v for m, v in (inp.get("field_solvent") or {}).items() if v is not None}
    if set(solvent) - set(models):
        raise PlbResultsError(f"field_solvent names models that were not run: {sorted(set(solvent) - set(models))}")
    for m, v in solvent.items():
        ok = (isinstance(v, str) and v.strip() != "") or (
            isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v > 1.0
        )
        if not ok:
            raise PlbResultsError(f"{m}: solvent {v!r} is neither a name nor a finite dielectric constant above 1")
    ddd = {m: float(v) for m, v in (inp.get("field_distance_dielectric") or {}).items() if v is not None}
    if set(ddd) - set(models):
        raise PlbResultsError(
            f"field_distance_dielectric names models that were not run: {sorted(set(ddd) - set(models))}"
        )
    for m, v in ddd.items():
        if not (math.isfinite(v) and v > 0) or m in solvent:
            raise PlbResultsError(
                f"{m}: distance-dielectric slope {v!r} must be positive and finite, and not with a solvent"
            )
    for m in models:
        if inp["field_provenance"][m].get("distance_dielectric_slope") != (ddd.get(m)):
            raise PlbResultsError(f"{m}: field_provenance and inputs.field_distance_dielectric disagree on the slope")
    for m in models:
        recorded = inp["field_provenance"][m].get("solvent")
        if recorded != solvent.get(m):
            raise PlbResultsError(
                f"{m}: field_provenance records solvent {recorded!r}, inputs.field_solvent {solvent.get(m)!r}"
            )
    for m in models:
        n_m = int(npm[m])
        if not 2 <= n_m <= n_poses:
            raise PlbResultsError(f"{m}: {n_m} completed poses (need 2 to {n_poses})")
        dE[m] = _array(res["dE_int_kcal_mol"][m], ligands, n_m, f"dE_int_kcal_mol[{m}]")
        e_fld = _array(res["E_field_hartree"][m], ligands, n_m, f"E_field_hartree[{m}]")
        if m in solvent:  # reference leg: the ligand alone in the same solvent, never the gas-phase vacuum energy
            alone = res.get("E_alone_hartree", {})
            if m not in alone:
                raise PlbResultsError(
                    f"{m}: a solvent model needs E_alone_hartree[{m}] (the ligand alone in the solvent)"
                )
            e_ref = _array(alone[m], ligands, n_m, f"E_alone_hartree[{m}]")
            leg = "E_alone(solvent)"
        else:
            e_ref, leg = e_vac[:, :n_m], "E_vac"
        if not np.allclose(dE[m], (e_fld - e_ref) * HARTREE_TO_KCAL, rtol=0, atol=1e-6):
            raise PlbResultsError(f"{m}: dE_int does not equal (E_field - {leg}) * {HARTREE_TO_KCAL} kcal/mol/Eh")
        dist[m] = _array(res["min_dist_to_field_ang"][m], ligands, n_m, f"min_dist_to_field_ang[{m}]")
        if (dist[m] <= 0).any():
            raise PlbResultsError(f"{m}: a ligand atom coincides with a field charge")
    return PlbResults(
        target=str(inp["target"]),
        reference=str(inp["reference"]),
        models=models,
        ligands=tuple(ligands),
        n_poses=n_poses,
        n_poses_by_model={m: int(npm[m]) for m in models},
        dE=dE,
        min_dist_ang=dist,
        inputs=inp,
        ferric=fer,
        total_cpu_s=float(res.get("total_cpu_s", math.nan)),
        solvent=solvent,
        distance_dielectric=ddd,
    )


def load_results(path: str | Path) -> PlbResults:
    try:
        rec = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise PlbResultsError(f"cannot read {path}: {e}") from e
    return validate_results(rec)


# ------------------------------------------------------------------------------------------------ statistics


def _avg_ranks(a: np.ndarray) -> np.ndarray:
    """Average ranks (1-based) of a 1-D array; ties share the mean rank."""
    a = np.asarray(a, dtype=float)
    order = np.argsort(a, kind="stable")
    ranks = np.empty(len(a))
    sa = a[order]
    i = 0
    while i < len(a):
        j = i
        while j + 1 < len(a) and sa[j + 1] == sa[i]:
            j += 1
        ranks[order[i : j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def pearson(x, y) -> float:
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    if np.ptp(x) == 0 or np.ptp(y) == 0:
        return float("nan")
    return float(np.corrcoef(x, y)[0, 1])


def kendall_tau_b(x, y) -> float:
    """Kendall tau-b (ties corrected); nan if either side is constant."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    iu = np.triu_indices(len(x), 1)
    sx = np.sign(x[:, None] - x[None, :])[iu]
    sy = np.sign(y[:, None] - y[None, :])[iu]
    n0 = len(sx)
    den = math.sqrt((n0 - (sx == 0).sum()) * (n0 - (sy == 0).sum()))
    return float((sx * sy).sum() / den) if den > 0 else float("nan")


def ols(x, y) -> dict[str, float]:
    """y = intercept + slope * x. rmse is the residual RMS with n - 2 degrees of freedom."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    sxx = float(((x - x.mean()) ** 2).sum())
    if sxx == 0:
        return {"slope": float("nan"), "intercept": float("nan"), "rmse": float("nan")}
    slope = float(((x - x.mean()) * (y - y.mean())).sum() / sxx)
    icpt = float(y.mean() - slope * x.mean())
    resid = y - (icpt + slope * x)
    dof = len(x) - 2
    return {
        "slope": slope,
        "intercept": icpt,
        "rmse": float(math.sqrt((resid**2).sum() / dof)) if dof > 0 else float("nan"),
    }


def point_stats(x, y) -> dict[str, float]:
    """All trend statistics of y against x. x = experiment, y = computed."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    fit = ols(x, y)
    return {
        "spearman": spearman(x, y),
        "kendall": kendall_tau_b(x, y),
        "pearson": pearson(x, y),
        "slope": fit["slope"],
        "intercept": fit["intercept"],
        "rmse_fit": fit["rmse"],
        "mae_raw": float(np.abs(y - x).mean()),
        "sign_agreement": float((np.sign(x) == np.sign(y)).mean()),
        "n_sign_agree": int((np.sign(x) == np.sign(y)).sum()),
        "sd_ratio": float(y.std(ddof=1) / x.std(ddof=1)) if x.std(ddof=1) > 0 else float("nan"),
        "n": int(len(x)),
    }


def permutation_matrix(n: int, *, max_exact: int = 9, n_mc: int = 200_000, seed: int = 0) -> tuple[np.ndarray, bool]:
    """All n! permutations when n <= max_exact (exact), else n_mc seeded random ones (Monte Carlo)."""
    if n <= max_exact:
        return np.array(list(permutations(range(n))), dtype=np.int8), True
    rng = np.random.default_rng(seed)
    return np.array([rng.permutation(n) for _ in range(n_mc)], dtype=np.int16), False


def permutation_null(x, y, *, max_exact: int = 9, n_mc: int = 200_000, seed: int = 0) -> dict[str, Any]:
    """Null distribution of spearman, kendall, pearson, slope and sign-agreement count under label shuffling.

    The labels (x, the experimental ddG) are permuted across ligands, so the null is "ddE carries no information
    about WHICH ligand is which". Exact for n <= max_exact, Monte Carlo (seeded) above. p-values count permutations
    at least as extreme as the observed statistic (the identity permutation included), two-sided on |stat| and
    one-sided in the pre-declared positive direction.
    """
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    n = len(x)
    perms, exact = permutation_matrix(n, max_exact=max_exact, n_mc=n_mc, seed=seed)
    xp = x[perms]  # (N, n)
    rx, ry = _avg_ranks(x)[perms], _avg_ranks(y)
    iu = np.triu_indices(n, 1)
    sy = np.sign(y[:, None] - y[None, :])[iu]
    sxp = np.sign(xp[:, iu[0]] - xp[:, iu[1]])  # (N, pairs)
    n0 = len(sy)
    den_k = np.sqrt((n0 - (sxp == 0).sum(1)) * (n0 - (sy == 0).sum()))

    def corr(a, b):  # row-wise Pearson of a (N, n) with b (n,)
        ac, bc = a - a.mean(1, keepdims=True), b - b.mean()
        den = np.sqrt((ac**2).sum(1) * (bc**2).sum())
        with np.errstate(invalid="ignore", divide="ignore"):
            return (ac * bc).sum(1) / den

    xc = x - x.mean()
    stats = {
        "spearman": corr(rx, ry),
        "kendall": np.where(den_k > 0, (sxp * sy).sum(1) / np.where(den_k > 0, den_k, 1), np.nan),
        "pearson": corr(xp, y),
        "slope": ((xp - xp.mean(1, keepdims=True)) * (y - y.mean())).sum(1) / (xc**2).sum(),
        "n_sign_agree": (np.sign(xp) == np.sign(y)).sum(1).astype(float),
    }
    obs = point_stats(x, y)
    out: dict[str, Any] = {"exact": exact, "n_permutations": int(len(perms))}
    for k, v in stats.items():
        o = obs[k]
        tol = 1e-9
        out[k] = {
            "observed": o,
            "p_two_sided": float(np.mean(np.abs(v) >= abs(o) - tol)) if k != "n_sign_agree" else None,
            "p_one_sided_positive": float(np.mean(v >= o - tol)),
            "null_sd": float(np.nanstd(v)),
            "null_quantiles": {q: float(np.nanquantile(v, q)) for q in (0.025, 0.5, 0.975)},
        }
    if exact:
        absrho = np.abs(stats["spearman"])
        vals = np.unique(np.round(absrho, 12))[::-1]
        crit = None
        for c in vals:  # smallest |rho| whose two-sided exact p is <= 0.05
            if np.mean(absrho >= c - 1e-9) <= 0.05:
                crit = float(c)
        out["spearman_critical_abs_two_sided_0.05"] = crit
    return out


def bootstrap_ci(
    x, y, stat: str, *, n_boot: int = 10_000, seed: int = 0, level: float = 0.95
) -> dict[str, float | int | None]:
    """Percentile CI of one `point_stats` entry, resampling LIGANDS (pairs (x_i, y_i)) with replacement."""
    x, y = np.asarray(x, dtype=float), np.asarray(y, dtype=float)
    rng = np.random.default_rng(seed)
    n = len(x)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, n, size=n)
        xs, ys = x[i], y[i]
        if np.ptp(xs) == 0 or np.ptp(ys) == 0:
            continue
        vals.append(_one_stat(stat, xs, ys))
    v = np.array([u for u in vals if np.isfinite(u)])
    a = (1 - level) / 2
    return {
        "lo": float(np.quantile(v, a)) if len(v) else None,
        "hi": float(np.quantile(v, 1 - a)) if len(v) else None,
        "n_valid": int(len(v)),
        "n_boot": n_boot,
    }


def _one_stat(stat: str, x: np.ndarray, y: np.ndarray) -> float:
    if stat == "spearman":
        return spearman(x, y)
    if stat == "kendall":
        return kendall_tau_b(x, y)
    if stat == "pearson":
        return pearson(x, y)
    if stat == "slope":
        return ols(x, y)["slope"]
    if stat == "rmse_fit":
        return ols(x, y)["rmse"]
    if stat == "mae_raw":
        return float(np.abs(y - x).mean())
    if stat == "sign_agreement":
        return float((np.sign(x) == np.sign(y)).mean())
    raise KeyError(stat)


def binomial_two_sided(k: int, n: int, p: float = 0.5) -> float:
    """Exact two-sided binomial p-value (sum of outcomes no more likely than k)."""
    if n == 0:
        return float("nan")
    pm = [math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(n + 1)]
    return float(min(1.0, sum(q for q in pm if q <= pm[k] * (1 + 1e-9))))


def power_simulation(n: int, rho_pearson_grid, *, n_sim: int = 4000, seed: int = 0, alpha: float = 0.05) -> dict:
    """Power of the exact two-sided Spearman permutation test at n ligands, for a bivariate-normal truth.

    Uses the exact null critical |rho| of this n. Ligand-level only: it assumes the n ddE values are the TRUE
    per-ligand means; measurement noise in ddE (see `reliability`) lowers the power further.
    """
    perms, _ = permutation_matrix(n, max_exact=9, n_mc=100_000, seed=seed)
    base = np.arange(1, n + 1, dtype=float)
    rb = base - base.mean()
    rho_null = (rb[perms] * rb).sum(1) / (rb**2).sum()
    absn = np.abs(rho_null)
    crit = min(c for c in np.unique(np.round(absn, 12)) if np.mean(absn >= c - 1e-9) <= alpha)
    rng = np.random.default_rng(seed + 1)
    out = {}
    for r in rho_pearson_grid:
        hits = 0
        for _ in range(n_sim):
            u = rng.normal(size=n)
            v = r * u + math.sqrt(1 - r * r) * rng.normal(size=n)
            hits += abs(spearman(u, v)) >= crit - 1e-9
        out[f"{r:.2f}"] = hits / n_sim
    return {"critical_abs_spearman": float(crit), "n": n, "n_sim": n_sim, "power_by_true_pearson_r": out}


# ------------------------------------------------------------------------------------------------ analysis


def _candidates(res: PlbResults, model: str, dE: np.ndarray | None = None) -> dict[str, Candidate]:
    arr = res.dE[model] if dE is None else dE
    return {n: Candidate(n, "C", per_pose={QUANTITY: [float(v) for v in arr[i]]}) for i, n in enumerate(res.ligands)}


def paired_ddE(res: PlbResults, model: str, dE: np.ndarray | None = None) -> dict[str, Measurement]:
    """Paired ddE of every analogue against the parent, through the funnel's own `paired_delta`."""
    c = _candidates(res, model, dE)
    return {n: paired_delta(c[res.reference], c[n], QUANTITY) for n in res.analogues}


def descriptors(smiles_by_name: dict[str, str]) -> dict[str, dict[str, float]]:
    """Trivial baselines: heavy-atom count and Crippen logP, from the manifest's heavy-atom SMILES."""
    from rdkit import Chem
    from rdkit.Chem import Crippen

    out: dict[str, dict[str, float]] = {}
    for n, s in smiles_by_name.items():
        m = Chem.MolFromSmiles(s)
        out[n] = {"heavy_atoms": float(m.GetNumHeavyAtoms()), "crippen_logp": float(Crippen.MolLogP(m))}
    return out


def _stat_block(x, y, seed: int, n_boot: int) -> dict[str, Any]:
    pts = point_stats(x, y)
    null = permutation_null(x, y, seed=seed)
    return {
        "point": pts,
        "bootstrap_ci_over_ligands": {
            s: bootstrap_ci(x, y, s, n_boot=n_boot, seed=seed + 1 + i)
            for i, s in enumerate(("spearman", "kendall", "pearson", "slope", "rmse_fit", "mae_raw", "sign_agreement"))
        },
        "permutation": null,
        "sign_binomial_two_sided_p": binomial_two_sided(pts["n_sign_agree"], pts["n"]),
    }


def _pose_bootstrap(res: PlbResults, model: str, x: np.ndarray, *, n_boot: int, seed: int, ligands: bool) -> dict:
    """CI of each trend statistic when POSES are resampled (the same pose indices for every ligand, so the pairing
    is kept), and, with `ligands=True`, ligands too. Measures how much the pose sampling alone moves the result."""
    rng = np.random.default_rng(seed)
    n_l = len(res.analogues)
    vals: dict[str, list[float]] = {s: [] for s in ("spearman", "kendall", "slope")}
    for _ in range(n_boot):
        n_m = res.dE[model].shape[1]
        k = rng.integers(0, n_m, size=n_m)
        y = np.array([m.mean for m in paired_ddE(res, model, res.dE[model][:, k]).values()])
        xs = x
        if ligands:
            i = rng.integers(0, n_l, size=n_l)
            xs, y = x[i], y[i]
        if np.ptp(xs) == 0 or np.ptp(y) == 0:
            continue
        for s in vals:
            vals[s].append(_one_stat(s, xs, y))
    return {
        s: {"lo": float(np.nanquantile(v, 0.025)), "hi": float(np.nanquantile(v, 0.975)), "n_valid": len(v)}
        for s, v in vals.items()
    }


def pair_resolution(res: PlbResults, model: str, ddg: dict[str, float], sigma: dict[str, float | None], floors: dict):
    """Every pair of the series: is it resolved by ddE (z=2, the funnel's `resolved`), and in the right order?"""
    zero = Measurement(0.0, 0.0, res.dE[model].shape[1], ())
    rows = []
    for (i, a), (j, b) in combinations(list(enumerate(res.ligands)), 2):
        d = res.dE[model][j] - res.dE[model][i]  # pose-by-pose difference: the pair is paired by pose index
        m = Measurement.from_samples(d)
        exp = ddg[b] - ddg[a]
        sa, sb = sigma[a], sigma[b]
        exp_sigma = None if sa is None or sb is None else math.hypot(sa, sb)
        rows.append(
            {
                "a": a,
                "b": b,
                "dde_mean": m.mean,
                "dde_sem": m.sem,
                "exp_ddg_diff": exp,
                "exp_sigma": exp_sigma,
                "correct_sign": bool(np.sign(m.mean) == np.sign(exp)),
                **{f"resolved@{k}": bool(resolved(m, zero, 2.0, f)) for k, f in floors.items()},
            }
        )
    summary: dict[str, Any] = {"n_pairs": len(rows)}
    for k in floors:
        r = [x for x in rows if x[f"resolved@{k}"]]
        ok = sum(x["correct_sign"] for x in r)
        summary[k] = {
            "floor_kcal_mol": floors[k],
            "n_resolved": len(r),
            "n_resolved_correct_order": ok,
            "n_resolved_wrong_order": len(r) - ok,
            "binomial_two_sided_p": binomial_two_sided(ok, len(r)) if r else None,
        }
    summary["n_pairs_exp_resolved_2sigma"] = sum(
        1 for x in rows if x["exp_sigma"] is not None and abs(x["exp_ddg_diff"]) > 2 * x["exp_sigma"]
    )
    summary["n_pairs_exp_diff_ge_noise_floor"] = sum(
        1 for x in rows if abs(x["exp_ddg_diff"]) >= DDE_NOISE_FLOOR_KCAL_MOL
    )
    summary["n_pairs_correct_sign_all"] = sum(x["correct_sign"] for x in rows)
    return rows, summary


def charge_model_comparison(res: PlbResults, model_a: str, model_b: str, *, n_boot: int, seed: int) -> dict[str, Any]:
    """The same geometries under two pocket charge models, through `gates.charge_sensitivity`."""
    n_c = min(res.dE[model_a].shape[1], res.dE[model_b].shape[1])  # the poses BOTH models completed: same geometries
    da, db = res.dE[model_a][:, :n_c], res.dE[model_b][:, :n_c]
    ma, mb = paired_ddE(res, model_a, da), paired_ddE(res, model_b, db)
    cands = [Candidate(n, "C") for n in res.analogues]
    rep = charge_sensitivity(cands, "ddE", lambda c, q: ma[c.name].mean, lambda c, q: mb[c.name].mean)
    a, b = np.array(rep.values_a), np.array(rep.values_b)
    iu = np.triu_indices(len(a), 1)
    sa, sb = np.sign(a[:, None] - a[None, :])[iu], np.sign(b[:, None] - b[None, :])[iu]
    return {
        "models": [model_a, model_b],
        "n": rep.n,
        "n_sign_flips_of_ddE": rep.n_sign_flips,
        "spearman_between_models": rep.spearman,
        "spearman_between_models_bootstrap_ci": bootstrap_ci(a, b, "spearman", n_boot=n_boot, seed=seed),
        "kendall_between_models": kendall_tau_b(a, b),
        "n_pairs": int(len(sa)),
        "n_pairs_order_flipped": int((sa * sb < 0).sum()),
        "n_pairs_order_agreed": int((sa * sb > 0).sum()),
        "charge_sensitivity_floor_kcal_mol": rep.floor,
        "deltas_b_minus_a": rep.deltas,
        "values_a": dict(zip(rep.names, rep.values_a)),
        "values_b": dict(zip(rep.names, rep.values_b)),
        "raw_dE_int_spearman_across_ligand_pose": spearman(da.ravel(), db.ravel()),
        "raw_dE_int_mean_shift_b_minus_a_kcal_mol": float((db - da).mean()),
        "raw_dE_int_sd_of_shift_kcal_mol": float((db - da).std(ddof=1)),
        "n_poses_used": n_c,
        "paired_sem_b_over_a": {n: (mb[n].sem / ma[n].sem if ma[n].sem > 0 else float("nan")) for n in res.analogues},
    }


def distance_dielectric_field(charges_bohr, ligand_coords_ang, slope: float) -> list[tuple[float, float, float, float]]:
    """Divide each point charge (q, x, y, z in Bohr) by eps(d) = max(1, slope * d), d in Angstrom the distance of the
    charge to the NEAREST atom of `ligand_coords_ang` (an (n, 3) array). Positions are untouched. A heuristic charge
    scaling, not a reaction-field model: see docs/benchmarks/plb_ddd_plan.md section 1."""
    if not (math.isfinite(slope) and slope > 0):
        raise ValueError(f"slope must be positive and finite, got {slope}")
    lig = np.asarray(ligand_coords_ang, dtype=float)
    out = []
    for q, x, y, z in charges_bohr:
        xyz_ang = np.array([x, y, z]) / ANGSTROM_TO_BOHR
        d = float(np.sqrt(((lig - xyz_ang) ** 2).sum(-1)).min())
        out.append((q / max(1.0, slope * d), x, y, z))
    return out


def dielectric_family(res: PlbResults) -> dict[str, float]:
    """model -> relative permittivity, for the models of ONE force field whose field provenance has `dielectric_eps`.

    Empty when the results carry no screening treatment (the cdk2 file). Refuses a family without exactly one
    unscreened (eps = 1) model, an eps <= 0, or models that do not share a force field and a PQR: a screened model
    is the unscreened field with its charges divided by eps, nothing else.
    """
    fam = {
        m: float(res.inputs["field_provenance"][m]["dielectric_eps"])
        for m in res.models
        if "dielectric_eps" in res.inputs["field_provenance"][m]
        and m not in res.solvent  # PCM is not a charge divisor
        and m not in res.distance_dielectric  # a distance-dependent dielectric is not a uniform divisor
    }
    if not fam:
        return {}
    if any(not v > 0 for v in fam.values()):
        raise PlbResultsError(f"dielectric_eps must be positive: {fam}")
    # A model on its OWN PQR (another force field, e.g. CHARMM36 beside AMBER) at eps = 1 is a second charge model, not
    # a member of a screening family: it is left out. Everything else must form ONE family on one PQR.
    by_pqr: dict[str, list[str]] = {}
    for m in fam:
        by_pqr.setdefault(res.inputs["prep_provenance"][m]["output_sha256"], []).append(m)
    groups = [g for g in by_pqr.values() if len(g) > 1 or fam[g[0]] != 1.0]
    if not groups:
        return {}
    if len(groups) > 1:
        raise PlbResultsError(f"screened models must share one PQR (one force field, one crop): {fam}")
    fam = {m: fam[m] for m in groups[0]}
    if sorted(v for v in fam.values() if v == 1.0) != [1.0]:
        raise PlbResultsError(f"a screening family needs exactly one unscreened (eps = 1) model: {fam}")
    return fam


def screening_analysis(res: PlbResults, x: np.ndarray, *, n_boot: int, seed: int) -> dict[str, Any]:
    """The pre-declared screening analysis (issue #28): the same ligands, poses and field, charges divided by eps.

    Every eps is compared with the unscreened field on the poses ALL models of the family completed (the same
    geometries). For each eps: the trend statistics of ddE against ddG (Spearman with its permutation p, slope with
    a bootstrap CI over ligands, sd ratio), how far the raw interaction energy moved against the first-order
    expectation 1/eps, the rank agreement with the unscreened ddE, and sign flips of ddE and of dE_int.
    """
    fam = dielectric_family(res)
    if not fam:
        return {}
    base = next(m for m, v in fam.items() if v == 1.0)
    n_c = min(res.dE[m].shape[1] for m in fam)
    names = list(res.analogues)
    ref_dE = res.dE[base][:, :n_c]
    ref_y = np.array([m.mean for m in paired_ddE(res, base, ref_dE).values()])
    ref_slope = point_stats(x, ref_y)["slope"]
    rows: dict[str, Any] = {}
    for i, (m, e) in enumerate(sorted(fam.items(), key=lambda kv: kv[1])):
        dE = res.dE[m][:, :n_c]
        pm_ = paired_ddE(res, m, dE)
        y = np.array([pm_[n].mean for n in names])
        pts = point_stats(x, y)
        null = permutation_null(x, y, seed=seed + 10 * i)
        ratio = float(dE.mean() / ref_dE.mean())
        rows[f"{e:g}"] = {
            "model": m,
            "eps": e,
            "n_poses": n_c,
            "point": pts,
            "spearman_p_two_sided": null["spearman"]["p_two_sided"],
            "permutation_exact": null["exact"],
            "bootstrap_ci_over_ligands": {
                s_: bootstrap_ci(x, y, s_, n_boot=n_boot, seed=seed + 100 * (i + 1) + j)
                for j, s_ in enumerate(("spearman", "slope"))
            },
            "ddE_mean": dict(zip(names, (float(v) for v in y), strict=True)),
            "mean_dE_int_kcal_mol": float(dE.mean()),
            "mean_dE_int_ratio_to_unscreened": ratio,
            "first_order_expectation_1_over_eps": 1.0 / e,
            "slope_measured_over_first_order": pts["slope"] / (ref_slope / e) if ref_slope else None,
            "spearman_with_unscreened_ddE": spearman(ref_y, y),
            "n_ddE_sign_flips_vs_unscreened": int((np.sign(y) != np.sign(ref_y)).sum()),
            "n_dE_int_sign_flips_vs_unscreened": int((np.sign(dE) != np.sign(ref_dE)).sum()),
            "n_dE_int_values": int(dE.size),
        }
    # eps at which the fitted slope crosses 1: log-log interpolation between the bracketing eps values, descriptive only
    pts_ = [(r_["eps"], r_["point"]["slope"]) for r_ in rows.values()]
    pts_.sort()
    crossing = None
    for (e0, s0), (e1, s1) in zip(pts_, pts_[1:], strict=False):
        if s0 > 1.0 >= s1 > 0:
            t = (math.log(s0) - 0.0) / (math.log(s0) - math.log(s1))
            crossing = float(math.exp(math.log(e0) + t * (math.log(e1) - math.log(e0))))
            break
    return {
        "base_model": base,
        "eps_values": sorted(fam.values()),
        "n_poses_common": n_c,
        "by_eps": rows,
        "slope_crosses_1_at_eps": crossing,
    }


# ---- implicit solvation (issue #28, docs/benchmarks/plb_pcm_plan.md): thresholds and labels declared in advance

RANK_SHIFT_THRESHOLD = 0.15  # |Delta-rho| the uniform-eps lane called a change (about 20 adjacent swaps at n = 12)
SPREAD_NARROWS_BELOW, SPREAD_WIDENS_ABOVE = 0.8, 1.25


def rank_change_label(delta: float, lo: float | None, hi: float | None) -> str:
    """The plan's P1 label for Delta-rho = rho(solvent) - rho(gas) and its paired-bootstrap 95% CI."""
    excludes_zero = lo is not None and hi is not None and (lo > 0 or hi < 0)
    if delta >= RANK_SHIFT_THRESHOLD:
        return "improves" if excludes_zero else "shift of at least 0.15 not distinguishable from zero"
    if delta <= -RANK_SHIFT_THRESHOLD:
        return "worsens" if excludes_zero else "shift of at least 0.15 not distinguishable from zero"
    return "unchanged at the level this study can detect"


def tracks_label(rho: float, p_two_sided: float, ci_lo: float | None, kendall: float) -> str:
    """The tyk2 plan's Q1 criteria (also the PCM plan's P2): rho > 0, p < 0.05, CI excluding 0, tau-b > 0."""
    if rho > 0 and p_two_sided < 0.05 and ci_lo is not None and ci_lo > 0 and kendall > 0:
        return "tracks (weakly)"
    if rho < 0 and p_two_sided < 0.05:
        return "anti-correlated"
    return "does not track at the level detectable"


def spread_label(ratio: float) -> str:
    if ratio < SPREAD_NARROWS_BELOW:
        return "narrows the spread"
    if ratio > SPREAD_WIDENS_ABOVE:
        return "widens the spread"
    return "about the same spread"


def paired_delta_spearman_ci(x, y_a, y_b, *, n_boot: int, seed: int, level: float = 0.95) -> dict[str, Any]:
    """Percentile CI of rho(x, y_a) - rho(x, y_b), resampling LIGANDS (the same resample for both correlations)."""
    x, ya, yb = (np.asarray(v, dtype=float) for v in (x, y_a, y_b))
    rng = np.random.default_rng(seed)
    n = len(x)
    vals = []
    for _ in range(n_boot):
        i = rng.integers(0, n, size=n)
        if np.ptp(x[i]) == 0 or np.ptp(ya[i]) == 0 or np.ptp(yb[i]) == 0:
            continue
        d = spearman(x[i], ya[i]) - spearman(x[i], yb[i])
        if np.isfinite(d):
            vals.append(d)
    v = np.array(vals)
    a = (1 - level) / 2
    return {
        "lo": float(np.quantile(v, a)) if len(v) else None,
        "hi": float(np.quantile(v, 1 - a)) if len(v) else None,
        "n_valid": int(len(v)),
        "n_boot": n_boot,
    }


def _vs_gas(
    res: PlbResults, model: str, base: str, n_c: int, x: np.ndarray, *, n_boot: int, seed: int
) -> dict[str, Any]:
    """One solvent model against the gas-phase model on the first `n_c` poses both completed (the same geometries)."""
    names = list(res.analogues)
    d_s, d_g = res.dE[model][:, :n_c], res.dE[base][:, :n_c]
    y_s = np.array([m.mean for m in paired_ddE(res, model, d_s).values()])
    y_g = np.array([m.mean for m in paired_ddE(res, base, d_g).values()])
    pts, pts_g = point_stats(x, y_s), point_stats(x, y_g)
    null = permutation_null(x, y_s, seed=seed)
    ci = {s_: bootstrap_ci(x, y_s, s_, n_boot=n_boot, seed=seed + 1 + j) for j, s_ in enumerate(("spearman", "slope"))}
    delta = paired_delta_spearman_ci(x, y_s, y_g, n_boot=n_boot, seed=seed + 50)
    d_rho = pts["spearman"] - pts_g["spearman"]
    sems = np.array([m.sem for m in paired_ddE(res, model, d_s).values()])
    ratio_sd = pts["sd_ratio"] / pts_g["sd_ratio"] if pts_g["sd_ratio"] else float("nan")
    return {
        "model": model,
        "solvent": res.solvent.get(model),
        "distance_dielectric_slope": res.distance_dielectric.get(model),
        "gas_model": base,
        "n_poses": n_c,
        "pose_indices": list(res.inputs["pose_indices"][model][:n_c]),
        "point": pts,
        "gas_point": pts_g,
        "spearman_p_two_sided": null["spearman"]["p_two_sided"],
        "kendall_p_two_sided": null["kendall"]["p_two_sided"],
        "permutation_exact": null["exact"],
        "bootstrap_ci_over_ligands": ci,
        "ddE_mean": dict(zip(names, (float(v) for v in y_s), strict=True)),
        "gas_ddE_mean": dict(zip(names, (float(v) for v in y_g), strict=True)),
        "delta_spearman_vs_gas": d_rho,
        "delta_spearman_paired_bootstrap_ci": delta,
        "spearman_with_gas_ddE": spearman(y_g, y_s),
        "mean_paired_sem": float(sems.mean()),
        "mean_dE_int_kcal_mol": float(d_s.mean()),
        "gas_mean_dE_int_kcal_mol": float(d_g.mean()),
        "mean_dE_int_ratio_to_gas": float(d_s.mean() / d_g.mean()),
        "sd_ratio_over_gas_sd_ratio": ratio_sd,
        "n_ddE_sign_flips_vs_gas": int((np.sign(y_s) != np.sign(y_g)).sum()),
        "n_dE_int_sign_flips_vs_gas": int((np.sign(d_s) != np.sign(d_g)).sum()),
        "n_dE_int_values": int(d_s.size),
        "labels": {
            "rank_change": rank_change_label(d_rho, delta["lo"], delta["hi"]),
            "tracks": tracks_label(
                pts["spearman"], null["spearman"]["p_two_sided"], ci["spearman"]["lo"], pts["kendall"]
            ),
            "spread": spread_label(ratio_sd),
        },
    }


def solvent_analysis(res: PlbResults, x: np.ndarray, *, n_boot: int, seed: int) -> dict[str, Any]:
    """The pre-declared implicit-solvent analysis: every solvent model against the ONE gas-phase model on the poses
    both completed, and (when there are several solvent models) every solvent model on the poses ALL of them completed.
    Empty when the results carry no solvent model. Refuses a solvent model that is not the gas model's field."""
    if not res.solvent:
        return {}
    gas = [m for m in res.models if m not in res.solvent and m not in res.distance_dielectric]
    if len(gas) != 1:
        raise PlbResultsError(f"a solvent comparison needs exactly one gas-phase model, found {gas}")
    base = gas[0]
    pqr = res.inputs["prep_provenance"][base]["output_sha256"]
    for m in res.solvent:
        if res.inputs["prep_provenance"][m]["output_sha256"] != pqr:
            raise PlbResultsError(f"{m} does not use the gas-phase model's field (PQR sha256 differs)")
        if float(res.inputs["field_provenance"][m].get("dielectric_eps", 1.0)) != 1.0:
            raise PlbResultsError(f"{m}: a solvent model must not also divide the field's charges")
    solvent_models = [m for m in res.models if m in res.solvent]
    n_all = min(res.dE[m].shape[1] for m in [base, *solvent_models])
    out: dict[str, Any] = {"gas_model": base, "n_poses_common_all": n_all, "by_model": {}, "on_common_poses_of_all": {}}
    for i, m in enumerate(solvent_models):
        n_c = min(res.dE[m].shape[1], res.dE[base].shape[1])
        out["by_model"][m] = _vs_gas(res, m, base, n_c, x, n_boot=n_boot, seed=seed + 1000 * (i + 1))
        if len(solvent_models) > 1:
            out["on_common_poses_of_all"][m] = _vs_gas(
                res, m, base, n_all, x, n_boot=n_boot, seed=seed + 1000 * (i + 1) + 500
            )
    return out


def distance_dielectric_analysis(res: PlbResults, x: np.ndarray, *, n_boot: int, seed: int) -> dict[str, Any]:
    """The pre-declared distance-dielectric analysis (issue #28): every such model against the ONE unscreened model on
    the poses both completed. Empty when the results carry none. Refuses a model that is not the unscreened model's
    field (PQR sha256), or that also divides the charges uniformly or sits in a solvent."""
    if not res.distance_dielectric:
        return {}
    base = [m for m in res.models if m not in res.solvent and m not in res.distance_dielectric]
    if len(base) != 1:
        raise PlbResultsError(f"a distance-dielectric comparison needs exactly one unscreened model, found {base}")
    base = base[0]
    pqr = res.inputs["prep_provenance"][base]["output_sha256"]
    out: dict[str, Any] = {"unscreened_model": base, "by_model": {}}
    for i, m in enumerate(res.models):
        if m not in res.distance_dielectric:
            continue
        if res.inputs["prep_provenance"][m]["output_sha256"] != pqr:
            raise PlbResultsError(f"{m} does not use the unscreened model's field (PQR sha256 differs)")
        if m in res.solvent or float(res.inputs["field_provenance"][m].get("dielectric_eps", 1.0)) != 1.0:
            raise PlbResultsError(f"{m}: a distance-dielectric model must not also be a solvent or a uniform divisor")
        n_c = min(res.dE[m].shape[1], res.dE[base].shape[1])
        out["by_model"][m] = _vs_gas(res, m, base, n_c, x, n_boot=n_boot, seed=seed + 1000 * (i + 1))
    return out


def _source_ids(lg) -> frozenset[str] | None:
    """The publication ids of a manifest ligand's measurement (None where the object carries none)."""
    m = getattr(lg, "measurement", None)
    return None if m is None else frozenset(src.id for src in m.sources)


def analyse(res: PlbResults, target, *, seed: int = 20261010, n_boot: int = 10_000) -> dict[str, Any]:
    """Every statistic the plan declared, for every model, from the per-pose values and the manifest alone.

    `target` is a `smeltery.benchmark.Target` (experimental ddG and sigma); its reference must be the run's.
    """
    if target.reference != res.reference:
        raise PlbResultsError(f"manifest reference {target.reference!r} != run reference {res.reference!r}")
    names = list(res.analogues)
    excluded = dict(res.inputs["excluded_ligands"])
    if res.reference in excluded:
        raise PlbResultsError("the reference ligand cannot be excluded")
    kept = [lg for lg in target.ligands if lg.name not in excluded]
    if sorted(lg.name for lg in kept) != sorted(res.ligands):
        raise PlbResultsError(
            "the results' ligands are not the manifest target's ligands minus the declared exclusions "
            f"{sorted(excluded)}"
        )
    sources = {lg.name: _source_ids(lg) for lg in kept}
    ddg = {lg.name: lg.ddg_kcal_mol for lg in kept}
    sigma = {lg.name: lg.ddg_sigma_kcal_mol for lg in kept}
    x = np.array([ddg[n] for n in names])
    smiles = {lg.name: lg.smiles for lg in kept}
    desc = descriptors(smiles)
    out: dict[str, Any] = {
        "seed": seed,
        "n_boot": n_boot,
        "n_ligands_analogues": len(names),
        "excluded_ligands": excluded,
        "n_poses": res.n_poses,
        "n_poses_by_model": dict(res.n_poses_by_model),
        "exp_ddg": {n: ddg[n] for n in res.ligands},
        "exp_sigma": {n: sigma[n] for n in res.ligands},
        "power": power_simulation(len(names), [0.3, 0.5, 0.7, 0.8, 0.9], seed=seed),
        "baselines": {},
        "models": {},
    }
    for di, key in enumerate(("heavy_atoms", "crippen_logp")):
        d = np.array([desc[n][key] for n in names])
        out["baselines"][key] = {
            "values": {n: desc[n][key] for n in names},
            **_stat_block(x, d, seed + 100 + di, n_boot),
        }
    for mi, model in enumerate(res.models):
        pm = paired_ddE(res, model)
        y = np.array([pm[n].mean for n in names])
        c = _candidates(res, model)
        unp = {n: unpaired_delta(c[res.reference], c[n], QUANTITY) for n in names}
        sems = np.array([pm[n].sem for n in names])
        var_between = float(y.var(ddof=1) - (sems**2).mean())
        block = _stat_block(x, y, seed + 200 + 10 * mi, n_boot)
        block["per_ligand"] = {
            n: {
                "ddE_mean": pm[n].mean,
                "ddE_paired_sem": pm[n].sem,
                "ddE_unpaired_sem": unp[n].sem,
                "sd_over_poses": float(np.std(pm[n].per_pose, ddof=1)),
                "per_pose_ddE": list(pm[n].per_pose),
            }
            for n in names
        }
        block["pose_noise"] = {
            "mean_paired_sem": float(sems.mean()),
            "median_paired_sem": float(np.median(sems)),
            "mean_unpaired_sem": float(np.mean([unp[n].sem for n in names])),
            "sd_of_ddE_across_ligands": float(y.std(ddof=1)),
            "sd_of_exp_ddg_across_ligands": float(x.std(ddof=1)),
            "reliability_of_ddE_ranking": (var_between / float(y.var(ddof=1))) if var_between > 0 else 0.0,
            "var_between_ligands_after_removing_pose_noise": var_between,
        }
        block["pose_bootstrap_ci"] = _pose_bootstrap(res, model, x, n_boot=2000, seed=seed + 300 + mi, ligands=False)
        block["joint_pose_and_ligand_bootstrap_ci"] = _pose_bootstrap(
            res, model, x, n_boot=2000, seed=seed + 400 + mi, ligands=True
        )
        # pre-declared secondary: drop every pose index at which ANY ligand has an atom within 2 A of a field charge
        bad = np.where((res.min_dist_ang[model] < 2.0).any(axis=0))[0]
        keep = np.array([k for k in range(res.dE[model].shape[1]) if k not in set(bad.tolist())], dtype=int)
        sec: dict[str, Any] = {"dropped_pose_indices": bad.tolist(), "n_poses_kept": int(len(keep))}
        if len(keep) >= 2:
            pk = paired_ddE(res, model, res.dE[model][:, keep])
            yk = np.array([pk[n].mean for n in names])
            sec["spearman"] = spearman(x, yk)
            sec["kendall"] = kendall_tau_b(x, yk)
            sec["slope"] = ols(x, yk)["slope"]
        block["secondary_drop_close_contact_poses"] = sec
        block["min_dist_to_field_ang"] = {
            "min": float(res.min_dist_ang[model].min()),
            "per_pose_min_over_ligands": res.min_dist_ang[model].min(axis=0).tolist(),
        }
        # pre-declared secondary (tyk2 plan): only the analogues measured in the same publication as the parent
        same = [i for i, n in enumerate(names) if sources[n] and sources[n] == sources[res.reference]]
        if 3 <= len(same) < len(names):
            block["secondary_same_source_as_parent"] = {
                "ligands": [names[i] for i in same],
                **_stat_block(x[same], y[same], seed + 250 + 10 * mi, n_boot),
            }
        # spread diagnostics: is ddE just size?
        block["ddE_vs_descriptors"] = {
            k: {"spearman": spearman(y, [desc[n][k] for n in names])} for k in ("heavy_atoms", "crippen_logp")
        }
        floors = {"0": 0.0, "noise_floor_4.07": DDE_NOISE_FLOOR_KCAL_MOL}
        # a screened copy of one force field's field is not a second charge model
        # a solvent or distance-dielectric model is not a second charge model
        gas_models = [m for m in res.models if m not in res.solvent and m not in res.distance_dielectric]
        if len(gas_models) >= 2 and mi == 0 and not dielectric_family(res):
            cm = charge_model_comparison(res, gas_models[0], gas_models[1], n_boot=n_boot, seed=seed + 500)
            out["charge_model_comparison"] = cm
            floors["charge_sensitivity"] = cm["charge_sensitivity_floor_kcal_mol"]
        if "charge_model_comparison" in out:
            floors.setdefault("charge_sensitivity", out["charge_model_comparison"]["charge_sensitivity_floor_kcal_mol"])
        rows, summ = pair_resolution(res, model, ddg, sigma, floors)
        block["pair_resolution"] = {"summary": summ, "pairs": rows}
        out["models"][model] = block
    scr = screening_analysis(res, x, n_boot=n_boot, seed=seed + 600)
    if scr:
        out["screening"] = scr
    sol = solvent_analysis(res, x, n_boot=n_boot, seed=seed + 700)
    if sol:
        out["solvent"] = sol
    ddd = distance_dielectric_analysis(res, x, n_boot=n_boot, seed=seed + 800)
    if ddd:
        out["distance_dielectric"] = ddd
    return out
