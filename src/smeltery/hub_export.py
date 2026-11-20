"""A Hugging Face–loadable folder, with a dataset card and Croissant metadata, from an exported table.

`smeltery export hf SRC -o OUT` takes what `smeltery export` already wrote -- a dataset DIRECTORY (`export dataset`,
with its `attestation.dataset/1` record) or one Parquet file (`export parquet`, optionally with split files made on it)
-- and writes a folder `datasets.load_dataset(OUT, ...)` and the Hugging Face Hub read:

    OUT/dataset/runs.parquet                 the table, byte for byte (a copy, never a hard link)
    OUT/dataset/splits/<name>.json           each split file, byte for byte
    OUT/dataset/attestation/{digests,dataset}.json   the dataset record, byte for byte (dataset-directory input only)
    OUT/data/<split>/<partition>.parquet     each split's partitions as Parquet, the rows the split file names
    OUT/croissant.json                       Croissant 1.0 JSON-LD: the files with sha256, one RecordSet, provenance
    OUT/README.md                            the dataset card: YAML front matter (license, configs, features)

Everything is generated from the data and its record; nothing is written by hand or by a model, there is no timestamp
or absolute path, and the same input gives byte-identical files (the partition Parquet files depend on the pyarrow
that writes them, as the table does). It needs pyarrow only (the `dataset` extra): no network, no `datasets`, no
`huggingface_hub`.

`OUT/dataset/` is the source as it was: for a dataset directory, still a conformant one
(`attestation_schema.conformance.check_dataset_dir(OUT / "dataset")`), which it would not be with the card beside it.

* **Digests are the export's own.** A dataset directory is checked first (every file's sha256 and size against its
  record, and the full `attestation_schema` conformance check when that package is installed); the sha256 the record
  states is what croissant.json carries. A bare Parquet file is hashed. Files this module writes are hashed after
  writing. Every copied file is re-hashed in OUT before anything is published.
* **The license is never invented.** `--license` (an SPDX expression) or the dataset record's `license`; the two must
  agree when both are given. With neither, the card says the license is not stated, the front matter has no `license`
  key, croissant.json has no `license`, and `--push` refuses. smeltery's own `MIT OR Apache-2.0` is the code's, not the
  data's.
* **Provenance** comes from the table's own columns: each source run's `run_id`, `family` and `core_record_sha256`,
  and the ferric builds its rows state (`ferric_git_sha`, `ferric_dirty`, with rows whose build is not recorded
  counted by `ferric_build_basis`). In croissant.json it is W3C PROV (`prov:wasDerivedFrom`).
* **Splits.** A smeltery split lists row identities; the Hub needs files. Each split becomes a config of the same name
  whose splits are its partitions (`train`, `valid`, `test`), written as Parquet with the table's schema and metadata.
  The `default` config is the whole table as one split named `full` (not `train`: nothing was held out).

`--push REPO_ID` (the `hub` extra, huggingface_hub) uploads OUT to a Hugging Face dataset repository. It is a dry run
unless `--confirm` is given: it prints what would be uploaded and to where. The repository id must be given in full
(`owner/name`); the token is whatever huggingface_hub finds itself (`HF_TOKEN`, or the cache `hf auth login` writes),
never a command-line argument.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import dataset_export as D
from . import dataset_splits as S
from .record import file_sha256

CARD = "README.md"
CROISSANT = "croissant.json"
DATA_DIR = "data"
#: The exported files (table, splits, record) go under this directory of OUT, byte for byte and at their own relative
#: paths, so a dataset directory stays one conformant subtree: attestation-schema's check refuses a dataset directory
#: holding files its manifest does not list, which the card and the partition files would be.
SOURCE_DIR = "dataset"
TABLE_NAME = "runs.parquet"
TABLE = f"{SOURCE_DIR}/{TABLE_NAME}"
S_DIR = "splits"
RECORD_FILES = ("attestation/digests.json", "attestation/dataset.json")
DEFAULT_CONFIG = "default"
FULL_SPLIT = "full"
RECORD_SET = "runs"
CROISSANT_VERSION = "http://mlcommons.org/croissant/1.0"
PARQUET_MEDIA = "application/x-parquet"  # what mlcroissant reads Parquet by
HUB_URL = "https://huggingface.co/datasets/"

#: The Croissant 1.0 JSON-LD context (the spec's, as mlcroissant writes it), plus W3C PROV for provenance.
CONTEXT = {
    "@language": "en",
    "@vocab": "https://schema.org/",
    "citeAs": "cr:citeAs",
    "column": "cr:column",
    "conformsTo": "dct:conformsTo",
    "cr": "http://mlcommons.org/croissant/",
    "rai": "http://mlcommons.org/croissant/RAI/",
    "data": {"@id": "cr:data", "@type": "@json"},
    "equivalentProperty": "cr:equivalentProperty",
    "dataType": {"@id": "cr:dataType", "@type": "@vocab"},
    "dct": "http://purl.org/dc/terms/",
    "examples": {"@id": "cr:examples", "@type": "@json"},
    "extract": "cr:extract",
    "field": "cr:field",
    "fileProperty": "cr:fileProperty",
    "fileObject": "cr:fileObject",
    "fileSet": "cr:fileSet",
    "format": "cr:format",
    "includes": "cr:includes",
    "isLiveDataset": "cr:isLiveDataset",
    "jsonPath": "cr:jsonPath",
    "key": "cr:key",
    "md5": "cr:md5",
    "parentField": "cr:parentField",
    "path": "cr:path",
    "prov": "http://www.w3.org/ns/prov#",
    "recordSet": "cr:recordSet",
    "references": "cr:references",
    "regex": "cr:regex",
    "repeated": "cr:repeated",
    "replace": "cr:replace",
    "samplingRate": "cr:samplingRate",
    "sc": "https://schema.org/",
    "separator": "cr:separator",
    "source": "cr:source",
    "subField": "cr:subField",
    "transform": "cr:transform",
}

#: Hugging Face's license identifiers that ARE lowercase SPDX ids. Any other SPDX expression goes in as
#: `license: other` with `license_name`, and the exact expression is stated in the card and in croissant.json.
HF_SPDX_LICENSES = frozenset(
    """
    apache-2.0 mit afl-3.0 artistic-2.0 bsl-1.0 bsd-2-clause bsd-3-clause bsd-3-clause-clear cc0-1.0
    cc-by-2.0 cc-by-2.5 cc-by-3.0 cc-by-4.0 cc-by-sa-3.0 cc-by-sa-4.0 cc-by-nc-2.0 cc-by-nc-3.0 cc-by-nc-4.0
    cc-by-nd-4.0 cc-by-nc-nd-3.0 cc-by-nc-nd-4.0 cc-by-nc-sa-2.0 cc-by-nc-sa-3.0 cc-by-nc-sa-4.0
    cdla-sharing-1.0 cdla-permissive-1.0 cdla-permissive-2.0 wtfpl ecl-2.0 epl-1.0 epl-2.0 etalab-2.0
    eupl-1.1 eupl-1.2 agpl-3.0 gpl-2.0 gpl-3.0 lgpl-2.1 lgpl-3.0 isc lppl-1.3c ms-pl mpl-2.0 odbl osl-3.0
    postgresql ofl-1.1 ncsa unlicense zlib pddl
    """.split()
)

#: One line per column of `dataset_export.arrow_schema()` (a test pins the keys to it), for the card and croissant.json.
COLUMN_DESCRIPTIONS = {
    "run_id": "the source run's run_id, as its core record states it",
    "input_digest": "the source run's input digest (legacy record)",
    "record_sha256": "the legacy record's self-hash as written; null in records that predate it",
    "core_record_sha256": "sha256 of the run's core record (attestation/run.json); null without one",
    "record_file": "the legacy record's file name (run-<digest12>.json)",
    "campaign": "the run's campaign",
    "family": "the run's family, as its core record states it",
    "started_utc": "when the run started (UTC)",
    "ligand": "the candidate's name in the run",
    "is_parent": "the candidate is the run's parent (the reference)",
    "smiles": "SMILES, RDKit-canonical unless smiles_form says as-recorded",
    "smiles_form": "rdkit-canonical or as-recorded",
    "smiles_recorded": "the SMILES as the run recorded it",
    "target": "what the run was measured against (structure spec, pocket, receptor, or field:<digest>)",
    "target_kind": "structure, pocket, receptor, field or none",
    "target_sha256": "sha256 of the target as recorded",
    "tier": "the tier's recorded name",
    "tier_settings": "the tier's recorded settings, as JSON",
    "score_quantity": "the tier's headline quantity",
    "score": "mean of the candidate's per-pose values of score_quantity, in score_unit",
    "score_unit": "the unit of score, score_sem and score_floor on this row",
    "score_sem": "standard error of score; null for one sample",
    "score_n": "number of poses in score",
    "score_floor": "the systematic floor the tier declares for score_quantity; null while unmeasured",
    "score_floor_source": "where score_floor comes from, or which kind of null it is",
    "paired_ddE": "paired ddE against the parent, on the tier that measures the cut quantity; unit score_unit",
    "paired_ddE_sem": "standard error of paired_ddE",
    "paired_ddE_n": "number of pairs in paired_ddE",
    "paired_ddE_floor": "the floor the cut used",
    "paired_ddE_floor_source": "where paired_ddE_floor comes from",
    "scores": "every quantity of the tier (and the paired ddE), each with its own unit, sem, n and floor",
    "decision": "the candidate's funnel outcome: pass, drop, reference or undecided",
    "decision_reason": "why",
    "user_decision": "a user's decision from decisions-<digest12>.json, else null",
    "user_decision_reason": "the user's stated reason",
    "smeltery_version": "the smeltery that ran",
    "smeltery_commit": "its git commit",
    "smeltery_dirty": "its working tree was dirty",
    "ferric_version": "the ferric that measured this row (null for a per-row build stamp)",
    "ferric_git_sha": "the ferric build commit of this row's SCFs; null when not recorded",
    "ferric_dirty": "that build's working tree was dirty",
    "ferric_provenance_level": "VERIFIED, INFERRED or UNKNOWN",
    "ferric_provenance": "the full ferric provenance label",
    "ferric_source": "where the ferric identity was read from",
    "ferric_build_basis": "run_record, scf_rows, mixed_scf_rows or unrecorded: what the ferric columns were read from",
}

#: arrow type spelling -> (Hugging Face `datasets` dtype, Croissant dataType)
_TYPES = {
    "string": ("string", "sc:Text"),
    "large_string": ("large_string", "sc:Text"),
    "double": ("float64", "sc:Float"),
    "float": ("float32", "sc:Float"),
    "int32": ("int32", "sc:Integer"),
    "int64": ("int64", "sc:Integer"),
    "bool": ("bool", "sc:Boolean"),
}
_HF_SPLIT_NAME = re.compile(r"^\w+(\.\w+)*$")
_REPO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._-]*$")
_SPDX_ID = re.compile(r"^[A-Za-z0-9.+-]+$")


class HubError(D.ExportError):
    """The Hugging Face folder cannot be written or pushed."""


_NO_LICENSE = (
    "the data's license is not stated; a Hub push needs one: export with --license SPDX (the data's terms, not "
    "smeltery's code license)"
)


# ---- reading the source ----------------------------------------------------------------------------------------------


sha256_file = file_sha256  # the one file hash, `smeltery.record.file_sha256`


@dataclass
class Source:
    """What `export hf` reads: the table, its split files, and the dataset record when there is one."""

    root: Path | None  # the dataset directory, or None for a bare table
    table: Path
    digests: dict[str, str]  # path relative to the source (dataset directory) -> sha256, for every file copied as-is
    record: dict | None = None  # dataset.json as JSON
    splits: list[tuple[str, Path]] = field(default_factory=list)  # (name, path), in record / argument order


def _check_record_files(root: Path, record: dict) -> dict[str, str]:
    """Every file the record lists, re-hashed and sized against it (stdlib, so this runs without attestation-schema);
    returns path -> sha256 for the record's files plus the record's own two."""
    out = {}
    for f in record.get("files") or []:
        p = root / f["path"]
        if not p.is_file():
            raise HubError(f"{root}: {f['path']} is listed in its dataset record but missing")
        got = sha256_file(p)
        if got != f["sha256"] or p.stat().st_size != f.get("bytes", p.stat().st_size):
            raise HubError(
                f"{root}: {f['path']} does not match its dataset record (sha256 {got}, record {f['sha256']})"
            )
        out[f["path"]] = f["sha256"]
    digests = root / RECORD_FILES[0]
    if sha256_file(digests) != record.get("content_sha256"):
        raise HubError(f"{root}: {RECORD_FILES[0]} does not hash to the record's content_sha256")
    out[RECORD_FILES[0]] = record["content_sha256"]
    out[RECORD_FILES[1]] = sha256_file(root / RECORD_FILES[1])
    return out


def read_source(src: str | Path, split_files: list[str | Path] | tuple = ()) -> Source:
    """The table (and splits, and record) `src` names: a dataset directory or a Parquet file."""
    from . import dataset_record as DR

    src = Path(src)
    if DR.is_dataset_dir(src):
        if split_files:
            raise HubError("--split is for a bare table; a dataset directory's splits are the ones its record lists")
        if DR.AVAILABLE:  # the full check (record, canonical form, manifest) when attestation-schema can do it
            DR.open_dataset(src)
        record = json.loads((src / RECORD_FILES[1]).read_bytes())
        digests = _check_record_files(src, record)
        table = ((record.get("ext") or {}).get(DR.EXT) or {}).get("table") or {}
        path = table.get("path") or f"{DR.TABLE_STEM}.parquet"
        if table.get("format", "parquet") != "parquet" or not path.endswith(".parquet"):
            raise HubError(
                f"{src}: the table is {path}; the Hub layout needs Parquet (export the dataset with --format parquet)"
            )
        splits = [(s["name"], src / s["path"]) for s in record.get("splits") or []]
        return Source(src, src / path, digests, record, splits)
    if not src.is_file():
        raise HubError(f"{src}: not a dataset directory or a table file")
    with open(src, "rb") as f:
        if f.read(4) != b"PAR1":
            raise HubError(f"{src}: not a Parquet file (the Hub layout needs Parquet; `smeltery export parquet`)")
    digests = {TABLE_NAME: sha256_file(src)}
    splits = []
    for sf in split_files:
        S.load_split(sf, src)  # refuses a split made on another table, or one that does not cover it
        name = Path(sf).stem
        if any(name == n for n, _ in splits):
            raise HubError(f"two split files are named {name}")
        splits.append((name, Path(sf)))
        digests[f"{S_DIR}/{name}.json"] = sha256_file(Path(sf))
    return Source(None, src, digests, None, splits)


# ---- licence ---------------------------------------------------------------------------------------------------------


def check_license(expression: str) -> None:
    """SPDX license-expression syntax: attestation-schema's checker when installed, else ids and operators only."""
    from . import dataset_record as DR

    if DR.AVAILABLE and DR._dataset is not None:
        try:
            DR._dataset.check_spdx(expression)
        except ValueError as e:
            raise HubError(f"--license {expression!r} is not an SPDX license expression: {e}") from e
        return
    tokens = re.findall(r"\(|\)|[^\s()]+", expression)
    if not tokens or any(t in ("NOASSERTION", "NONE") or not re.fullmatch(r"[A-Za-z0-9.+:()-]+", t) for t in tokens):
        raise HubError(f"--license {expression!r} is not an SPDX license expression")


def resolve_license(given: str | None, record: dict | None) -> str | None:
    """The data's license: `--license`, else the record's; refused when the two disagree. None = not stated."""
    stated = (record or {}).get("license")
    if given is not None:
        check_license(given)
        if stated is not None and stated != given:
            raise HubError(f"--license {given!r} disagrees with the dataset record's license {stated!r}")
        return given
    return stated


def hf_license(spdx: str) -> dict:
    """Card front-matter keys for an SPDX expression: Hugging Face's id when it is one, else `other` + a name."""
    if spdx.lower() in HF_SPDX_LICENSES:
        return {"license": spdx.lower()}
    slug = re.sub(r"[^a-z0-9.]+", "-", spdx.lower()).strip("-")
    return {"license": "other", "license_name": slug}


def croissant_license(spdx: str) -> str:
    """A single SPDX id as its spdx.org page; an expression (or a LicenseRef) as written."""
    if _SPDX_ID.fullmatch(spdx) and not spdx.startswith("LicenseRef-"):
        return f"https://spdx.org/licenses/{spdx}.html"
    return spdx


# ---- facts from the table --------------------------------------------------------------------------------------------


def _schema_fields(schema) -> list[dict]:
    """[{name, type, hf, croissant, sub: [...]}] for every column, from the arrow schema."""
    import pyarrow as pa

    out = []
    for f in schema:
        t = f.type
        if pa.types.is_list(t) and pa.types.is_struct(t.value_type):
            sub = [{"name": c.name, "type": str(c.type), **_typed(c.type, f"{f.name}.{c.name}")} for c in t.value_type]
            out.append({"name": f.name, "type": str(t), "hf": None, "croissant": None, "sub": sub})
        else:
            out.append({"name": f.name, "type": str(t), **_typed(t, f.name), "sub": None})
    return out


def _typed(t, name: str) -> dict:
    spelled = str(t)
    if spelled not in _TYPES:
        raise HubError(f"column {name}: arrow type {spelled} has no Hugging Face / Croissant mapping here")
    hf, cr = _TYPES[spelled]
    return {"hf": hf, "croissant": cr}


def hf_features(fields: list[dict]) -> list[dict]:
    """The card's `dataset_info.features`, in `datasets.Features._to_yaml_list()` form."""
    out = []
    for f in fields:
        if f["sub"] is None:
            out.append({"name": f["name"], "dtype": f["hf"]})
        else:
            out.append({"name": f["name"], "list": [{"name": s["name"], "dtype": s["hf"]} for s in f["sub"]]})
    return out


def _one_unit(units: set) -> str | None:
    return next(iter(units)) if len(units) == 1 else None


def provenance(rows: list[dict]) -> list[dict]:
    """One entry per source run, in table order: run_id, family, core_record_sha256, ferric builds with row counts,
    and rows whose build is not recorded, counted by basis."""
    runs: dict[str, dict] = {}
    for r in rows:
        e = runs.setdefault(
            r["run_id"],
            {"run_id": r["run_id"], "family": r["family"], "core": set(), "builds": Counter(), "unstated": Counter()},
        )
        e["core"].add(r["core_record_sha256"])
        if r["ferric_git_sha"]:
            e["builds"][(r["ferric_git_sha"], r["ferric_dirty"])] += 1
        else:
            e["unstated"][r["ferric_build_basis"]] += 1
    out = []
    for e in runs.values():
        (core,) = e["core"] if len(e["core"]) == 1 else (None,)
        out.append(
            {
                "run_id": e["run_id"],
                "family": e["family"],
                "core_record_sha256": core,
                "rows": sum(e["builds"].values()) + sum(e["unstated"].values()),
                "ferric_builds": [
                    {"git_sha": sha, "dirty": dirty, "rows": n}
                    for (sha, dirty), n in sorted(e["builds"].items(), key=lambda kv: (kv[0][0], str(kv[0][1])))
                ],
                "ferric_unstated": dict(sorted(e["unstated"].items())),
            }
        )
    return out


# ---- the files -------------------------------------------------------------------------------------------------------


def _copy(src: Path, dst: Path) -> None:
    """A real copy, never a hard link: a dataset directory is immutable, and a link would let an in-place edit of the
    Hub folder rewrite the source's bytes (its digests would then fail). shutil.copy2 may clone or use
    copy_file_range where the OS offers it; either way the result is a separate inode."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _write_partitions(stage: Path, table_path: Path, splits: list[tuple[str, Path]]) -> dict[str, dict[str, int]]:
    """Each split's partitions as Parquet under data/<split>/; returns split -> partition -> rows."""
    import pyarrow.parquet as pq

    table = pq.read_table(table_path)
    out: dict[str, dict[str, int]] = {}
    for name, path in splits:
        doc = S.load_split(path, table_path)
        out[name] = {}
        indices = S.partition_indices(doc, table_path)
        order = list(S.DEFAULT_FRACTIONS)  # train, valid, test first; any other partition after, by name
        for part in sorted(indices, key=lambda p: (order.index(p) if p in order else len(order), p)):
            idx = indices[part]
            if not _HF_SPLIT_NAME.fullmatch(part):
                raise HubError(f"split {name}: partition name {part!r} is not a Hugging Face split name (\\w+)")
            dest = stage / DATA_DIR / name / f"{part}.parquet"
            dest.parent.mkdir(parents=True, exist_ok=True)
            pq.write_table(table.take(idx), dest)
            out[name][part] = len(idx)
    return out


def _file_object(path: str, sha: str, size: int, media: str, description: str) -> dict:
    return {
        "@type": "cr:FileObject",
        "@id": path,
        "name": path,
        "description": description,
        "contentUrl": path,
        "contentSize": f"{size} B",
        "encodingFormat": media,
        "sha256": sha,
    }


def croissant(
    *,
    name: str,
    description: str,
    license: str | None,
    url: str | None,
    version: str | None,
    files: list[dict],
    fields: list[dict],
    units: dict[str, str | None],
    descriptions: dict[str, str],
    runs: list[dict],
    record: dict | None,
) -> dict:
    """The Croissant 1.0 document. `files` are FileObjects; the RecordSet reads the table's FileObject."""

    def src(column: str, path: str | None = None) -> dict:
        s: dict = {"fileObject": {"@id": TABLE}, "extract": {"column": column}}
        if path:
            s["transform"] = {"jsonPath": path}
        return s

    rs_fields = []
    for f in fields:
        fid = f"{RECORD_SET}/{f['name']}"
        entry: dict = {
            "@type": "cr:Field",
            "@id": fid,
            "name": f["name"],
            "description": descriptions[f["name"]],
        }
        if f["sub"] is None:
            entry["dataType"] = f["croissant"]
            entry["source"] = src(f["name"])
            if units.get(f["name"]):
                entry["unitText"] = units[f["name"]]
        else:
            entry["repeated"] = True
            entry["subField"] = []
            for s in f["sub"]:
                sub = {
                    "@type": "cr:Field",
                    "@id": f"{fid}/{s['name']}",
                    "name": s["name"],
                    "description": f"{f['name']} element: {s['name']}",
                    "dataType": s["croissant"],
                    "source": src(f["name"], s["name"]),
                }
                if units.get(f"{f['name']}.{s['name']}"):
                    sub["unitText"] = units[f"{f['name']}.{s['name']}"]
                entry["subField"].append(sub)
        rs_fields.append(entry)
    derived = []
    for r in runs:
        activity: dict = {
            "@type": "prov:Activity",
            "name": f"smeltery run {r['run_id']}",
            "prov:wasAssociatedWith": [
                {
                    "@type": "prov:SoftwareAgent",
                    "name": "ferric",
                    "identifier": b["git_sha"],
                    "description": f"ferric build {b['git_sha']} (dirty={b['dirty']}) measured {b['rows']} rows",
                }
                for b in r["ferric_builds"]
            ],
        }
        if r["ferric_unstated"]:
            activity["description"] = "rows whose ferric build is not recorded, by ferric_build_basis: " + ", ".join(
                f"{basis} {n}" for basis, n in r["ferric_unstated"].items()
            )
        entity: dict = {
            "@type": "prov:Entity",
            "name": r["run_id"],
            "identifier": r["run_id"],
            "description": f"smeltery run {r['run_id']} (family {r['family']}), {r['rows']} rows; its core record "
            + (
                f"attestation/run.json has sha256 {r['core_record_sha256']}"
                if r["core_record_sha256"]
                else "was not present at export time (no digest)"
            ),
            "prov:wasGeneratedBy": activity,
        }
        if r["core_record_sha256"]:
            entity["sha256"] = r["core_record_sha256"]
        derived.append(entity)
    doc: dict = {
        "@context": CONTEXT,
        "@type": "sc:Dataset",
        "conformsTo": CROISSANT_VERSION,
        "name": name,
        "description": description,
    }
    if license is not None:
        doc["license"] = croissant_license(license)
    if url is not None:
        doc["url"] = url
    if version is not None:
        doc["version"] = version
    if record is not None:
        doc["dateCreated"] = record.get("created_at")
        doc["identifier"] = f"sha256:{record['content_sha256']}"
    doc["distribution"] = files
    doc["recordSet"] = [
        {
            "@type": "cr:RecordSet",
            "@id": RECORD_SET,
            "name": RECORD_SET,
            "description": f"{D.GRAIN}; every row of {TABLE}",
            "field": rs_fields,
        }
    ]
    doc["prov:wasDerivedFrom"] = derived
    return doc


# ---- the card --------------------------------------------------------------------------------------------------------


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int | float):
        return json.dumps(v)
    return json.dumps(str(v), ensure_ascii=False)  # a JSON string is a YAML double-quoted scalar


def to_yaml(obj, indent: int = 0) -> str:
    """Block YAML for dicts, lists and scalars (no library: the card must be writable with pyarrow alone). Strings are
    always double-quoted, so no value is ever read back as another type."""
    pad = " " * indent
    lines: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, dict | list) and v:
                lines.append(f"{pad}{k}:")
                lines.append(to_yaml(v, indent + 2))
            else:
                lines.append(f"{pad}{k}: {_yaml_scalar(v) if not isinstance(v, dict | list) else json.dumps(v)}")
    elif isinstance(obj, list):
        for item in obj:
            if isinstance(item, dict | list) and item:
                body = to_yaml(item, indent + 2)
                lines.append(f"{pad}- {body[indent + 2 :]}")
            else:
                lines.append(f"{pad}- {_yaml_scalar(item)}")
    else:
        lines.append(f"{pad}{_yaml_scalar(obj)}")
    return "\n".join(lines)


def _size_category(n: int) -> str:
    for limit, label in ((1_000, "n<1K"), (10_000, "1K<n<10K"), (100_000, "10K<n<100K"), (1_000_000, "100K<n<1M")):
        if n < limit:
            return label
    return "n>1M"


def _cell(text) -> str:
    if text is None:
        return ""
    return str(text).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")


def card(
    *,
    title: str,
    license: str | None,
    repo_id: str | None,
    n_rows: int,
    fields: list[dict],
    units: dict[str, str | None],
    quantity_units: dict,
    configs: list[dict],
    dataset_info: list[dict],
    counts: dict[str, dict[str, int]],
    files: list[dict],
    runs: list[dict],
    record: dict | None,
    meta: dict[str, str],
) -> str:
    front: dict = {}
    if license is not None:
        front.update(hf_license(license))
    front["pretty_name"] = title
    front["tags"] = ["smeltery", "croissant"]
    front["size_categories"] = [_size_category(n_rows)]
    front["configs"] = configs
    front["dataset_info"] = dataset_info
    out = ["---", to_yaml(front), "---", "", f"# {title}", ""]
    out.append(
        f"{n_rows} rows, {D.GRAIN}, from {len(runs)} smeltery run(s). Written by `smeltery export hf` from the "
        f"table `smeltery export` wrote (`smeltery.schema_version` {meta.get('smeltery.schema_version')}, "
        f"{meta.get('smeltery.generator')}); this card is generated from the data and its record, not written by hand. "
        f"Machine-readable metadata, with every file's sha256: [`{CROISSANT}`]({CROISSANT}) (Croissant 1.0)."
    )
    out += ["", "## License", ""]
    if license is None:
        out.append(
            "**Not stated.** The source dataset states no license for the data, so none is given here. smeltery's own "
            "`MIT OR Apache-2.0` licenses its code, not these results or the receptors and ligands they were computed "
            "for. Re-export with `--license SPDX` once the terms are known."
        )
    else:
        stated = (record or {}).get("license")
        if stated is not None:
            out.append(f"`{license}` (SPDX), as the dataset record `dataset/attestation/dataset.json` states it.")
        else:
            whose = "; the dataset record states none" if record is not None else ""
            out.append(f"`{license}` (SPDX), as given to `smeltery export hf --license`{whose}.")
    out += ["", "## Loading", "", "```python", "from datasets import load_dataset", ""]
    where = repo_id or "path/to/this/folder"
    out.append(f'ds = load_dataset("{where}", "{DEFAULT_CONFIG}", split="{FULL_SPLIT}")  # every row')
    for c in configs[1:]:
        out.append(f'parts = load_dataset("{where}", "{c["config_name"]}")  # the {c["config_name"]} split')
    out += ["```", "", "## Configurations", "", "| config | split | rows | file |", "|---|---|---|---|"]
    for c in configs:
        n = counts[c["config_name"]]
        for df in c["data_files"]:
            out.append(f"| `{c['config_name']}` | `{df['split']}` | {n[df['split']]} | `{df['path']}` |")
    if len(configs) > 1:
        out += [
            "",
            "A split config's partitions are the rows its split file under `dataset/splits/` names (row identity "
            "`run_id, ligand, target, tier`); groups (scaffolds or targets) never straddle partitions. The split "
            "file's `table.sha256` is the sha256 of `runs.parquet`.",
        ]
    out += ["", "## Columns", "", "| column | type | unit | meaning |", "|---|---|---|---|"]
    for f in fields:
        spelled = f["type"] if f["sub"] is None else "list<struct>"
        out.append(
            f"| `{f['name']}` | `{spelled}` | {_cell(units.get(f['name']))} | {_cell(COLUMN_DESCRIPTIONS[f['name']])} |"
        )
    for f in fields:
        if f["sub"] is not None:
            parts = ", ".join(f"`{x['name']}` ({x['type']})" for x in f["sub"])
            out += ["", f"Each `{f['name']}` element has {parts}."]
    out += [
        "",
        "A unit is given only where one unit holds for every row; otherwise the row's own `score_unit` (or a "
        "`scores` element's `unit`) states it.",
    ]
    out += ["", "### Units of the quantities", "", "| quantity | unit |", "|---|---|"]
    for q, u in quantity_units.items():
        out.append(f"| `{q}` | {_cell(', '.join(u) if isinstance(u, list) else u)} |")
    out += ["", "## Provenance", ""]
    if record is not None:
        out += [
            f"Dataset record `dataset/attestation/dataset.json` (`{record.get('schema')}`): "
            f"dataset_id `{record.get('dataset_id')}`, "
            f"version `{record.get('version')}`, content sha256 `{record.get('content_sha256')}` (the sha256 of "
            f"`dataset/attestation/digests.json`), created {record.get('created_at')} by "
            f"{(record.get('producer') or {}).get('name')} {(record.get('producer') or {}).get('version')}.",
            "",
        ]
    out += [
        "| run_id | family | core record sha256 | rows | ferric builds (rows) | build not recorded (rows) |",
        "|---|---|---|---|---|---|",
    ]
    for r in runs:
        builds = ", ".join(
            f"`{b['git_sha'][:12]}`{' dirty' if b['dirty'] else ''} ({b['rows']})" for b in r["ferric_builds"]
        )
        unstated = ", ".join(f"{basis} ({n})" for basis, n in r["ferric_unstated"].items())
        core = f"`{r['core_record_sha256']}`" if r["core_record_sha256"] else "none"
        out.append(
            f"| `{r['run_id']}` | {_cell(r['family'])} | "
            f"{core} | {r['rows']} | "
            f"{builds or 'none'} | {unstated or '0'} |"
        )
    out += ["", "## Files", "", "| path | bytes | sha256 |", "|---|---|---|"]
    for f in files:
        out.append(f"| `{f['contentUrl']}` | {f['contentSize'].removesuffix(' B')} | `{f['sha256']}` |")
    out += [
        "",
        "## Limits",
        "",
        "- `score` is a mean over poses of values the run record holds, not a new measurement; `scores` carries every "
        "quantity of the tier with its own unit, standard error, count and floor.",
        "- A null floor is unmeasured, not zero; `score_floor_source` says which kind of null.",
        "- A row whose `ferric_git_sha` is null has no recorded ferric build; `ferric_build_basis` says why.",
        "",
    ]
    return "\n".join(out)


# ---- export ----------------------------------------------------------------------------------------------------------


def _fresh(dest: Path) -> None:
    if dest.is_symlink() or dest.is_file():
        raise HubError(f"{dest} exists; the Hugging Face folder is written into a new directory")
    if dest.is_dir() and any(dest.iterdir()):
        raise HubError(f"{dest} exists and is not empty; write the Hugging Face folder to a new directory")


def export_hf(
    src: str | Path,
    dest: str | Path,
    *,
    license: str | None = None,
    title: str | None = None,
    repo_id: str | None = None,
    split_files: list[str | Path] | tuple = (),
    require_license: bool = False,
) -> Path:
    """Write the Hugging Face folder for `src` (a dataset directory or a Parquet table) into the new directory `dest`.

    `repo_id` (OWNER/NAME) sets croissant.json's `url` and the card's loading example; `require_license` refuses, before
    anything is written, when no license is stated (what `--push` needs)."""
    D._pyarrow()
    import pyarrow.parquet as pq

    if repo_id is not None:
        check_repo_id(repo_id)
    source = read_source(src, split_files)
    license = resolve_license(license, source.record)
    if license is None and require_license:
        raise HubError(_NO_LICENSE)
    dest = Path(dest)
    _fresh(dest)
    table = pq.read_table(source.table)
    meta = {k.decode(): v.decode() for k, v in (table.schema.metadata or {}).items()}
    if meta.get("smeltery.schema_version") != str(D.SCHEMA_VERSION):
        raise HubError(
            f"{source.table}: smeltery.schema_version {meta.get('smeltery.schema_version')!r}, this smeltery writes "
            f"{D.SCHEMA_VERSION}: re-export it"
        )
    rows = table.to_pylist()
    fields = _schema_fields(table.schema)
    if [f["name"] for f in fields] != [f.name for f in D.arrow_schema()]:
        raise HubError(f"{source.table}: its columns are not `smeltery export`'s")
    units = D.column_units(rows)
    sub_units = {s["unit"] for r in rows for s in r["scores"]}
    for k in ("value", "sem", "systematic_floor"):
        units[f"scores.{k}"] = _one_unit(sub_units)
    runs = provenance(rows)
    record = source.record
    dataset_id = (record or {}).get("dataset_id") or sorted({r["family"] or r["campaign"] for r in rows})[0]
    title = title or (record or {}).get("name") or dataset_id
    url = HUB_URL + repo_id if repo_id else None

    stage = dest.parent / f".{dest.name}.staging-{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    try:
        # the files copied as-is, under their own paths (a bare table is renamed runs.parquet)
        copies: list[tuple[str, Path]] = []
        if source.root is not None:
            copies = [(rel, source.root / rel) for rel in source.digests]
            table_rel = source.table.relative_to(source.root).as_posix()
        else:
            copies = [(TABLE_NAME, source.table)] + [(f"{S_DIR}/{n}.json", p) for n, p in source.splits]
            table_rel = TABLE_NAME
        if table_rel != TABLE_NAME:
            raise HubError(f"the table is {table_rel}; this layout expects {TABLE_NAME}")
        for rel, p in copies:
            _copy(p, stage / SOURCE_DIR / rel)
            if sha256_file(stage / SOURCE_DIR / rel) != source.digests[rel]:
                raise HubError(f"{p} changed while it was being copied")
        split_rel = {  # source-relative
            n: (p.relative_to(source.root).as_posix() if source.root else f"{S_DIR}/{n}.json") for n, p in source.splits
        }
        parts = _write_partitions(
            stage, stage / TABLE, [(n, stage / SOURCE_DIR / split_rel[n]) for n, _ in source.splits]
        )

        files = []

        def add(rel: str, sha: str, media: str, what: str) -> None:
            files.append(_file_object(rel, sha, (stage / rel).stat().st_size, media, what))

        add(TABLE, source.digests[TABLE_NAME], PARQUET_MEDIA, f"the table: {len(rows)} rows, {D.GRAIN}")
        for n, _ in source.splits:
            add(
                f"{SOURCE_DIR}/{split_rel[n]}",
                source.digests[split_rel[n]],
                "application/json",
                f"smeltery split {n} of {TABLE}",
            )
            for part, k in parts[n].items():
                rel = f"{DATA_DIR}/{n}/{part}.parquet"
                add(rel, sha256_file(stage / rel), PARQUET_MEDIA, f"partition {part} of split {n}: {k} rows of {TABLE}")
        if record is not None:
            add(
                f"{SOURCE_DIR}/{RECORD_FILES[0]}",
                source.digests[RECORD_FILES[0]],
                "application/json",
                "attestation.digests/1 manifest of the dataset directory; its sha256 is the content digest",
            )
            add(
                f"{SOURCE_DIR}/{RECORD_FILES[1]}",
                source.digests[RECORD_FILES[1]],
                "application/json",
                "the attestation.dataset/1 record",
            )

        configs: list[dict] = [
            {"config_name": DEFAULT_CONFIG, "data_files": [{"split": FULL_SPLIT, "path": TABLE}], "default": True}
        ]
        info: list[dict] = [{"config_name": DEFAULT_CONFIG, "features": hf_features(fields)}]
        counts = {DEFAULT_CONFIG: {FULL_SPLIT: len(rows)}}
        for n, _ in source.splits:
            configs.append(
                {
                    "config_name": n,
                    "data_files": [{"split": p, "path": f"{DATA_DIR}/{n}/{p}.parquet"} for p in parts[n]],
                }
            )
            info.append({"config_name": n, "features": hf_features(fields)})
            counts[n] = parts[n]

        description = (
            f"smeltery screening results: {len(rows)} rows, {D.GRAIN}, from {len(runs)} run(s) "
            f"({', '.join(r['run_id'] for r in runs)}). Scores are means of per-pose values with their standard error, "
            "count and systematic floor; units per column where one holds, else per row."
        )
        version = (record or {}).get("version")
        doc = croissant(
            name=dataset_id,
            description=description,
            license=license,
            url=url,
            version=version,
            files=files,
            fields=fields,
            units=units,
            descriptions=COLUMN_DESCRIPTIONS,
            runs=runs,
            record=record,
        )
        (stage / CROISSANT).write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        text = card(
            title=title,
            license=license,
            repo_id=repo_id,
            n_rows=len(rows),
            fields=fields,
            units=units,
            quantity_units=json.loads(meta.get("smeltery.units") or "{}"),
            configs=configs,
            dataset_info=info,
            counts=counts,
            files=files,
            runs=runs,
            record=record,
            meta=meta,
        )
        (stage / CARD).write_text(text, encoding="utf-8")
        if dest.is_dir():
            dest.rmdir()
        stage.rename(dest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return dest


# ---- verification and push -------------------------------------------------------------------------------------------


def verify_folder(folder: str | Path) -> list[str]:
    """Problems with a folder `export_hf` wrote: a FileObject whose file is missing or whose bytes no longer hash to
    the sha256 croissant.json states, or a file croissant.json does not describe (README.md and croissant.json
    themselves aside). Empty = consistent."""
    folder = Path(folder)
    try:
        doc = json.loads((folder / CROISSANT).read_bytes())
    except (OSError, ValueError) as e:
        return [f"{folder / CROISSANT}: {e}"]
    problems, described = [], {CARD, CROISSANT}
    for f in doc.get("distribution") or []:
        rel = f["contentUrl"]
        described.add(rel)
        p = folder / rel
        if not p.is_file():
            problems.append(f"{rel}: missing")
        elif sha256_file(p) != f.get("sha256"):
            problems.append(f"{rel}: sha256 {sha256_file(p)}, croissant.json states {f.get('sha256')}")
    for p in sorted(folder.rglob("*")):
        if p.is_file() and p.relative_to(folder).as_posix() not in described:
            problems.append(f"{p.relative_to(folder).as_posix()}: not described by {CROISSANT}")
    return problems


def check_repo_id(repo_id: str) -> None:
    if not _REPO_ID.fullmatch(repo_id or ""):
        raise HubError(f"repository id {repo_id!r}: give it in full as OWNER/NAME (nothing is pushed to a default)")


@dataclass(frozen=True)
class PushPlan:
    repo_id: str
    url: str
    files: list[tuple[str, int]]  # (relative path, bytes)

    def describe(self) -> str:
        total = sum(n for _, n in self.files)
        lines = [
            f"would upload {len(self.files)} files ({total} bytes) to dataset repository {self.repo_id} ({self.url}):"
        ]
        lines += [f"  {rel}  {n} B" for rel, n in self.files]
        return "\n".join(lines)


def plan_push(folder: str | Path, repo_id: str) -> PushPlan:
    """What `push` would upload; refused unless the folder matches its croissant.json and states a license."""
    check_repo_id(repo_id)
    folder = Path(folder)
    problems = verify_folder(folder)
    if problems:
        raise HubError(f"{folder} does not match its {CROISSANT}:\n  " + "\n  ".join(problems))
    doc = json.loads((folder / CROISSANT).read_bytes())
    if not doc.get("license"):
        raise HubError(_NO_LICENSE)
    if doc.get("url") != HUB_URL + repo_id:
        raise HubError(f"{folder} was generated for {doc.get('url')!r}, not {HUB_URL + repo_id}: re-export it")
    files = [(p.relative_to(folder).as_posix(), p.stat().st_size) for p in sorted(folder.rglob("*")) if p.is_file()]
    return PushPlan(repo_id, HUB_URL + repo_id, files)


def push(folder: str | Path, repo_id: str, *, private: bool = False, api=None) -> PushPlan:
    """Upload `folder` to the dataset repository `repo_id` (created when missing). The token is huggingface_hub's own
    resolution (HF_TOKEN, or the cached login), never an argument. `api` is for tests (an `HfApi`-like object)."""
    plan = plan_push(folder, repo_id)
    if api is None:
        try:
            from huggingface_hub import HfApi
        except ImportError as e:
            raise HubError(
                "pushing to the Hub needs huggingface_hub, the optional `hub` extra: pip install 'smeltery[hub]'"
            ) from e
        api = HfApi()
    try:
        api.create_repo(repo_id, repo_type="dataset", private=True if private else None, exist_ok=True)
        api.upload_folder(
            folder_path=str(folder),
            repo_id=repo_id,
            repo_type="dataset",
            commit_message="smeltery export hf",
        )
    except Exception as e:  # the client's errors (auth, network, permissions) are reported, not swallowed
        raise HubError(f"push to {repo_id} failed: {type(e).__name__}: {e}") from e
    return plan


# ---- CLI -------------------------------------------------------------------------------------------------------------


def _push_or_plan(folder: Path, repo_id: str, confirm: bool, private: bool) -> None:
    if confirm:
        plan = push(folder, repo_id, private=private)
        print(f"uploaded {len(plan.files)} files to {plan.url}")
    else:
        print(plan_push(folder, repo_id).describe())
        print("dry run: nothing uploaded (add --confirm to upload)")


def push_main(argv: list[str]) -> int:
    """`smeltery export hf push FOLDER REPO_ID [--confirm] [--private]`: upload a folder `export hf` wrote."""
    ap = argparse.ArgumentParser(
        prog="smeltery export hf push",
        description="Upload a folder `smeltery export hf` wrote to a Hugging Face dataset repository (`hub` extra). "
        "Every file is re-checked against croissant.json first. A dry run unless --confirm.",
    )
    ap.add_argument("folder")
    ap.add_argument("repo_id", help="OWNER/NAME; must be the one the folder was written for (--push at export)")
    ap.add_argument("--confirm", action="store_true", help="really upload (default: print what would be uploaded)")
    ap.add_argument("--private", action="store_true", help="create the repository private")
    args = ap.parse_args(argv)
    try:
        _push_or_plan(Path(args.folder), args.repo_id, args.confirm, args.private)
    except (D.ExportError, ValueError, OSError) as e:
        print(f"smeltery export hf push: {e}", file=sys.stderr)
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    """`smeltery export hf SRC -o OUT [--license SPDX] [--name TITLE] [--split FILE]... [--push REPO_ID [--confirm]
    [--private]]`, or `smeltery export hf push FOLDER REPO_ID [--confirm] [--private]`."""
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["push"]:
        return push_main(argv[1:])
    ap = argparse.ArgumentParser(
        prog="smeltery export hf",
        description="Write a Hugging Face dataset folder (the Parquet table, a dataset card README.md, croissant.json "
        "with every file's sha256) from a dataset directory (`export dataset`) or a Parquet table (`export parquet`). "
        "No network. --push uploads it (the `hub` extra); without --confirm that is a dry run. "
        "`export hf push --help` uploads a folder written earlier.",
    )
    ap.add_argument("src", help="a dataset directory, or a Parquet file written by `smeltery export parquet`")
    ap.add_argument("-o", "--out", required=True, help="the folder to create (must not exist, or be empty)")
    ap.add_argument(
        "--license",
        help="the DATA's license, an SPDX expression; default: the dataset record's, else not stated (the card says "
        "so and --push refuses). smeltery's own code license is not the data's",
    )
    ap.add_argument(
        "--name", help="the card's title (default: the record's name, else its dataset_id, else the family)"
    )
    ap.add_argument("--split", action="append", default=[], help="a split file made on the table (bare table only)")
    ap.add_argument("--push", metavar="REPO_ID", help="OWNER/NAME of the Hub dataset repository to upload to")
    ap.add_argument("--confirm", action="store_true", help="with --push: really upload (default: print what would be)")
    ap.add_argument("--private", action="store_true", help="with --push: create the repository private")
    args = ap.parse_args(argv)
    if (args.confirm or args.private) and not args.push:
        ap.error("--confirm and --private need --push REPO_ID")
    try:
        out = export_hf(
            args.src,
            args.out,
            license=args.license,
            title=args.name,
            repo_id=args.push,
            split_files=args.split,
            require_license=bool(args.push),
        )
        doc = json.loads((out / CROISSANT).read_bytes())
        print(f"wrote {out}: {CARD}, {CROISSANT}, {len(doc['distribution'])} data files")
        if not doc.get("license"):
            print("license: not stated (pass --license SPDX to state the data's terms)", file=sys.stderr)
        if args.push:
            _push_or_plan(out, args.push, args.confirm, args.private)
    except (D.ExportError, S.SplitError, ValueError, OSError) as e:
        print(f"smeltery export hf: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
