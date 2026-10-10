# ruff: noqa: E501  (report-table f-strings)
"""Analyse a committed PLB results file: write the analysis JSON and print the report's tables.

    python scripts/analyse_plb.py benchmarks/plb/results/cdk2_sto3g.json benchmarks/plb/results/cdk2_sto3g_analysis.json

Everything is recomputed from the per-pose values in the results file and the committed manifest; the analysis JSON
is a cache of that computation (tests/test_plb_results.py recomputes it and compares). Seeds are fixed in
`smeltery.plb_measure.analyse` (seed 20261010, 10000 bootstrap resamples) and were declared in the plan.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from smeltery.benchmark import load_benchmark
from smeltery.plb_measure import PlbResultsError, analyse, load_results


def fmt_ci(ci: dict, nd: int = 2) -> str:
    return "n/a" if ci["lo"] is None else f"[{ci['lo']:+.{nd}f}, {ci['hi']:+.{nd}f}]"


def tables(an: dict) -> str:
    out = []
    n = an["n_ligands_analogues"]
    out.append(
        f"n = {n} analogues, {an['n_poses']} poses per ligand; bootstrap {an['n_boot']} resamples, seed {an['seed']}\n"
    )
    out.append("### Trend of computed ddE against experimental ddG\n")
    out.append(
        "| quantity | rho | rho 95% CI | p (exact perm., 2-sided) | tau-b | tau 95% CI | p (tau) | slope | slope 95% CI | RMSE after fit | MAE raw | sign agree | binom. p |"
    )
    out.append("|---|---:|---|---:|---:|---|---:|---:|---|---:|---:|---|---:|")

    def row(label, b):
        p, c, pm = b["point"], b["bootstrap_ci_over_ligands"], b["permutation"]
        return (
            f"| {label} | {p['spearman']:+.3f} | {fmt_ci(c['spearman'])} | {pm['spearman']['p_two_sided']:.4f} | "
            f"{p['kendall']:+.3f} | {fmt_ci(c['kendall'])} | {pm['kendall']['p_two_sided']:.4f} | "
            f"{p['slope']:+.2f} | {fmt_ci(c['slope'])} | {p['rmse_fit']:.2f} | {p['mae_raw']:.2f} | "
            f"{p['n_sign_agree']}/{p['n']} | {b['sign_binomial_two_sided_p']:.3f} |"
        )

    for m, b in an["models"].items():
        out.append(row(f"smeltery ddE ({m})", b))
    for k, b in an["baselines"].items():
        out.append(row(f"baseline: {k}", b))
    out.append("")
    out.append("### Pose noise and what survives it\n")
    out.append(
        "| model | mean paired SEM | mean unpaired SEM | sd of ddE across ligands | sd of exp ddG | reliability of ddE ranking | pose-only CI of rho | joint CI of rho |"
    )
    out.append("|---|---:|---:|---:|---:|---:|---|---|")
    for m, b in an["models"].items():
        pn = b["pose_noise"]
        out.append(
            f"| {m} | {pn['mean_paired_sem']:.2f} | {pn['mean_unpaired_sem']:.2f} | {pn['sd_of_ddE_across_ligands']:.2f} | "
            f"{pn['sd_of_exp_ddg_across_ligands']:.2f} | {pn['reliability_of_ddE_ranking']:.2f} | "
            f"{fmt_ci(b['pose_bootstrap_ci']['spearman'])} | {fmt_ci(b['joint_pose_and_ligand_bootstrap_ci']['spearman'])} |"
        )
    out.append("")
    for m, b in an["models"].items():
        out.append(f"### Per ligand ({m}), kcal/mol\n")
        out.append("| ligand | exp ddG | ddE mean | paired SEM | unpaired SEM | sd over poses |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for name, v in b["per_ligand"].items():
            out.append(
                f"| {name} | {an['exp_ddg'][name]:+.2f} | {v['ddE_mean']:+.2f} | {v['ddE_paired_sem']:.2f} | "
                f"{v['ddE_unpaired_sem']:.2f} | {v['sd_over_poses']:.2f} |"
            )
        out.append("")
        s = b["pair_resolution"]["summary"]
        out.append(f"### Pairs of the series resolved by ddE ({m}); {s['n_pairs']} pairs\n")
        out.append("| floor | kcal/mol | resolved (z=2) | right order | wrong order | binom. p |")
        out.append("|---|---:|---:|---:|---:|---:|")
        for k, v in s.items():
            if isinstance(v, dict):
                p = "n/a" if v["binomial_two_sided_p"] is None else f"{v['binomial_two_sided_p']:.3f}"
                out.append(
                    f"| {k} | {v['floor_kcal_mol']:.2f} | {v['n_resolved']} | {v['n_resolved_correct_order']} | "
                    f"{v['n_resolved_wrong_order']} | {p} |"
                )
        out.append(
            f"\nExperiment: {s['n_pairs_exp_resolved_2sigma']} pairs differ by more than 2 sigma of their reported "
            f"errors, {s['n_pairs_exp_diff_ge_noise_floor']} by at least the 4.07 noise floor; ddE has the right sign "
            f"for {s['n_pairs_correct_sign_all']} of {s['n_pairs']} pairs.\n"
        )
        sec = b["secondary_drop_close_contact_poses"]
        out.append(f"Secondary (poses with any atom < 2 A from a field charge dropped): {sec}\n")
    cm = an.get("charge_model_comparison")
    if cm:
        out.append("### Charge model: same geometries, two pocket charge sets (issue #29, part 1)\n")
        out.append(
            f"models {cm['models']}, n = {cm['n']}: sign flips of ddE {cm['n_sign_flips_of_ddE']}/{cm['n']}; pair order flipped "
            f"{cm['n_pairs_order_flipped']}/{cm['n_pairs']}; Spearman between models {cm['spearman_between_models']:+.3f} "
            f"{fmt_ci(cm['spearman_between_models_bootstrap_ci'])}; Kendall {cm['kendall_between_models']:+.3f}; "
            f"charge_sensitivity floor {cm['charge_sensitivity_floor_kcal_mol']:.2f} kcal/mol; raw dE_int shift "
            f"{cm['raw_dE_int_mean_shift_b_minus_a_kcal_mol']:+.2f} +/- {cm['raw_dE_int_sd_of_shift_kcal_mol']:.2f} (sd) kcal/mol; "
            f"raw dE_int Spearman across ligand-pose {cm['raw_dE_int_spearman_across_ligand_pose']:+.3f}\n"
        )
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results", type=Path)
    ap.add_argument("analysis_out", type=Path)
    ap.add_argument("--target", default=None, help="manifest target (default: the results' own)")
    a = ap.parse_args(argv)
    try:
        res = load_results(a.results)
    except PlbResultsError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 2
    target = load_benchmark().targets[a.target or res.target]
    an = analyse(res, target)
    a.analysis_out.write_text(json.dumps(an, indent=1, sort_keys=True) + "\n")
    print(tables(an))
    return 0


if __name__ == "__main__":
    sys.exit(main())
