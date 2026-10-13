"""Analyse a committed PLB MM/GBSA results file against experiment and the stored ddE study (issue #27).

    python scripts/analyse_plb_mmgbsa.py benchmarks/plb/results/cdk2_mmgbsa.json \\
        benchmarks/plb/results/cdk2_sto3g_analysis.json benchmarks/plb/results/cdk2_mmgbsa_analysis.json

Everything is recomputed from the per-ligand energies in the MM/GBSA results file, the committed manifest (experimental ddG)
and the stored ddE analysis (the ddE studies' AMBER ff14SB per-ligand pose means and Crippen logP). The statistics and
their seeds are the pre-declared ones of docs/benchmarks/plb_mmgbsa_plan.md, section 5; the language labels are section 6.
`tests/test_plb_mmgbsa.py` recomputes the analysis JSON and compares it. Needs numpy and RDKit only (no OpenMM).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import numpy as np

from smeltery.benchmark import load_benchmark
from smeltery.gates import spearman
from smeltery.plb_measure import _source_ids, _stat_block, paired_delta_spearman_ci, tracks_label

SEED = 20261010
# The ddE and logP blocks use the seeds the ddE studies used (SEED + 200 for the first model, SEED + 101 for Crippen logP), so
# their intervals are the ones already published in plb_cdk2.md and plb_tyk2.md; the MM/GBSA blocks use SEED + 10 and SEED + 40.
N_BOOT = 10_000
COMPONENTS = ("vdw", "elec", "gbsa")


def comparison_label(lo: float | None, hi: float | None) -> str:
    """Plan section 6: the Delta-rho = rho_MM - rho_other CI decides, and only its excluding 0 is read."""
    if lo is None or hi is None:
        return "cannot be distinguished at this n"
    if lo > 0:
        return "MM/GBSA ranks this series better (95% CI excludes 0)"
    if hi < 0:
        return "the comparator ranks this series better (95% CI excludes 0)"
    return "cannot be distinguished at this n"


def relative(values: dict[str, float], reference: str) -> dict[str, float]:
    return {n: float(v - values[reference]) for n, v in values.items()}


def _block(x, y, seed: int) -> dict[str, Any]:
    b = _stat_block(np.asarray(x, dtype=float), np.asarray(y, dtype=float), seed, N_BOOT)
    p, ci, perm = b["point"], b["bootstrap_ci_over_ligands"]["spearman"], b["permutation"]
    b["label"] = tracks_label(p["spearman"], perm["spearman"]["p_two_sided"], ci["lo"], p["kendall"])
    return b


def analyse_variant(
    res: dict,
    variant: str,
    names: list[str],
    extra: list[str],
    x_by_name: dict[str, float],
    ddE: dict,
    logp: dict,
    same_source: list[str] | None = None,
) -> dict[str, Any]:
    ref = res["reference"]
    lig = res["ligands"]
    have = [n for n in (ref, *names, *extra) if variant in lig.get(n, {})]
    missing = [n for n in (ref, *names) if n not in have]
    out: dict[str, Any] = {"missing_ligands": missing}
    if missing:
        return out
    dg = relative({n: lig[n][variant]["dG"]["total"] for n in have}, ref)
    comp = {c: relative({n: lig[n][variant]["dG"][c] for n in have}, ref) for c in COMPONENTS}
    x = np.array([x_by_name[n] for n in names])
    y = np.array([dg[n] for n in names])
    out["ddG_MM"] = {n: dg[n] for n in have}
    out["components_vs_parent"] = comp
    out["primary"] = _block(x, y, SEED + 10)
    yd = np.array([ddE[n] for n in names])
    yl = np.array([logp[n] for n in names])
    out["vs_ddE"] = {
        "delta_spearman": spearman(x, y) - spearman(x, yd),
        "paired_bootstrap_ci": paired_delta_spearman_ci(x, y, yd, n_boot=N_BOOT, seed=SEED + 20),
    }
    out["vs_ddE"]["label"] = comparison_label(
        out["vs_ddE"]["paired_bootstrap_ci"]["lo"], out["vs_ddE"]["paired_bootstrap_ci"]["hi"]
    )
    out["vs_logP"] = {
        "delta_spearman": spearman(x, y) - spearman(x, yl),
        "paired_bootstrap_ci": paired_delta_spearman_ci(x, y, yl, n_boot=N_BOOT, seed=SEED + 30),
    }
    out["vs_logP"]["label"] = comparison_label(
        out["vs_logP"]["paired_bootstrap_ci"]["lo"], out["vs_logP"]["paired_bootstrap_ci"]["hi"]
    )
    out["spearman_with_ddE"] = spearman(y, yd)
    out["spearman_with_logP"] = spearman(y, yl)
    out["component_spearman_vs_exp"] = {c: spearman(x, [comp[c][n] for n in names]) for c in COMPONENTS}
    out["component_spearman_vs_exp"]["vdw_plus_elec_vacuum_mm"] = spearman(
        x, [comp["vdw"][n] + comp["elec"][n] for n in names]
    )
    out["component_spearman_vs_exp"]["elec_plus_gbsa_polar"] = spearman(
        x, [comp["elec"][n] + comp["gbsa"][n] for n in names]
    )
    unconv = sorted(n for n in have if not lig[n][variant]["minimisation"]["converged"])
    out["unconverged_minimisations"] = unconv
    if unconv:
        keep = [n for n in names if n not in unconv]
        if len(keep) >= 4 and ref not in unconv:
            xk = np.array([x_by_name[n] for n in keep])
            yk = np.array([dg[n] for n in keep])
            out["drop_unconverged"] = {"ligands": keep, "spearman": spearman(xk, yk), "n": len(keep)}
    if same_source and 3 <= len(same_source) < len(names):
        # NOT pre-declared in the MM/GBSA plan (the tyk2 ddE plan declared it for ddE); added after the primary result was
        # read, because the series mixes two publications, and labelled as post hoc wherever it is quoted.
        xs = np.array([x_by_name[n] for n in same_source])
        out["post_hoc_same_source_as_parent"] = {
            "ligands": list(same_source),
            "ddE_spearman": spearman(xs, [ddE[n] for n in same_source]),
            **_block(xs, [dg[n] for n in same_source], SEED + 70),
        }
    if extra:
        names_all = [*names, *extra]
        xa = np.array([x_by_name[n] for n in names_all])
        ya = np.array([dg[n] for n in names_all])
        out["with_excluded_ligands_descriptive"] = {"ligands": names_all, **_block(xa, ya, SEED + 40)}
    return out


def analyse(res: dict, ddE_an: dict, target) -> dict[str, Any]:
    """The plan's analysis for one series. `target` is a `smeltery.benchmark.Target`."""
    ref = res["reference"]
    if target.reference != ref:
        raise ValueError(f"manifest reference {target.reference!r} != the results' {ref!r}")
    model = "amber14"
    per = ddE_an["models"][model]["per_ligand"]
    names = sorted(per)
    ddg = {lg.name: lg.ddg_kcal_mol for lg in target.ligands}
    for n in names:  # the ddE study's experimental values are the manifest's
        if abs(ddE_an["exp_ddg"][n] - ddg[n]) > 1e-12 * max(1.0, abs(ddg[n])):
            raise ValueError(f"{n}: experimental ddG differs between the manifest and the stored ddE analysis")
    extra = sorted(n for n in res["ligands"] if n != ref and n not in names)
    ddE = {n: per[n]["ddE_mean"] for n in names}
    logp = {n: ddE_an["baselines"]["crippen_logp"]["values"][n] for n in names}
    x = np.array([ddg[n] for n in names])
    out: dict[str, Any] = {
        "seed": SEED,
        "n_boot": N_BOOT,
        "target": res["target"],
        "reference": ref,
        "analogues": names,
        "extra_ligands_descriptive_only": extra,
        "exp_ddg": {n: ddg[n] for n in (ref, *names, *extra)},
        "ddE_amber14_pose_mean": ddE,
        "ddE_block": _block(x, [ddE[n] for n in names], SEED + 200),
        "crippen_logp_block": _block(x, [logp[n] for n in names], SEED + 101),
        "variants": {},
    }
    sources = {lg.name: _source_ids(lg) for lg in target.ligands}
    same = [n for n in names if sources.get(n) and sources[n] == sources.get(ref)]
    for v in res["variants"]:
        out["variants"][v] = analyse_variant(res, v, names, extra, ddg, ddE, logp, same)
    return out


def fmt_ci(ci: dict, nd: int = 2) -> str:
    return "n/a" if ci["lo"] is None else f"[{ci['lo']:+.{nd}f}, {ci['hi']:+.{nd}f}]"


def tables(an: dict) -> str:
    out = [f"{an['target']}: n = {len(an['analogues'])} analogues, parent {an['reference']}; bootstrap {an['n_boot']}, seed {an['seed']}\n"]
    out.append("| score | rho | rho 95% CI | p (perm., 2-sided) | tau-b | label |")
    out.append("|---|---:|---|---:|---:|---|")

    def row(label, b):
        p, c, pm = b["point"], b["bootstrap_ci_over_ligands"], b["permutation"]
        return f"| {label} | {p['spearman']:+.3f} | {fmt_ci(c['spearman'])} | {pm['spearman']['p_two_sided']:.4f} | {p['kendall']:+.3f} | {b['label']} |"

    for v, d in an["variants"].items():
        if d["missing_ligands"]:
            out.append(f"| MM/GBSA ({v}) | not computed: missing {d['missing_ligands']} | | | | |")
        else:
            out.append(row(f"MM/GBSA ({v})", d["primary"]))
    out.append(row("smeltery ddE (amber14)", an["ddE_block"]))
    out.append(row("Crippen logP", an["crippen_logp_block"]))
    out.append("\n| variant | Delta-rho vs ddE | 95% CI (paired) | reading | Delta-rho vs logP | 95% CI | rho(MM, ddE) | rho(MM, logP) |")
    out.append("|---|---:|---|---|---:|---|---:|---:|")
    for v, d in an["variants"].items():
        if d["missing_ligands"]:
            continue
        a, b = d["vs_ddE"], d["vs_logP"]
        out.append(
            f"| {v} | {a['delta_spearman']:+.3f} | {fmt_ci(a['paired_bootstrap_ci'])} | {a['label']} | {b['delta_spearman']:+.3f} | "
            f"{fmt_ci(b['paired_bootstrap_ci'])} | {d['spearman_with_ddE']:+.3f} | {d['spearman_with_logP']:+.3f} |"
        )
    out.append("\n| variant | rho vdW | rho Coulomb | rho GB+SA | rho vdW+Coulomb | rho Coulomb+GB+SA | not converged |")
    out.append("|---|---:|---:|---:|---:|---:|---|")
    for v, d in an["variants"].items():
        if d["missing_ligands"]:
            continue
        c = d["component_spearman_vs_exp"]
        out.append(
            f"| {v} | {c['vdw']:+.3f} | {c['elec']:+.3f} | {c['gbsa']:+.3f} | {c['vdw_plus_elec_vacuum_mm']:+.3f} | "
            f"{c['elec_plus_gbsa_polar']:+.3f} | {', '.join(d['unconverged_minimisations']) or 'none'} |"
        )
    for v, d in an["variants"].items():
        h = d.get("post_hoc_same_source_as_parent")
        if h:
            p, c, pm = h["point"], h["bootstrap_ci_over_ligands"], h["permutation"]
            out.append(
                f"\nPOST HOC, not pre-declared ({v}): the {p['n']} analogues from the parent's own publication: MM/GBSA rho {p['spearman']:+.3f} "
                f"{fmt_ci(c['spearman'])}, p {pm['spearman']['p_two_sided']:.4f}; ddE rho on the same ligands {h['ddE_spearman']:+.3f}"
            )
    for v, d in an["variants"].items():
        w = d.get("with_excluded_ligands_descriptive")
        if w:
            p, c, pm = w["point"], w["bootstrap_ci_over_ligands"], w["permutation"]
            out.append(
                f"\nWith the ddE-excluded ligand(s) {an['extra_ligands_descriptive_only']} ({v}, n = {p['n']}, descriptive only): "
                f"rho {p['spearman']:+.3f} {fmt_ci(c['spearman'])}, p {pm['spearman']['p_two_sided']:.4f}"
            )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results", type=Path)
    ap.add_argument("ddE_analysis", type=Path)
    ap.add_argument("analysis_out", type=Path)
    a = ap.parse_args(argv)
    res = json.loads(a.results.read_text())
    ddE_an = json.loads(a.ddE_analysis.read_text())
    an = analyse(res, ddE_an, load_benchmark().targets[res["target"]])
    a.analysis_out.write_text(json.dumps(an, indent=1, sort_keys=True) + "\n")
    print(tables(an))
    return 0


if __name__ == "__main__":
    sys.exit(main())
