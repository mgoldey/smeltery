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
        f"n = {n} analogues, design {an['n_poses']} poses, completed per model {an['n_poses_by_model']}; bootstrap {an['n_boot']} resamples, seed {an['seed']}\n"
    )
    out.append("### Trend of computed ddE against experimental ddG\n")
    out.append(
        "| quantity | rho | rho 95% CI | p (permutation, 2-sided; exact for n <= 9, else 200,000 seeded shuffles) | tau-b | tau 95% CI | p (tau) | slope | slope 95% CI | RMSE after fit | MAE raw | sign agree | binom. p |"
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
        ss = b.get("secondary_same_source_as_parent")
        if ss:
            sp, sc_, spm = ss["point"], ss["bootstrap_ci_over_ligands"], ss["permutation"]
            out.append(
                f"Secondary (only the {len(ss['ligands'])} analogues measured in the parent's publication): rho {sp['spearman']:+.3f} "
                f"{fmt_ci(sc_['spearman'])}, p {spm['spearman']['p_two_sided']:.4f}; tau-b {sp['kendall']:+.3f}; slope {sp['slope']:+.2f} {fmt_ci(sc_['slope'])}\n"
            )
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
    sc = an.get("screening")
    if sc:
        out.append(
            f"### Screening: the unscreened field's charges divided by eps (issue #28), {sc['n_poses_common']} common poses, "
            f"base model {sc['base_model']}\n"
        )
        out.append(
            "| eps | rho | p (perm., 2-sided) | rho 95% CI | slope | slope 95% CI | sd ratio | mean dE_int | dE_int / unscreened | 1/eps | slope / first-order | rho with unscreened ddE | ddE sign flips | dE_int sign flips |"
        )
        out.append("|---:|---:|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in sc["by_eps"].values():
            p, c = r["point"], r["bootstrap_ci_over_ligands"]
            sf = (
                "n/a" if r["slope_measured_over_first_order"] is None else f"{r['slope_measured_over_first_order']:.2f}"
            )
            out.append(
                f"| {r['eps']:g} | {p['spearman']:+.3f} | {r['spearman_p_two_sided']:.4f} | {fmt_ci(c['spearman'])} | "
                f"{p['slope']:+.2f} | {fmt_ci(c['slope'])} | {p['sd_ratio']:.2f} | {r['mean_dE_int_kcal_mol']:+.2f} | "
                f"{r['mean_dE_int_ratio_to_unscreened']:.3f} | {r['first_order_expectation_1_over_eps']:.3f} | {sf} | "
                f"{r['spearman_with_unscreened_ddE']:+.3f} | {r['n_ddE_sign_flips_vs_unscreened']}/{len(r['ddE_mean'])} | "
                f"{r['n_dE_int_sign_flips_vs_unscreened']}/{r['n_dE_int_values']} |"
            )
        cr = sc["slope_crosses_1_at_eps"]
        out.append(
            f"\nFitted slope crosses 1 at eps = {'not within the range' if cr is None else f'{cr:.2f}'} (log-log interpolation, descriptive)\n"
        )
    sv = an.get("solvent")
    if sv:
        out.append(
            f"### Implicit solvation (IEF-PCM): the same field, both legs in the solvent, against the gas-phase model {sv['gas_model']} on the same poses (issue #28)\n"
        )
        hdr = (
            "| model | solvent | poses | rho | p (perm., 2-sided) | rho 95% CI | tau-b | slope | slope 95% CI | sd ratio | "
            "gas rho | gas slope | gas sd ratio | Delta-rho | Delta-rho 95% CI (paired) | rho with gas ddE | mean dE_int | gas mean dE_int | ratio | "
            "ddE sign flips | dE_int sign flips | rank change | tracks | spread |"
        )
        sep = "|---|---|---:|---:|---:|---|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|---|---|"

        def srow(r):
            p, c, g = r["point"], r["bootstrap_ci_over_ligands"], r["gas_point"]
            lb = r["labels"]
            return (
                f"| {r['model']} | {r['solvent']} | {r['n_poses']} | {p['spearman']:+.3f} | {r['spearman_p_two_sided']:.4f} | "
                f"{fmt_ci(c['spearman'])} | {p['kendall']:+.3f} | {p['slope']:+.2f} | {fmt_ci(c['slope'])} | {p['sd_ratio']:.2f} | "
                f"{g['spearman']:+.3f} | {g['slope']:+.2f} | {g['sd_ratio']:.2f} | {r['delta_spearman_vs_gas']:+.3f} | "
                f"{fmt_ci(r['delta_spearman_paired_bootstrap_ci'])} | {r['spearman_with_gas_ddE']:+.3f} | "
                f"{r['mean_dE_int_kcal_mol']:+.2f} | {r['gas_mean_dE_int_kcal_mol']:+.2f} | {r['mean_dE_int_ratio_to_gas']:.3f} | "
                f"{r['n_ddE_sign_flips_vs_gas']}/{len(r['ddE_mean'])} | {r['n_dE_int_sign_flips_vs_gas']}/{r['n_dE_int_values']} | "
                f"{lb['rank_change']} | {lb['tracks']} | {lb['spread']} |"
            )

        out.append(hdr)
        out.append(sep)
        out.extend(srow(r) for r in sv["by_model"].values())
        if sv["on_common_poses_of_all"]:
            out.append(f"\nOn the {sv['n_poses_common_all']} poses ALL solvent models completed:\n")
            out.append(hdr)
            out.append(sep)
            out.extend(srow(r) for r in sv["on_common_poses_of_all"].values())
        out.append("")
    dd = an.get("distance_dielectric")
    if dd:
        out.append(
            f"### Distance-dependent dielectric eps(d) = max(1, s d), d = distance to the nearest ligand atom, against the unscreened model {dd['unscreened_model']} on the same poses (issue #28)\n"
        )
        out.append(
            "| model | s (1/A) | poses | rho | p (perm., 2-sided) | rho 95% CI | tau-b | slope | sd ratio | unscreened rho | Delta-rho | "
            "Delta-rho 95% CI (paired) | rho with unscreened ddE | mean dE_int | ratio to unscreened | ddE sign flips | dE_int sign flips | rank change | tracks |"
        )
        out.append("|---|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---:|---|---|")
        for r in dd["by_model"].values():
            p, c, g, lb = r["point"], r["bootstrap_ci_over_ligands"], r["gas_point"], r["labels"]
            out.append(
                f"| {r['model']} | {r['distance_dielectric_slope']:g} | {r['n_poses']} | {p['spearman']:+.3f} | "
                f"{r['spearman_p_two_sided']:.4f} | {fmt_ci(c['spearman'])} | {p['kendall']:+.3f} | {p['slope']:+.2f} | "
                f"{p['sd_ratio']:.2f} | {g['spearman']:+.3f} | {r['delta_spearman_vs_gas']:+.3f} | "
                f"{fmt_ci(r['delta_spearman_paired_bootstrap_ci'])} | {r['spearman_with_gas_ddE']:+.3f} | "
                f"{r['mean_dE_int_kcal_mol']:+.2f} | {r['mean_dE_int_ratio_to_gas']:.3f} | "
                f"{r['n_ddE_sign_flips_vs_gas']}/{len(r['ddE_mean'])} | {r['n_dE_int_sign_flips_vs_gas']}/{r['n_dE_int_values']} | "
                f"{lb['rank_change']} | {lb['tracks']} |"
            )
        out.append("")
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
