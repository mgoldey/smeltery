"""Assay-to-assay variability from ChEMBL: how far do independent measurements of one compound disagree? (#26)

For each PLB target, take every ChEMBL binding-assay activity of one measurement type (IC50 or Ki) with an exact
value in nM, and group by compound. A compound measured in two or more DIFFERENT publications (documents) gives
independent estimates of the same quantity. The pooled within-compound standard deviation of pChEMBL (-log10
molar) over those compounds is the assay-to-assay variability, converted to kcal/mol with RT ln 10 at 298.15 K.

    SMELTERY_NETWORK=1 python scripts/chembl_variability.py OUT.json [--targets cdk2 tyk2]

Only AGGREGATES are written (ChEMBL is CC BY-SA 3.0; the per-record data are not redistributed). `inputs_sha256` is
the digest of the sorted (compound, document, type, pChEMBL) tuples used, so a re-run can be compared without
storing them. Two comparisons are never made: IC50 against Ki (they differ systematically), and values inside one
document (those are replicates of one experiment, not independent assays).

Limits written into the output, not hidden: ChEMBL records the same value in several documents when one paper
re-reports another's data; such pairs are counted (`n_identical_pairs`) and a second estimate excludes compounds
whose documents all agree to within 0.005 log units.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

API = "https://www.ebi.ac.uk/chembl/api/data"
RT_LN10_KCAL = 0.0019872041 * 298.15 * math.log(10)  # kcal/mol per log10 unit at 298.15 K
IDENTICAL_TOL = 0.005  # pChEMBL is stored to two decimals

#: PLB target -> UniProt accession (the key used to find the ChEMBL target). The ChEMBL name returned is recorded.
UNIPROT = {
    "cdk2": "P24941", "tyk2": "P29597", "mcl1": "Q07820", "thrombin": "P00734", "p38": "Q16539", "pde2": "O00408",
    "ptp1b": "P18031", "cmet": "P08581", "syk": "P43405", "cdk8": "P49336", "tnks2": "Q9H2K2", "hif2a": "Q99814",
    "eg5": "P52732", "pfkfb3": "Q16875", "shp2": "Q06124",
}  # fmt: skip
FIELDS = (
    "molecule_chembl_id,document_chembl_id,assay_chembl_id,standard_type,pchembl_value,assay_type,potential_duplicate"
)


def to_kcal(log_units: float) -> float:
    return log_units * RT_LN10_KCAL


def pooled_within_sd(groups: list[list[float]]) -> dict:
    """Pooled within-group SD over groups of >= 2 values: sqrt(sum (n-1) s^2 / sum (n-1)). Returns ss, df and sd."""
    ss = sum(sum((x - statistics.fmean(g)) ** 2 for x in g) for g in groups if len(g) >= 2)
    df = sum(len(g) - 1 for g in groups if len(g) >= 2)
    return {"ss": ss, "df": df, "sd": math.sqrt(ss / df) if df else None}


def compound_groups(records: list[dict]) -> dict[tuple[str, str], dict[str, float]]:
    """(compound, type) -> {document: median pChEMBL within that document}. One value per document: replicates inside a
    document are one experiment, so they are collapsed, not counted as independent."""
    by: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for r in records:
        by[(r["molecule_chembl_id"], r["standard_type"], r["document_chembl_id"])].append(float(r["pchembl_value"]))
    out: dict[tuple[str, str], dict[str, float]] = defaultdict(dict)
    for (mol, typ, doc), vals in by.items():
        out[(mol, typ)][doc] = statistics.median(vals)
    return out


def summarize(records: list[dict], std_type: str) -> dict:
    """Variability statistics for one measurement type over compounds measured in >= 2 documents."""
    grp = {k: v for k, v in compound_groups(records).items() if k[1] == std_type and len(v) >= 2}
    values = [list(v.values()) for v in grp.values()]
    pairs = [abs(a - b) for v in values for i, a in enumerate(v) for b in v[i + 1 :]]
    ident = sum(1 for d in pairs if d < IDENTICAL_TOL)
    informative = [v for v in values if max(v) - min(v) >= IDENTICAL_TOL]
    pooled = pooled_within_sd(values)
    pooled_ex = pooled_within_sd(informative)
    return {
        "type": std_type,
        "n_compounds_2plus_documents": len(values),
        "n_pairs": len(pairs),
        "n_identical_pairs": ident,
        "median_abs_diff_log": statistics.median(pairs) if pairs else None,
        "pooled": {**pooled, "sd_kcal_mol": to_kcal(pooled["sd"]) if pooled["sd"] is not None else None},
        "pooled_excluding_all_identical_compounds": {
            **pooled_ex,
            "n_compounds": len(informative),
            "sd_kcal_mol": to_kcal(pooled_ex["sd"]) if pooled_ex["sd"] is not None else None,
        },
    }


def inputs_digest(records: list[dict]) -> str:
    rows = sorted(
        (r["molecule_chembl_id"], r["document_chembl_id"], r["standard_type"], str(r["pchembl_value"])) for r in records
    )
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def _get(url: str) -> dict:
    for attempt in range(4):
        try:
            with urllib.request.urlopen(url, timeout=120) as f:
                return json.load(f)
        except Exception:  # transient network error: back off and retry, then raise
            if attempt == 3:
                raise
            time.sleep(2 * (attempt + 1))
    raise AssertionError("unreachable")


def fetch_target(accession: str) -> dict:
    d = _get(f"{API}/target.json?" + urllib.parse.urlencode({"target_components__accession": accession, "limit": 20}))
    single = [t for t in d["targets"] if t["target_type"] == "SINGLE PROTEIN"]
    if len(single) != 1:
        raise RuntimeError(f"{accession}: expected exactly one SINGLE PROTEIN ChEMBL target, found {len(single)}")
    return {"target_chembl_id": single[0]["target_chembl_id"], "pref_name": single[0]["pref_name"]}


def fetch_records(target_chembl_id: str) -> list[dict]:
    q = {
        "target_chembl_id": target_chembl_id, "standard_relation": "=", "standard_units": "nM", "assay_type": "B",
        "standard_type__in": "IC50,Ki", "pchembl_value__isnull": "false", "potential_duplicate": "0",
        "only": FIELDS, "limit": 1000,
    }  # fmt: skip
    url, out = f"{API}/activity.json?" + urllib.parse.urlencode(q), []
    while url:
        d = _get(url)
        out += d["activities"]
        url = "https://www.ebi.ac.uk" + d["page_meta"]["next"] if d["page_meta"]["next"] else None
        time.sleep(0.2)
    return out


PLB_TO_CHEMBL_TYPE = {"ic50": "IC50", "pic50": "IC50", "ki": "Ki"}


def own_type_rows(results: dict, manifest: dict) -> list[dict]:
    """One row per PLB target with a compound measured in 2+ documents, for the target's OWN PLB measurement type."""
    rows = []
    for name, t in results["targets"].items():
        typ = PLB_TO_CHEMBL_TYPE[manifest["targets"][name]["assay_type"]]
        s = t["by_type"][typ]
        if s["n_compounds_2plus_documents"]:
            rows.append({"target": name, "type": typ, "chembl": t["target_chembl_id"], **s})
    return rows


def overall(rows: list[dict]) -> dict:
    """Pool the rows' within-compound sums of squares and degrees of freedom (exact, from the stored values)."""
    ss, df = sum(r["pooled"]["ss"] for r in rows), sum(r["pooled"]["df"] for r in rows)
    sd = math.sqrt(ss / df)
    return {"ss": ss, "df": df, "sd": sd, "sd_kcal_mol": to_kcal(sd), "diff_sd_kcal_mol": to_kcal(sd) * math.sqrt(2)}


def render_report(results: dict, manifest: dict) -> str:
    """The markdown table in benchmarks/variability/README.md (a test fails if the README drifts from this)."""
    rows = own_type_rows(results, manifest)
    lines = [
        "| PLB target | ChEMBL target | type | compounds in 2+ papers | pairs | identical pairs | "
        "pooled SD (log10) | pooled SD (kcal/mol) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        p = r["pooled"]
        lines.append(
            f"| {r['target']} | {r['chembl']} | {r['type']} | {r['n_compounds_2plus_documents']} | {r['n_pairs']} | "
            f"{r['n_identical_pairs']} | {p['sd']:.3f} | {p['sd_kcal_mol']:.2f} |"
        )
    o = overall(rows)
    lines.append(
        f"| **all of the above, pooled** | | | {sum(r['n_compounds_2plus_documents'] for r in rows)} | "
        f"{sum(r['n_pairs'] for r in rows)} | {sum(r['n_identical_pairs'] for r in rows)} | "
        f"**{o['sd']:.3f}** | **{o['sd_kcal_mol']:.2f}** |"
    )
    return (
        "\n".join(lines)
        + "\n\nA difference of two independent measurements has SD sqrt(2) x that: "
        + f"**{o['diff_sd_kcal_mol']:.2f} kcal/mol**."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", type=Path, help="results JSON to write (or, with --report, to read)")
    ap.add_argument("--targets", nargs="*", default=list(UNIPROT))
    ap.add_argument("--report", action="store_true", help="print the markdown table from an existing results JSON")
    args = ap.parse_args(argv)
    if args.report:
        manifest = json.loads(
            (Path(__file__).resolve().parents[1] / "benchmarks" / "plb" / "manifest.json").read_text()
        )
        print(render_report(json.loads(args.out.read_text()), manifest))
        return 0
    if os.environ.get("SMELTERY_NETWORK") != "1":
        print("error: this script uses the network; set SMELTERY_NETWORK=1", file=sys.stderr)
        return 2
    status = _get(f"{API}/status.json")
    result = {"targets": {}, "provenance": {}}
    for name in args.targets:
        t = fetch_target(UNIPROT[name])
        recs = fetch_records(t["target_chembl_id"])
        result["targets"][name] = {
            "uniprot": UNIPROT[name], **t, "n_records": len(recs), "inputs_sha256": inputs_digest(recs),
            "by_type": {typ: summarize(recs, typ) for typ in ("IC50", "Ki")},
        }  # fmt: skip
        print(name, t["target_chembl_id"], len(recs), "records", flush=True)
    result["provenance"] = {
        "source": "ChEMBL", "chembl_db_version": status["chembl_db_version"],
        "chembl_release_date": status["chembl_release_date"],
        "retrieved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "api": API,
        "filters": {
            "standard_relation": "=", "standard_units": "nM", "assay_type": "B (binding)",
            "standard_type": ["IC50", "Ki"], "pchembl_value": "not null", "potential_duplicate": 0,
        },
        "temperature_K": 298.15, "kcal_per_log10_unit": RT_LN10_KCAL,
        "licence": "CC BY-SA 3.0 (https://chembl.gitbook.io/chembl-interface-documentation/about)",
        "attribution": "ChEMBL, EMBL-EBI, https://www.ebi.ac.uk/chembl/ (Zdrazil et al., Nucleic Acids Res. 2024)",
        "redistribution": "aggregates only; the per-record data are not redistributed",
    }  # fmt: skip
    args.out.write_text(json.dumps(result, indent=1, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
