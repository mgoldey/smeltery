"""One-off: build benchmarks/plb/manifest.json from a local checkout of openforcefield/protein-ligand-benchmark.

Not part of the package and not run by CI. It needs PyYAML, which is deliberately NOT a smeltery dependency:

    git clone https://github.com/openforcefield/protein-ligand-benchmark /tmp/plb-src   # untrusted data
    git -C /tmp/plb-src checkout fd88824f9114244f95a14b485e6d6c96c1de716d
    uv run --with pyyaml python scripts/build_plb_manifest.py /tmp/plb-src benchmarks/plb/manifest.json

The checkout is untrusted data: this script only reads YAML (safe_load), SDF/PDB bytes (hashed) and PDB text.
Every number written is computed here from the upstream files; nothing is typed in by hand except the licence
text, which is copied from the checkout's own LICENSE/LICENSE_DATA/CITATION.cff and checked against them.
"""

from __future__ import annotations

import hashlib
import json
import statistics
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # repo root, for benchmarks.plb.suitability

from benchmarks.plb import suitability as site_rules  # noqa: E402
from smeltery import benchmark as bm  # noqa: E402

UPSTREAM = "https://github.com/openforcefield/protein-ligand-benchmark"
RAW = "https://raw.githubusercontent.com/openforcefield/protein-ligand-benchmark"
RETRIEVAL_DATE = "2026-10-10"
BENCHMARK_VERSION = "1.0.0"
DDE_NOISE_FLOOR = 4.07  # smeltery.pocket.binding_energy.DDE_NOISE_FLOOR_KCAL_MOL; a test pins equality
LITERATURE_DOI = "10.1021/jm300131x"
METALS = {"ZN", "MG", "MN", "FE", "CA", "NA", "K", "CO", "NI", "CU", "CD", "HG", "CL"}  # CL/NA/K are ions, listed

FILE_KINDS = {
    "protein_pdb": "01_protein/crd/protein.pdb",
    "ligands_sdf": "02_ligands/ligands.sdf",
    "ligands_yml": "00_data/ligands.yml",
    "target_yml": "00_data/target.yml",
}


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def sources_of(meas: dict, target_refs) -> list[dict]:
    d = meas.get("doi")
    items = d if isinstance(d, list) else [d]
    out = []
    for s in items:
        if not isinstance(s, str) or not s:
            raise SystemExit(f"measurement without a source: {meas}")
        out.append({"kind": "doi" if s.startswith("10.") else "url", "id": s})
    return out


def hetatm_summary(pdb: Path) -> dict:
    res: dict[str, set] = defaultdict(set)
    for line in pdb.read_text().splitlines():
        if line.startswith("HETATM"):
            res[line[17:20].strip()].add((line[21], line[22:27].strip()))
    return {k: len(v) for k, v in sorted(res.items()) if k not in ("HOH", "WAT")}


def main(src: Path, out: Path, only: list[str] | None = None) -> None:
    commit = subprocess.run(["git", "-C", str(src), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    commit = commit.stdout.strip()
    cdate = subprocess.run(
        ["git", "-C", str(src), "log", "-1", "--format=%cs"], capture_output=True, text=True, check=True
    ).stdout.strip()
    lic_data = (src / "LICENSE_DATA").read_text()
    assert "Attribution 4.0 International" in lic_data and "Copyright (c) 2020, Open Forcefield Group" in lic_data
    assert "MIT License" in (src / "LICENSE").read_text()[:50]
    assert "10.5281/zenodo.4813735" in (src / "CITATION.cff").read_text()
    attribution = (
        "Protein-Ligand Benchmark Dataset for Free Energy Calculations. Copyright (c) 2020, Open Forcefield Group; "
        f"{UPSTREAM} (commit {commit}); data licensed under CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/). "
        "Cite: Hahn et al., 'Best Practices for Constructing, Preparing, and Evaluating Protein-Ligand Binding "
        "Affinity Benchmarks', LiveCoMS 4(1), 1497 (2022), doi:10.33011/livecoms.4.1.1497, and the dataset, "
        "doi:10.5281/zenodo.4813735. Changes: ligand records re-serialised from ligands.yml/target.yml to JSON, "
        "SMILES additionally canonicalised with RDKit, and relative binding free energies, uncertainties and "
        "suitability statistics derived (scripts/build_plb_manifest.py). No endorsement by the original authors "
        "is implied."
    )
    targets_out = {}
    all_ligs = []  # (target, ligand name, canonical heavy smiles, measurement) for duplicate detection
    ligprep_ph = {}
    for d in sorted((src / "data").iterdir()):
        if not d.is_dir() or (only and d.name not in only):
            continue
        name = d.name
        L = yaml.safe_load((d / "00_data/ligands.yml").read_text())
        T = yaml.safe_load((d / "00_data/target.yml").read_text())
        names = sorted(L)
        # PLB writes -1 for "no error given" (its README says null; the p38 comment 'no error given' confirms -1).
        raw_err = {n: L[n]["measurement"]["error"] for n in names}
        for n in names:
            e = L[n]["measurement"]["error"]
            if e is None or e == -1:
                L[n]["measurement"]["error"] = None
            elif e < 0:
                raise SystemExit(f"{name}/{n}: unexpected negative error {e}")
        smiles_up = [L[n]["smiles"] for n in names]
        canon = [bm.heavy_canonical(s) for s in smiles_up]
        types = {L[n]["measurement"]["type"] for n in names}
        assert len(types) == 1, (name, types)
        assay = types.pop()
        counts = site_rules.neighbour_counts(smiles_up)
        best = max(counts)
        heavy = [bm.ligand_chemistry(s)["heavy_atoms"] for s in smiles_up]
        # most single-site neighbours; ties -> fewest heavy atoms (the parent is the unsubstituted one), then name
        ref_i = min((i for i, c in enumerate(counts) if c == best), key=lambda i: (heavy[i], names[i]))
        ref = names[ref_i]
        x_ref = bm.to_molar(assay, L[ref]["measurement"]["value"], L[ref]["measurement"]["unit"])
        s_ref = bm.sigma_ln(
            assay, L[ref]["measurement"]["value"], L[ref]["measurement"]["unit"], L[ref]["measurement"]["error"]
        )
        ligs = []
        site = {}
        for i, n in enumerate(names):
            m = L[n]["measurement"]
            x = bm.to_molar(assay, m["value"], m["unit"])
            s = bm.sigma_ln(assay, m["value"], m["unit"], m["error"])
            ddg = bm.ddg_kcal_mol(x, x_ref)
            sig = 0.0 if (i == ref_i and s is not None) else bm.ddg_sigma_kcal_mol(s, s_ref)
            if i != ref_i:
                site[n] = site_rules.single_site_change(smiles_up[ref_i], smiles_up[i])
            chem = bm.ligand_chemistry(smiles_up[i])
            ligs.append(
                {
                    "name": n,
                    "smiles": canon[i],
                    "smiles_upstream": smiles_up[i],
                    "measurement": {
                        "type": m["type"],
                        "value": m["value"],
                        "unit": m["unit"],
                        "error": m["error"],
                        "plb_error_raw": raw_err[n],
                        "comment": m.get("comment", "") or "",
                        "sources": sources_of(m, T.get("references")),
                    },
                    "ddg_kcal_mol": ddg,
                    "ddg_sigma_kcal_mol": sig,
                    "plb_charge": L[n].get("charge"),
                    "chemistry": chem,
                    "single_site_vs_reference": None if i == ref_i else site[n],
                }
            )
            all_ligs.append((name, n, canon[i], dict(m)))
        # ligand prep pH from the checkout's LigPrep input (a settings file; that it made the SMILES is UNVERIFIED)
        inp = next(iter((src / "preparation/ligand-prep").glob(f"ligprep_{name}*/*.inp")), None)
        ph = None
        if inp is not None:
            for line in inp.read_text().splitlines():
                if line.startswith("PH "):
                    ph = float(line.split()[1])
        ligprep_ph[name] = ph
        pH_txt = (
            f"Ligand protonation/tautomer state is the one in PLB's ligands.yml SMILES (formal charges explicit). "
            f"PLB documents Schrodinger LigPrep/Epik at pH {ph} for this target "
            f"(preparation/ligand-prep/{inp.parent.name}/{inp.name}); that this file generated the shipped SMILES "
            "is UNVERIFIED. Protein: Schrodinger PrepWizard, PropKa/Epik pH 7.4 (preparation/cli-commands.txt)."
            if inp is not None
            else "UNVERIFIED: no LigPrep input found for this target in the checkout."
        )
        nc = T.get("netcharge")
        files = []
        for kind, rel in FILE_KINDS.items():
            p = d / rel
            files.append(
                {
                    "kind": kind,
                    "path": f"data/{name}/{rel}",
                    "url": f"{RAW}/{commit}/data/{name}/{rel}",
                    "sha256": sha256(p),
                    "size_bytes": p.stat().st_size,
                }
            )
        ddgs = [lg["ddg_kcal_mol"] for lg in ligs]
        sigs = [lg["ddg_sigma_kcal_mol"] for lg in ligs if lg["ddg_sigma_kcal_mol"] is not None and lg["name"] != ref]
        charges = [lg["chemistry"]["net_charge"] for lg in ligs]
        mism = [
            lg["name"]
            for lg in ligs
            if lg["plb_charge"] is not None and lg["plb_charge"] != lg["chemistry"]["net_charge"]
        ]
        rel_err = [
            (lg["measurement"]["error"] / lg["measurement"]["value"])
            for lg in ligs
            if lg["measurement"]["error"] is not None and lg["measurement"]["type"] != "pic50"
        ]
        n_an = len(ligs) - 1
        n_site = sum(1 for v in site.values() if v["single_site"])
        pw = bm.power_summary(ddgs, DDE_NOISE_FLOOR, ref_i)
        hyp = bm.power_summary(ddgs, DDE_NOISE_FLOOR / 4.5, ref_i)
        if pw["n_analogues_ge_floor_vs_reference"] == 0 and pw["n_pairs_ge_floor"] == 0:
            verdict = "CANNOT: no experimental difference in the series reaches the ddE noise floor, even at slope 1."
        elif pw["n_analogues_ge_floor_vs_reference"] == 0:
            verdict = (
                "CANNOT vs the reference (no analogue differs from it by the floor); "
                f"{pw['n_pairs_ge_floor']} of {pw['n_pairs']} analogue-analogue pairs do, at slope 1."
            )
        else:
            verdict = (
                f"PARTLY: {pw['n_analogues_ge_floor_vs_reference']} of {n_an} analogues differ from the reference "
                f"by >= the floor and {pw['n_pairs_ge_floor']} of {pw['n_pairs']} pairs do, at slope 1 (best case)."
            )
        suit = {
            "n_ligands": len(ligs),
            "n_analogues": n_an,
            "n_single_site_vs_reference": n_site,
            "n_timed_out_mcs": sum(1 for v in site.values() if v["timed_out"]),
            "single_site_rule": (
                f"RDKit MCS (elements, bond order, ring-complete, {site_rules.MCS_TIMEOUT_S}s timeout) vs the "
                "reference: "
                f"<=1 connected unmatched piece per side, each <= {site_rules.SINGLE_SITE_MAX_ATOMS} heavy atoms"
            ),
            "reference_neighbours": best,
            "ligand_net_charges": sorted(set(charges)),
            "n_ligands_charge_differs_from_reference": sum(1 for c in charges if c != charges[ref_i]),
            "plb_charge_disagreements": mism,
            "ligands_with_non_organic_elements": [lg["name"] for lg in ligs if lg["chemistry"]["non_organic_elements"]],
            "ligands_with_covalent_warhead_candidates": {
                lg["name"]: lg["chemistry"]["warhead_candidates"]
                for lg in ligs
                if lg["chemistry"]["warhead_candidates"]
            },
            "heavy_atoms_min": min(lg["chemistry"]["heavy_atoms"] for lg in ligs),
            "heavy_atoms_max": max(lg["chemistry"]["heavy_atoms"] for lg in ligs),
            "protein_hetatm_residues": hetatm_summary(d / FILE_KINDS["protein_pdb"]),
            "ddg_min_kcal_mol": min(ddgs),
            "ddg_max_kcal_mol": max(ddgs),
            "ddg_range_kcal_mol": max(ddgs) - min(ddgs),
            "ddg_sigma_median_kcal_mol": statistics.median(sigs) if sigs else None,
            "ddg_sigma_max_kcal_mol": max(sigs) if sigs else None,
            "n_ligands_with_reported_error": sum(1 for lg in ligs if lg["measurement"]["error"] is not None),
            "n_ligands_without_reported_error": sum(1 for lg in ligs if lg["measurement"]["error"] is None),
            "median_relative_error": statistics.median(rel_err) if rel_err else None,
            "n_first_order_error_unreliable": sum(1 for r in rel_err if r >= 0.5),
            "power": pw,
            "hypothetical_power_if_ddE_were_4p5x_ddG": {
                "label": "HYPOTHETICAL scenario from issue #27's unmeasured 4.5-5x overshoot; not a result",
                "n_analogues_ge_floor_vs_reference": hyp["n_analogues_ge_floor_vs_reference"],
                "n_pairs_ge_floor": hyp["n_pairs_ge_floor"],
            },
            "power_verdict": verdict,
        }
        targets_out[name] = {
            "pdb_id": T["pdb"],
            "assay_type": assay,
            "protein_net_charge": None if nc is None else int(str(nc).split()[0]),
            "reference": ref,
            "reference_rule": "ligand with the most single-site-change neighbours among the target's ligands "
            "(ties: fewest heavy atoms, then lowest name); computed, not chosen",
            "protonation": pH_txt,
            "plb_associated_sets": T.get("associated_sets"),
            "plb_target_references": T.get("references"),
            "files": files,
            "suitability": suit,
            "ligands": ligs,
        }
        print(name, len(ligs), assay, "ref", ref, "site", n_site, "/", n_an, "|", verdict[:70], file=sys.stderr)

    # same compound appearing twice (heavy-atom isomeric SMILES), within or across targets
    seen = defaultdict(list)
    for t, n, c, m in all_ligs:
        seen[c].append((t, n, m))
    dups = [
        [
            {"target": t, "ligand": n, "type": m["type"], "value": m["value"], "unit": m["unit"], "error": m["error"]}
            for t, n, m in v
        ]
        for v in seen.values()
        if len(v) > 1
    ]
    n_dup_groups = sum(1 for v in seen.values() if len(v) > 1)
    variability = {
        "within_benchmark": {
            "n_compounds_appearing_more_than_once": n_dup_groups,
            "occurrences": dups,
            "statement": (
                "Same-compound (heavy-atom isomeric SMILES) repeats found within PLB at this commit: "
                f"{n_dup_groups}. "
                + (
                    "Assay-to-assay variability therefore CANNOT be measured from this dataset alone."
                    if n_dup_groups == 0
                    else "Their spread is listed but each repeat is a different target or assay, so it is not "
                    "a clean reproducibility estimate."
                )
            ),
        },
        "literature": {
            "doi": LITERATURE_DOI,
            "citation": "Kramer, Kalliokoski, Gedeck, Vulpetti, J. Med. Chem. 2012, 55, 5165 (ChEMBL public Ki pairs)",
            "verified_from": "abstract via Europe PMC REST API, 2026-10-10 (the full text was not read)",
            "mean_error_pKi": 0.44,
            "sd_pKi": 0.54,
            "median_error_pKi": 0.34,
            "kcal_per_pKi_298K": bm.R_KCAL_MOL_K * bm.TEMPERATURE_K * 2.302585092994046,
            "mean_error_kcal_mol": 0.44 * bm.R_KCAL_MOL_K * bm.TEMPERATURE_K * 2.302585092994046,
            "sd_kcal_mol": 0.54 * bm.R_KCAL_MOL_K * bm.TEMPERATURE_K * 2.302585092994046,
            "scope": "Ki only (not IC50), heterogeneous public data; pairs of independent measurements of the "
            "same compound/target. How the abstract's quantities map to one measurement versus a difference of "
            "two is UNVERIFIED (full text not read); treat ~0.5-0.7 kcal/mol as the order of magnitude of the "
            "inter-laboratory floor for ONE ddG, and as ~10x smaller than smeltery's ddE noise floor.",
        },
    }
    manifest = {
        "schema_version": bm.SCHEMA_VERSION,
        "name": "plb-experimental-ddg",
        "benchmark_version": BENCHMARK_VERSION,
        "temperature_k": bm.TEMPERATURE_K,
        "gas_constant_kcal_mol_k": bm.R_KCAL_MOL_K,
        "ddg_definition": {
            "formula": "ddG_i = R*T*ln(X_i/X_ref), X = IC50 or Ki in mol/L (pIC50: 10**-pIC50); kcal/mol; negative "
            "= binds tighter than the reference",
            "sigma_lnX": "error/value (concentration types; first order); ln(10)*error (pIC50)",
            "sigma_ddG": "R*T*sqrt(sigma_lnX_i^2 + sigma_lnX_ref^2), independent errors; 0 for the reference",
            "caveats": [
                "IC50 is not Kd: Cheng-Prusoff is NOT applied, so IC50-derived ddG assumes equal substrate "
                "concentration and Km across the series (true only for one assay).",
                "T = 298.15 K is an assumption; PLB does not record assay temperature.",
                "PLB marks an unreported error as -1 (its README says null; a p38 comment reads 'no error given'). "
                "The manifest stores such errors as null (plb_error_raw keeps -1) and their ddG sigma is null.",
                "The reported error is copied from the source papers; PLB does not say whether it is an SD, SEM or "
                "range (UNVERIFIED).",
                "First-order error propagation is unreliable when error/value is large; see "
                "suitability.n_first_order_error_unreliable.",
            ],
        },
        "provenance": {
            "upstream_url": UPSTREAM,
            "upstream_commit": commit,
            "upstream_commit_date": cdate,
            "retrieval_date": RETRIEVAL_DATE,
            "upstream_dataset_doi": "10.5281/zenodo.4813735",
            "assembled_by": "scripts/build_plb_manifest.py",
            "upstream_files_hashed": sorted(FILE_KINDS),
            "notes": "targets.yml lists further targets (bace, jnk1, galectin, ...) with no data directory at this "
            "commit; only the directories present are included. PLB's README table counts differ from the data; "
            "the counts here are from ligands.yml.",
        },
        "licence": {
            "data_licence": "CC-BY-4.0",
            "data_licence_file": "LICENSE_DATA",
            "data_licence_url": "https://creativecommons.org/licenses/by/4.0/legalcode",
            "code_licence": "MIT",
            "copyright": "Copyright (c) 2020, Open Forcefield Group",
            "attribution": attribution,
        },
        "noise_floor": {
            "value_kcal_mol": DDE_NOISE_FLOOR,
            "source": "smeltery.pocket.binding_energy.DDE_NOISE_FLOOR_KCAL_MOL (experiments/danuglipron/RESULTS.md)",
        },
        "assay_variability": variability,
        "targets": targets_out,
    }
    out.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", out, out.stat().st_size, "bytes; ligprep pH:", ligprep_ph, file=sys.stderr)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3:] or None)
