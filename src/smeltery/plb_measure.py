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
from dataclasses import dataclass
from itertools import combinations, permutations
from pathlib import Path
from typing import Any

import numpy as np

from .funnel import paired_delta, resolved, unpaired_delta
from .gates import charge_sensitivity, spearman
from .model import HARTREE_TO_KCAL, Candidate, Measurement
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
    for m in models:
        n_m = int(npm[m])
        if not 2 <= n_m <= n_poses:
            raise PlbResultsError(f"{m}: {n_m} completed poses (need 2 to {n_poses})")
        dE[m] = _array(res["dE_int_kcal_mol"][m], ligands, n_m, f"dE_int_kcal_mol[{m}]")
        e_fld = _array(res["E_field_hartree"][m], ligands, n_m, f"E_field_hartree[{m}]")
        if not np.allclose(dE[m], (e_fld - e_vac[:, :n_m]) * HARTREE_TO_KCAL, rtol=0, atol=1e-6):
            raise PlbResultsError(f"{m}: dE_int does not equal (E_field - E_vac) * {HARTREE_TO_KCAL} kcal/mol/Eh")
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


def analyse(res: PlbResults, target, *, seed: int = 20261010, n_boot: int = 10_000) -> dict[str, Any]:
    """Every statistic the plan declared, for every model, from the per-pose values and the manifest alone.

    `target` is a `smeltery.benchmark.Target` (experimental ddG and sigma); its reference must be the run's.
    """
    if target.reference != res.reference:
        raise PlbResultsError(f"manifest reference {target.reference!r} != run reference {res.reference!r}")
    names = list(res.analogues)
    if sorted(lg.name for lg in target.ligands) != sorted(res.ligands):
        raise PlbResultsError("the results' ligands are not the manifest target's ligands")
    ddg = {lg.name: lg.ddg_kcal_mol for lg in target.ligands}
    sigma = {lg.name: lg.ddg_sigma_kcal_mol for lg in target.ligands}
    x = np.array([ddg[n] for n in names])
    smiles = {lg.name: lg.smiles for lg in target.ligands}
    desc = descriptors(smiles)
    out: dict[str, Any] = {
        "seed": seed,
        "n_boot": n_boot,
        "n_ligands_analogues": len(names),
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
        # spread diagnostics: is ddE just size?
        block["ddE_vs_descriptors"] = {
            k: {"spearman": spearman(y, [desc[n][k] for n in names])} for k in ("heavy_atoms", "crippen_logp")
        }
        floors = {"0": 0.0, "noise_floor_4.07": DDE_NOISE_FLOOR_KCAL_MOL}
        if len(res.models) >= 2 and mi == 0:
            cm = charge_model_comparison(res, res.models[0], res.models[1], n_boot=n_boot, seed=seed + 500)
            out["charge_model_comparison"] = cm
            floors["charge_sensitivity"] = cm["charge_sensitivity_floor_kcal_mol"]
        if "charge_model_comparison" in out:
            floors.setdefault("charge_sensitivity", out["charge_model_comparison"]["charge_sensitivity_floor_kcal_mol"])
        rows, summ = pair_resolution(res, model, ddg, sigma, floors)
        block["pair_resolution"] = {"summary": summ, "pairs": rows}
        out["models"][model] = block
    return out
