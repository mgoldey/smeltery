"""Dataset directories: an export with its `attestation.dataset/1` record (DRAFT schema), and new versions of one.

`smeltery export parquet RUN... -o runs.parquet` writes one file; nothing outside it says which runs it was made from.
`smeltery export dataset RUN... -o DIR` writes a directory that says so, laid out per attestation-schema's dataset
spec (attestation docs/superpowers/specs/2026-11-19-dataset-records-design.md):

    DIR/runs.parquet                     the table (`dataset_export`, column layout `SCHEMA_VERSION`)
    DIR/splits/<kind>-seed<N>.json       each split asked for at export time (`dataset_splits`)
    DIR/attestation/digests.json         sha256 of every file above; its own sha256 is the dataset's CONTENT digest
    DIR/attestation/dataset.json         the record, written last

in that order, into a staging directory renamed onto DIR at the end, so a half-written dataset never appears under
DIR's name.

* **A dataset directory is immutable.** An existing, non-empty DIR is refused, and so is writing a split file into a
  dataset directory (`dataset_splits.write_split`). `smeltery export split KIND DIR -o NEWDIR` makes a NEW version
  (`new_version`): every file of DIR hard-linked (or copied) into NEWDIR, the new split added, a new record written.
  DIR is never touched, so a run that trained on DIR's content digest still resolves to DIR's record.
* **source_runs** are the exported runs as their CORE records state them (`OUT/attestation/run.json`): `run_id`,
  `family` and the sha256 of run.json's bytes, the digest `attestation_schema.lineage.resolve_lineage` links by. A run
  with no core record is listed with `record_sha256: null` (the resolver then reports it `undigested-source-run`),
  under the run_id and family a core record would have stated (`dataset_export.run_rows`).
* **ferric cross-check.** A row of a single-build run must carry exactly the `build_sha` of the `ferric` dependency in
  its run's core record. A row of a mixed-build run states the build of its own SCFs (`dataset_export`, schema 3): its
  sha must be one of the builds the run used (the core record's `build_sha`, or a `results.builds` sha), else the
  export is refused, naming the row and the run. A row whose build is unknown (null sha) is accepted and COUNTED:
  `ext.smeltery.ferric_rows` = rows, rows per build, `unknown`, `mixed`, and the CLI prints a line when any are not
  stated. A run with no core record has nothing to check against.
* **license** is an SPDX expression the caller states (`--license`), null by default -- never smeltery's own
  `MIT OR Apache-2.0`, which licenses the code, not the receptor's or ligand set's terms.
* **columns** come from `dataset_export.arrow_schema()` (name, the pyarrow type's spelling), with a unit on the score
  and paired-ddE columns only when one unit holds across every row; `ext.smeltery` carries the column layout's
  `schema_version`, grain, units, SMILES forms, the table's path and format, the pyarrow that wrote it (the table's
  bytes, hence the content digest, depend on it), and each split's parameters.

Reading one back (`open_dataset`, which `ExportDataset` and `smeltery export split` use) re-checks the whole directory
with `attestation_schema.conformance.check_dataset_dir`: every file's sha256 and size against the record, the content
digest against digests.json. A caller that knows the digest it wants passes it and gets a refusal on any other.

Needs attestation-schema with `attestation_schema.dataset` (>= `REQUIRED_SCHEMA`, the `attestation` extra) and pyarrow
(the `dataset` extra). Without the former every entry point raises `DatasetError` naming the version it needs.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from . import dataset_export as D
from . import dataset_splits as S

try:
    import attestation_schema as _schema
    from attestation_schema import conformance as _conformance
    from attestation_schema import dataset as _dataset
except ImportError:  # no attestation-schema, or one that predates attestation.dataset/1 (0.1.0)
    _schema = _conformance = _dataset = None

#: The first attestation-schema release with `attestation_schema.dataset` (attestation.dataset/1) and
#: `conformance.check_dataset_dir`. 0.1.0 (attestation 9fb7eed) has neither.
REQUIRED_SCHEMA = "0.2.0"
AVAILABLE = _dataset is not None

TABLE_STEM = "runs"
SPLITS_DIR = "splits"
PRODUCER = "smeltery export"
#: The `ext` namespace this module writes and reads back.
EXT = "smeltery"


class DatasetError(D.ExportError):
    """A dataset directory cannot be written or read (missing schema, existing directory, mismatched digest...)."""


def not_installed() -> str:
    """Why dataset directories are unavailable, naming the release they need and the one installed."""
    try:
        have = f"attestation-schema {metadata.version('attestation-schema')} is installed"
    except metadata.PackageNotFoundError:
        have = "attestation-schema is not installed"
    return (
        f"a dataset directory (attestation.dataset/1) needs attestation-schema >= {REQUIRED_SCHEMA}, the first release "
        f"with attestation_schema.dataset; {have}. pip install 'attestation-schema>={REQUIRED_SCHEMA}'"
    )


def _require() -> None:
    if not AVAILABLE:
        raise DatasetError(not_installed())


def _ds():
    """`attestation_schema.dataset`, or DatasetError naming the release it needs."""
    if _dataset is None or not AVAILABLE:
        raise DatasetError(not_installed())
    return _dataset


def _run_api():
    if _schema is None or not AVAILABLE:
        raise DatasetError(not_installed())
    return _schema


def _conf():
    if _conformance is None or not AVAILABLE:
        raise DatasetError(not_installed())
    return _conformance


@dataclass(frozen=True)
class SplitSpec:
    """A split to make: `dataset_splits.make_split` arguments. `name` is the split's name in the record and its file's
    stem: `<kind>[-generic]-seed<N>`."""

    kind: str
    seed: int = 0
    fractions: dict[str, float] | None = None
    generic: bool = False
    smiles_column: str = "smiles"

    @property
    def name(self) -> str:
        return f"{self.kind}{'-generic' if self.generic else ''}-seed{self.seed}"

    @property
    def path(self) -> str:
        return f"{SPLITS_DIR}/{self.name}.json"


# ---- writing -------------------------------------------------------------------------------------------------------


def _fresh(dest: Path) -> None:
    """Refuse a destination that exists with anything in it: a dataset directory is never written over."""
    if dest.is_symlink() or dest.is_file():
        raise DatasetError(f"{dest} exists; a dataset directory is written once, into a new directory")
    if dest.is_dir() and any(dest.iterdir()):
        raise DatasetError(
            f"{dest} exists and is not empty; a dataset directory is immutable -- write a new version to a new "
            "directory (`smeltery export split KIND OLD -o NEW` for a new split)"
        )


def _staging(dest: Path) -> Path:
    stage = dest.parent / f".{dest.name}.staging-{os.getpid()}"
    shutil.rmtree(stage, ignore_errors=True)
    stage.mkdir(parents=True)
    return stage


def _publish(stage: Path, dest: Path) -> None:
    """Move the finished staging directory onto `dest` (an empty `dest` is replaced; a non-empty one refuses)."""
    if dest.is_dir():
        dest.rmdir()  # empty (checked by _fresh); fails, rather than merging, if something appeared since
    stage.rename(dest)


def listed_ferric_shas(legacy: dict) -> set[str]:
    """Every ferric `git_sha` a legacy record's `results.builds` names (none for a single-build run)."""
    out = set()
    for b in (legacy.get("results") or {}).get("builds") or []:
        sha = (b.get("ferric") or {}).get("git_sha") if isinstance(b.get("ferric"), dict) else None
        if isinstance(sha, str) and sha:
            out.add(sha)
    return out


def check_ferric(
    rows: list[dict], cores: dict[str, tuple[Path, Any]], listed: dict[str, set[str]] | None = None
) -> dict:
    """Check every row's ferric columns against its run's core record; returns what the check could not decide.

    `cores` maps a row's `input_digest` to (run.json path, the strictly read `attestation_schema.RunRecord`), `listed`
    to the ferric shas the legacy record's `results.builds` names. A row whose run has no core record is not checked.

    * a row of a single-build run (`ferric_build_basis` `run_record`) must equal the core record's ferric `build_sha`
      exactly, null included;
    * any other row's non-null sha must be one of the builds the run used: the core `build_sha`, or a sha in
      `results.builds` (the core record's evidence names the same builds, it has no field for them). A sha the run
      never used refuses the export, naming each row and run;
    * a row whose build is unknown (null sha: `unrecorded`, or `mixed_scf_rows`, whose SCFs came from several builds)
      passes, and is counted in the result: {"rows": N, "by_build": {sha: rows}, "unknown": n, "mixed": m}."""
    listed = listed or {}
    bad = []
    summary: dict = {"rows": len(rows), "by_build": {}, "unknown": 0, "mixed": 0}
    for i, row in enumerate(rows):
        sha, basis = row["ferric_git_sha"], row["ferric_build_basis"]
        if sha is None and basis != D.BASIS_RUN:
            summary["mixed" if basis == D.BASIS_MIXED else "unknown"] += 1
        elif sha is not None:
            summary["by_build"][sha] = summary["by_build"].get(sha, 0) + 1
        found = cores.get(row["input_digest"])
        if found is None:
            continue
        path, rec = found
        ferric = [d for d in rec.dependencies or [] if d.name == "ferric"]
        want = ferric[0].build_sha if ferric else None
        if basis == D.BASIS_RUN:
            ok = sha == want
            said = f"lists ferric build_sha {want}" if ferric else "lists no ferric dependency"
        else:
            used = set(listed.get(row["input_digest"], ())) | ({want} if want else set())
            ok = sha is None or sha in used
            said = f"names ferric builds {sorted(used)} (core build_sha and results.builds)"
        if not ok:
            ident = ", ".join(str(row[c]) for c in S.IDENTITY_COLUMNS)
            bad.append(f"row {i} ({ident}): ferric_git_sha {sha}, but run {rec.run_id}'s core record ({path}) {said}")
    if bad:
        more = f" (and {len(bad) - 5} more)" if len(bad) > 5 else ""
        raise DatasetError("the table and the source runs disagree on ferric:\n  " + "\n  ".join(bad[:5]) + more)
    return summary


def _source_runs(paths: list[str | Path]) -> tuple[list[dict], dict[str, tuple[Path, Any]], dict[str, set[str]]]:
    """(`source_runs[]`, input_digest -> (core path, core record), input_digest -> `listed_ferric_shas`) for every
    legacy record under `paths`, in order.
    A core record is read with attestation-schema's strict reader and must be canonical, so the sha256 of its bytes is
    its `record_digest` (what the lineage resolver matches)."""
    out: list[dict] = []
    cores: dict[str, tuple[Path, Any]] = {}
    listed: dict[str, set[str]] = {}
    for arg in paths:
        for legacy in D.find_records(arg):
            ref = D.core_record_ref(legacy)
            legacy_rec = json.loads(legacy.read_bytes())
            listed[legacy_rec["input_digest"]] = listed_ferric_shas(legacy_rec)
            if ref is None:
                entry = {
                    "run_id": str(legacy_rec["input_digest"])[:12],
                    "family": legacy_rec.get("campaign"),
                    "record_sha256": None,
                }
            else:
                try:
                    rec = _run_api().read(ref.path)
                except (ValueError, OSError) as e:  # RecordError is a ValueError
                    raise DatasetError(f"{ref.path}: not a valid core record: {e}") from e
                if _run_api().record_digest(rec) != ref.sha256:
                    raise DatasetError(
                        f"{ref.path} is not in canonical form, so its sha256 is not its record_digest and no lineage "
                        "resolver could match it; rewrite it with `smeltery record core --force`"
                    )
                cores[legacy_rec["input_digest"]] = (ref.path, rec)
                entry = {"run_id": rec.run_id, "family": rec.family, "record_sha256": ref.sha256}
            if entry not in out:
                out.append(entry)
    return out, cores, listed


def _columns(rows: list[dict]) -> list[dict]:
    """`columns[]` from `arrow_schema()`; a unit on the score and paired-ddE columns only when one holds for all."""
    score_units = {r["score_unit"] for r in rows if r["score"] is not None}
    paired_units = {r["score_unit"] for r in rows if r["paired_ddE"] is not None}
    one = {
        "score": score_units,
        "score_sem": score_units,
        "score_floor": score_units,
        "paired_ddE": paired_units,
        "paired_ddE_sem": paired_units,
        "paired_ddE_floor": paired_units,
    }
    out = []
    for field in D.arrow_schema():
        units = one.get(field.name, set())
        unit = next(iter(units)) if len(units) == 1 else None
        out.append({"name": field.name, "type": str(field.type), "unit": unit})
    return out


def _split_ext(doc: dict) -> dict:
    return {
        "kind": doc["kind"],
        "seed": doc["seed"],
        "algorithm_version": doc["algorithm_version"],
        "schema_version": doc["schema_version"],
        "parameters": doc["parameters"],
    }


def _write_split(stage: Path, table: Path, spec: SplitSpec) -> tuple[dict, dict]:
    doc = S.make_split(
        table,
        spec.kind,
        seed=spec.seed,
        fractions=spec.fractions,
        smiles_column=spec.smiles_column,
        generic=spec.generic,
    )
    target = stage / spec.path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(S.dumps(doc), encoding="utf-8")
    entry = {"name": spec.name, "path": spec.path, "sha256": S.table_sha256(target), "kind": spec.kind}
    entry["seed"] = spec.seed
    return entry, _split_ext(doc)


def _identity() -> tuple[dict, dict]:
    """(producer, code) for a record written now, by this process: smeltery's own identity, the same mapping
    `core_record` gives a run (`core_record._code`)."""
    from . import core_record
    from .record import smeltery_identity

    producer = core_record.producer(PRODUCER)
    return producer, core_record._code(smeltery_identity(), producer["version"])


def _record(stage: Path, fields: dict) -> Any:
    """Manifest, then the record built from `fields` plus files and content digest, then dataset.json."""
    content = _ds().write_dataset_manifest(stage)
    files = [asdict(f) for f in _ds().dataset_files(stage)]
    try:
        record = _ds().dataset_from_dict(
            {"schema": _ds().DATASET_SCHEMA, **fields, "content_sha256": content, "files": files}
        )
    except ValueError as e:  # a RecordError names the field
        raise DatasetError(f"the dataset record would not be valid: {e}") from e
    _ds().write_dataset(record, stage)
    return record


def _check_license(license: str | None) -> None:
    if license is not None:
        try:
            _ds().check_spdx(license)
        except ValueError as e:
            raise DatasetError(f"--license {license!r} is not an SPDX license expression: {e}") from e


def export_dataset_dir(
    paths: list[str | Path],
    dest: str | Path,
    *,
    fmt: str = "parquet",
    dataset_id: str | None = None,
    version: str | None = None,
    name: str | None = None,
    license: str | None = None,
    splits: list[SplitSpec] | tuple[SplitSpec, ...] = (),
    canonicalize: bool = True,
) -> Path:
    """Export the runs under `paths` into the new dataset directory `dest`; returns `dest`.

    `dataset_id` defaults to the runs' campaign when they share one (else it must be given); `license` defaults to
    null (not stated). Writes the table, then each split, then the manifest, then the record."""
    _require()
    if fmt not in D.FORMATS:
        raise DatasetError(f"unknown format {fmt!r}; one of {', '.join(D.FORMATS)}")
    dest = Path(dest)
    _fresh(dest)
    _check_license(license)
    names = [s.name for s in splits]
    if len(set(names)) != len(names):
        raise DatasetError(f"two splits share a name: {names}")
    rows = D.export_rows(paths, canonicalize=canonicalize)
    if not rows:
        raise DatasetError("no rows: none of the runs has a per-pose score to export")
    source_runs, cores, listed = _source_runs(paths)
    ferric_rows = check_ferric(rows, cores, listed)
    if dataset_id is None:
        campaigns = sorted({r["campaign"] for r in rows})
        if len(campaigns) != 1:
            raise DatasetError(f"the runs span campaigns {campaigns}: name the dataset with --dataset-id")
        dataset_id = campaigns[0]
    meta = D.file_metadata(rows)
    table_name = f"{TABLE_STEM}.{fmt}"
    stage = _staging(dest)
    try:
        table = stage / table_name
        D.write_rows(rows, table, fmt)
        split_entries, split_ext = [], {}
        for spec in splits:
            entry, ext = _write_split(stage, table, spec)
            split_entries.append(entry)
            split_ext[spec.name] = ext
        producer, code = _identity()
        from .core_record import utc_now

        _record(
            stage,
            {
                "dataset_id": dataset_id,
                "name": name,
                "version": version,
                "created_at": utc_now(),
                "producer": producer,
                "code": code,
                "rows": len(rows),
                "columns": _columns(rows),
                "splits": split_entries,
                "source_runs": source_runs,
                "license": license,
                "ext": {
                    EXT: {
                        "schema_version": D.SCHEMA_VERSION,
                        "grain": D.GRAIN,
                        "units": json.loads(meta["smeltery.units"]),
                        "smiles_form": meta["smeltery.smiles_form"].split(","),
                        "generator": meta["smeltery.generator"],
                        "table": {"path": table_name, "format": fmt},
                        # The table's bytes, hence the content digest, depend on the writer's version; the record
                        # has no slot for the exporter's environment, so it is stated here.
                        "writer": {"pyarrow": metadata.version("pyarrow")},
                        "splits": split_ext,
                        # What the ferric cross-check could not decide: rows whose build is not recorded
                        "ferric_rows": ferric_rows,
                    }
                },
            },
        )
        _publish(stage, dest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return dest


# ---- reading -------------------------------------------------------------------------------------------------------


def is_dataset_dir(path: str | Path) -> bool:
    """`path` is a directory holding a dataset record. Needs nothing beyond the standard library."""
    return (Path(path) / "attestation" / "dataset.json").is_file()


def inside_dataset_dir(path: str | Path) -> Path | None:
    """The dataset directory `path` would be written into (itself or an ancestor), or None."""
    p = Path(path).resolve()
    for d in (p, *p.parents):
        if is_dataset_dir(d):
            return d
    return None


@dataclass(frozen=True)
class OpenedDataset:
    """A dataset directory that passed the conformance check: its record and the path of its table."""

    root: Path
    record: Any  # attestation_schema.DatasetRecord
    table: Path

    def split(self, which: str | Path) -> tuple[str, Path]:
        """(name, path) of the split named `which`, or whose path (relative to the directory) is `which`."""
        for s in self.record.splits:
            if str(which) in (s.name, s.path):
                return s.name, self.root / s.path
        known = [s.name for s in self.record.splits]
        raise S.SplitError(f"{self.root} has no split {str(which)!r}; it has {known}")

    def lineage_entries(self, *, role: str = "train", split: str | None = None) -> list[dict]:
        """The `data[]` entries a run consuming this dataset lists in its core record: the dataset by its content
        digest and, with `split`, that split's file by its digest (`attestation_schema.dataset_lineage_entries`)."""
        return [asdict(e) for e in _ds().dataset_lineage_entries(self.record, role=role, split=split)]


def _table_path(root: Path, record: Any) -> Path:
    ours = (record.ext.get(EXT) or {}).get("table") or {}
    if isinstance(ours.get("path"), str):
        return root / ours["path"]
    tables = [f.path for f in record.files if Path(f.path).suffix in (".parquet", ".arrow")]
    if len(tables) != 1:
        raise DatasetError(f"{root}: cannot tell which file is the table ({tables}); it was not written by smeltery")
    return root / tables[0]


def open_dataset(path: str | Path, *, expect_sha256: str | None = None) -> OpenedDataset:
    """Open a dataset directory after checking it (`conformance.check_dataset_dir`: the record, the content digest, and
    every file's sha256 and size). `expect_sha256`: the content digest the caller trained or will train on; any other
    is refused, so a run that names a digest cannot silently read another version."""
    _require()
    root = Path(path)
    if not is_dataset_dir(root):
        raise DatasetError(f"{root} is not a dataset directory (no attestation/dataset.json)")
    report = _conf().check_dataset_dir(root)
    if not report.ok:
        raise DatasetError(f"{root} does not match its dataset record:\n{report.format()}")
    record = report.dataset
    assert record is not None  # a conforming dataset directory's report carries its record
    if expect_sha256 is not None and record.content_sha256 != expect_sha256:
        raise DatasetError(f"{root} has content digest {record.content_sha256}, not the expected {expect_sha256}")
    return OpenedDataset(root, record, _table_path(root, record))


# ---- new versions --------------------------------------------------------------------------------------------------


def next_version(version: str | None) -> str | None:
    """`3` -> `4`, `v3` -> `v4`; None for any other label (the caller must name the new version)."""
    m = re.fullmatch(r"(v?)(\d+)", version or "")
    return f"{m[1]}{int(m[2]) + 1}" if m else None


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


def new_version(
    src: str | Path, dest: str | Path, *, split: SplitSpec, version: str | None = None, expect_sha256: str | None = None
) -> Path:
    """A new version of the dataset at `src`, in the new directory `dest`, with `split` added; returns `dest`.

    Every file of `src` is hard-linked (or copied) into `dest` under the same path, the split is made on that table and
    written beside the others, and a new manifest and record follow. The record keeps `dataset_id`, `name`, `license`,
    `rows`, `columns` and `source_runs` (the data came from the same runs), takes `version` (default: `src`'s bumped,
    `next_version`) and states where it came from in `ext.smeltery.derived_from`. `src` is never written."""
    _require()
    old = open_dataset(src, expect_sha256=expect_sha256)
    dest = Path(dest)
    a, b = old.root.resolve(), dest.resolve()
    if a == b or a in b.parents or b in a.parents:
        raise DatasetError(f"{dest} and {old.root} overlap; a new version goes in a directory of its own")
    _fresh(dest)
    if any(s.name == split.name for s in old.record.splits):
        raise DatasetError(f"{old.root} already has split {split.name!r}")
    version = version if version is not None else next_version(old.record.version)
    if version is None:
        raise DatasetError(f"{old.root} has version {old.record.version!r}: name the new one (--version)")
    ext = json.loads(json.dumps(old.record.ext))  # a deep copy of plain JSON
    ours = ext.setdefault(EXT, {})
    stage = _staging(dest)
    try:
        for f in old.record.files:
            _link_or_copy(old.root / f.path, stage / f.path)
            if S.table_sha256(stage / f.path) != f.sha256:  # changed since open_dataset checked it
                raise DatasetError(f"{old.root / f.path} changed while the new version was being written")
        entry, split_ext = _write_split(stage, stage / old.table.relative_to(old.root), split)
        ours.setdefault("splits", {})[split.name] = split_ext
        ours["derived_from"] = {
            "dataset_sha256": _ds().dataset_digest(old.record),
            "content_sha256": old.record.content_sha256,
            "version": old.record.version,
            "added": [split.path],
        }
        producer, code = _identity()
        from .core_record import utc_now

        r = old.record
        _record(
            stage,
            {
                "dataset_id": r.dataset_id,
                "name": r.name,
                "version": version,
                "created_at": utc_now(),
                "producer": producer,
                "code": code,
                "rows": r.rows,
                "columns": None if r.columns is None else [asdict(c) for c in r.columns],
                "splits": [asdict(s) for s in r.splits] + [entry],
                "source_runs": None if r.source_runs is None else [asdict(s) for s in r.source_runs],
                "license": r.license,
                "ext": ext,
            },
        )
        _publish(stage, dest)
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    return dest


# ---- CLI -------------------------------------------------------------------------------------------------------------


def parse_split(text: str) -> SplitSpec:
    """`KIND` or `KIND:SEED` (`scaffold`, `target:3`)."""
    kind, _, seed = text.partition(":")
    if kind not in S.KINDS:
        raise argparse.ArgumentTypeError(f"unknown split kind {kind!r}; known: {', '.join(S.KINDS)}")
    try:
        return SplitSpec(kind, int(seed) if seed else 0)
    except ValueError:
        raise argparse.ArgumentTypeError(f"seed {seed!r} is not an integer") from None


def parse_fractions(text: str | None) -> dict[str, float] | None:
    if not text:
        return None
    return {k: float(v) for k, v in (kv.split("=", 1) for kv in text.split(","))}


def main(argv: list[str] | None = None) -> int:
    """`smeltery export dataset RUN... -o DIR [--format parquet|arrow] [--dataset-id ID] [--version V] [--name N]
    [--license SPDX] [--split KIND[:SEED]]... [--fractions ...]`."""
    ap = argparse.ArgumentParser(
        prog="smeltery export dataset",
        description="Write finished runs as a dataset DIRECTORY: the table, its splits, and an attestation.dataset/1 "
        "record naming the core run records it was made from. The directory is immutable once written. Needs the "
        f"`dataset` extra (pyarrow) and attestation-schema >= {REQUIRED_SCHEMA}.",
    )
    ap.add_argument("runs", nargs="+", help="run OUT directories or run-*.json files")
    ap.add_argument("-o", "--out", required=True, help="the dataset directory to create (must not exist, or be empty)")
    ap.add_argument("--format", choices=D.FORMATS, default="parquet")
    ap.add_argument("--dataset-id", help="the dataset's stable name across versions (default: the runs' campaign)")
    ap.add_argument("--version", help="this version's label (e.g. 1, v1, 2026-11-19); default: none")
    ap.add_argument("--name", help="a human title")
    ap.add_argument(
        "--license",
        help="the DATA's license, an SPDX expression; default: not stated (null). smeltery's own code license is "
        "not the data's",
    )
    ap.add_argument("--split", action="append", type=parse_split, default=[], help="KIND[:SEED], repeatable")
    ap.add_argument("--fractions", help="for every --split: e.g. train=0.8,valid=0.1,test=0.1 (the default)")
    ap.add_argument("--no-canonical-smiles", action="store_true", help="keep SMILES as recorded")
    args = ap.parse_args(argv)
    try:
        fr = parse_fractions(args.fractions)
        specs = [SplitSpec(s.kind, s.seed, fr) for s in args.split]
        out = export_dataset_dir(
            args.runs,
            args.out,
            fmt=args.format,
            dataset_id=args.dataset_id,
            version=args.version,
            name=args.name,
            license=args.license,
            splits=specs,
            canonicalize=not args.no_canonical_smiles,
        )
        record = _ds().read_dataset(out)
    except (D.ExportError, S.SplitError, ValueError) as e:
        print(f"smeltery export dataset: {e}", file=sys.stderr)
        return 2
    print(f"wrote dataset {record.dataset_id} ({record.rows} rows) to {out}")
    print(f"content sha256 {record.content_sha256}")
    fr = (record.ext.get(EXT) or {}).get("ferric_rows") or {}
    if fr.get("unknown") or fr.get("mixed"):
        print(
            f"ferric build not stated for {fr['unknown'] + fr['mixed']} of {fr['rows']} rows "
            f"({fr['unknown']} unrecorded, {fr['mixed']} from several builds); see ext.smeltery.ferric_rows",
            file=sys.stderr,
        )
    return 0
