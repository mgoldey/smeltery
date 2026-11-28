"""The baseline control: does ddE rank a campaign's analogues better than a cheap baseline does?

L5 (Crippen logP) and L15 (single-snapshot MM/GBSA) in `docs/limits.md` measured that on two series a baseline that
costs seconds ranked the analogues as well as ddE or better. This module turns that one-off measurement into an
opt-in control any campaign with experimental labels can run, with the same statistics the benchmark used:
Spearman rho against the labels, the paired-bootstrap CI of Delta-rho = rho(baseline) - rho(ddE) that resamples
ligands once for both correlations (`plb_measure.paired_delta_spearman_ci`), and the smallest significant |rho| and
the power at a true r = 0.7 of the exact permutation test at this n (`plb_measure.power_simulation`, the logic
behind L4 and L8).

Nothing here touches the funnel: it reads `results.paired_ddE` after the cut and returns a dict. The verdict is one
of a fixed set of phrases (`PHRASES`), each a statement about THIS series at THIS n; none says ddE "matches" or "is
equivalent to" a baseline, because a CI that contains 0 is the absence of evidence of a difference, and none says
ddE "beats" a baseline unless the paired CI excludes 0 (and then says it is one series).

Pure functions, numpy only (RDKit is imported inside `crippen_logp`); the hook that runs it after the measurement
is `smeltery.methods.baseline`.
"""

from __future__ import annotations

import importlib.util
import math
import shutil
from collections.abc import Callable, Mapping
from typing import Any

import numpy as np

from .gates import spearman

#: Bootstrap defaults: the PLB analyses' seed and resample count.
DEFAULT_SEED = 20261010
DEFAULT_N_BOOT = 10_000
#: The true Pearson r the power statement is made at (L4 and L8 quote it at 0.7), and the power below which a
#: verdict is marked underpowered.
POWER_AT_R = 0.7
ADEQUATE_POWER = 0.8
#: Above this n the exact/Monte-Carlo permutation null of `power_simulation` is replaced by the large-n normal
#: approximation (|rho| > 1.96 / sqrt(n - 1)); the record says which was used.
SIMULATION_MAX_N = 100

#: Optional packages the minimal MM/GBSA protocol of `docs/benchmarks/plb_mmgbsa.md` needs, and the executable.
MMGBSA_MODULES = ("openmm", "openmmforcefields", "openff.toolkit")
MMGBSA_EXECUTABLE = "sqm"

PHRASES = {
    "no_labels": "no experimental labels: baselines computed, no comparison with ddE against experiment",
    "too_small": "n too small to conclude",
    "undefined": "undefined: ddE or the baseline is constant across the labelled analogues",
    "baseline_better": "does not beat the {b} baseline: the baseline ranks this series better",
    "indistinguishable": "indistinguishable from the {b} baseline at this n",
    "ddE_better": "ranks this series better than the {b} baseline; one series, not a general claim",
    "skipped": "{b} baseline not computed",
}

BASELINE_LABELS = {"crippen_logp": "logP", "mmgbsa": "MM/GBSA"}


def detectable(n: int, *, seed: int = 0) -> dict[str, Any]:
    """What a Spearman test can detect at n labelled analogues (L4: n = 8 -> 0.738, 40%; L8: n = 12 -> 0.587, 68%).

    `critical_abs_rho` is the smallest |rho| whose two-sided permutation p is <= 0.05 (None when no |rho| reaches it,
    n < 5); `power_at_r_0_7` the power of that test at a true Pearson r of 0.7 (None where unsimulated).
    """
    if n < 3:
        return {"n": n, "critical_abs_rho": None, "power_at_r_0_7": None, "method": "none"}
    if n > SIMULATION_MAX_N:
        return {"n": n, "critical_abs_rho": 1.96 / math.sqrt(n - 1), "power_at_r_0_7": None, "method": "normal"}
    from .plb_measure import power_simulation

    try:
        sim = power_simulation(n, [POWER_AT_R], n_sim=2000, seed=seed)
    except ValueError:  # min() of an empty set: no |rho| is significant at this n
        return {"n": n, "critical_abs_rho": None, "power_at_r_0_7": None, "method": "exact"}
    return {
        "n": n,
        "critical_abs_rho": sim["critical_abs_spearman"],
        "power_at_r_0_7": sim["power_by_true_pearson_r"][f"{POWER_AT_R:.2f}"],
        "method": "exact" if n <= 9 else "monte_carlo",
    }


def mmgbsa_availability() -> tuple[bool, str]:
    """(True, "") when the packages and executable the MM/GBSA protocol needs are all already installed, else
    (False, the missing ones). Nothing is installed or imported."""
    missing = []
    for mod in MMGBSA_MODULES:
        try:
            found = importlib.util.find_spec(mod) is not None
        except (ImportError, ValueError):  # a parent package that is absent
            found = False
        if not found:
            missing.append(mod)
    if shutil.which(MMGBSA_EXECUTABLE) is None:
        missing.append(f"{MMGBSA_EXECUTABLE} (AmberTools)")
    if missing:
        return False, "MM/GBSA needs " + ", ".join(missing) + ", not installed (docs/environments.md)"
    return True, ""


def crippen_logp(smiles_by_name: Mapping[str, str]) -> dict[str, float]:
    """Crippen logP of each heavy-atom SMILES (RDKit). A SMILES RDKit cannot read raises ValueError naming it."""
    from rdkit import Chem
    from rdkit.Chem import Crippen

    out: dict[str, float] = {}
    for name, smi in smiles_by_name.items():
        mol = Chem.MolFromSmiles(smi)
        if mol is None:
            raise ValueError(f"{name}: RDKit cannot read the SMILES {smi!r}")
        out[name] = float(Crippen.MolLogP(mol))
    return out


def _finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _nan_none(x: float) -> float | None:
    return x if math.isfinite(x) else None


def compare(
    name: str,
    values: Mapping[str, float],
    ddE: Mapping[str, float],
    labels: Mapping[str, float],
    *,
    seed: int,
    n_boot: int,
    det: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One baseline against ddE on the analogues that have a label, a finite ddE and a finite baseline value.

    Returns the entry recorded under `results.baseline.baselines[name]`: rho of each against the labels, Delta-rho =
    rho(baseline) - rho(ddE) with its paired-bootstrap 95% CI, the descriptive rho between ddE and the baseline,
    and the verdict phrase. Positive Delta-rho: the baseline ranks the series better.
    """
    from .plb_measure import paired_delta_spearman_ci

    names = [
        k for k in ddE if k in labels and k in values and all(_finite(d[k]) is not None for d in (ddE, labels, values))
    ]
    n = len(names)
    det = det if det is not None else detectable(n)
    entry: dict[str, Any] = {
        "status": "computed",
        "n": n,
        "names": names,
        "values": {k: float(values[k]) for k in names},
    }
    x = np.array([labels[k] for k in names], dtype=float)
    ya = np.array([values[k] for k in names], dtype=float)
    yb = np.array([ddE[k] for k in names], dtype=float)
    b = BASELINE_LABELS.get(name, name)
    if det["critical_abs_rho"] is None:
        entry["verdict"] = f"{PHRASES['too_small']}: n = {n}, no Spearman rho is significant at this n (needs n >= 5)"
        entry["verdict_key"] = "too_small"
        return entry
    rho_a, rho_b = spearman(x, ya), spearman(x, yb)
    entry["rho_baseline"], entry["rho_ddE"] = _nan_none(rho_a), _nan_none(rho_b)
    if not (math.isfinite(rho_a) and math.isfinite(rho_b)):
        entry["verdict"], entry["verdict_key"] = PHRASES["undefined"], "undefined"
        return entry
    entry["delta_rho"] = rho_a - rho_b
    ci = paired_delta_spearman_ci(x, ya, yb, n_boot=n_boot, seed=seed)
    entry["delta_rho_ci95"] = [ci["lo"], ci["hi"]]
    entry["bootstrap"] = {"n_boot": n_boot, "n_valid": ci["n_valid"], "seed": seed, "level": 0.95}
    agree = spearman(ya, yb)
    entry["rho_baseline_vs_ddE"] = _nan_none(agree)
    lo, hi = ci["lo"], ci["hi"]
    if lo is None or hi is None:
        entry["verdict"], entry["verdict_key"] = PHRASES["undefined"], "undefined"
        return entry
    key = "baseline_better" if lo > 0 else "ddE_better" if hi < 0 else "indistinguishable"
    phrase = PHRASES[key].format(b=b)
    if key == "indistinguishable":
        phrase += " (paired 95% CI of Delta-rho contains 0; that is not evidence they are equivalent)"
    else:
        phrase += " (paired 95% CI of Delta-rho excludes 0)"
    power = det["power_at_r_0_7"]
    entry["underpowered"] = bool(power is not None and power < ADEQUATE_POWER)
    if entry["underpowered"]:
        phrase += (
            f"; underpowered: smallest significant |rho| {det['critical_abs_rho']:.3f}, "
            f"about {100 * power:.0f}% power at true r = {POWER_AT_R}"
        )
    entry["verdict"], entry["verdict_key"] = phrase, key
    return entry


def baseline_control(
    ddE: Mapping[str, float],
    labels: Mapping[str, float] | None,
    baselines: Mapping[str, Callable[[], Mapping[str, float]] | str],
    *,
    seed: int = DEFAULT_SEED,
    n_boot: int = DEFAULT_N_BOOT,
) -> dict[str, Any]:
    """The `results.baseline` block. `baselines[name]` is either a callable giving {candidate: value} or a string:
    the reason that baseline was skipped. `labels` None means the run has no experimental labels: the baselines
    are still computed (so the record carries them) and no comparison is made."""
    out: dict[str, Any] = {"analogues": sorted(ddE), "baselines": {}}
    for name, spec in baselines.items():
        b = BASELINE_LABELS.get(name, name)
        if isinstance(spec, str):
            out["baselines"][name] = {
                "status": "skipped",
                "reason": spec,
                "verdict": f"{PHRASES['skipped'].format(b=b)}: {spec}",
                "verdict_key": "skipped",
            }
    runnable = {k: v for k, v in baselines.items() if not isinstance(v, str)}
    values = {k: dict(fn()) for k, fn in runnable.items()}
    if labels is None:
        for name, vals in values.items():
            out["baselines"][name] = {
                "status": "computed",
                "values": {k: float(v) for k, v in vals.items() if k in ddE},
                "verdict": PHRASES["no_labels"],
                "verdict_key": "no_labels",
            }
        out["n_labelled"] = 0
    else:
        usable = [k for k in ddE if k in labels and _finite(labels[k]) is not None and _finite(ddE[k]) is not None]
        out["n_labelled"] = len(usable)
        out["unlabelled"] = sorted(set(ddE) - set(usable))
        out["labels_not_in_run"] = sorted(set(labels) - set(ddE))
        det = detectable(len(usable))
        out["detectable"] = det
        for i, (name, vals) in enumerate(values.items()):
            out["baselines"][name] = compare(name, vals, ddE, labels, seed=seed + i, n_boot=n_boot, det=det)
    out["baselines"] = {k: out["baselines"][k] for k in baselines}  # the config's order, skipped ones included
    out["verdict"] = "; ".join(e["verdict"] for e in out["baselines"].values()) or PHRASES["no_labels"]
    return out
