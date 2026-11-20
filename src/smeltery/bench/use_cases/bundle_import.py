"""Unpack a pipeline bundle (the zip `GET /api/pipelines/{name}/bundle` writes) into a staging folder, safely.

Nothing in the zip is trusted: entry names, the sizes it declares and its manifest are all checked, and a bundle with
any problem is refused whole (the caller creates nothing). Problems come back per entry as `(path, message)`.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import tomllib
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from smeltery.backup import unsafe_member_name
from smeltery.bench.ports.pipeline_store import FILENAME
from smeltery.config_text import set_value
from smeltery.run_inputs import named_inputs

MAX_ENTRIES = 500
#: One file of a bundle, and (as `api.common.MAX_UPLOAD`) one uploaded input: the one 64 MB cap.
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
_DAMAGED = (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, OSError)


class BundleRefused(Exception):
    """The zip as a whole is not acceptable (not a zip, too many entries, too large, no single top folder).
    `too_large` is true when the refusal is about size or count rather than shape."""

    def __init__(self, message: str, too_large: bool = False):
        super().__init__(message)
        self.too_large = too_large


@dataclass
class Unpacked:
    top: str
    pipeline: str | None
    config: str
    files: dict[str, Path]  # relative path -> staged file (config.toml and manifest.json excluded)
    originals: dict[str, str]  # relative path -> the absolute path the config named when it was exported
    errors: list[tuple[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # inputs the exporter could not find


def _bad_name(name: str) -> str | None:
    return unsafe_member_name(name, container="bundle", member="entry")


def unpack(zip_path: Path, staging: Path, *, max_entries=None, max_file=None, max_total=None) -> Unpacked:
    max_entries = MAX_ENTRIES if max_entries is None else max_entries
    max_file = MAX_FILE if max_file is None else max_file
    max_total = MAX_TOTAL if max_total is None else max_total
    try:
        z = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile as e:
        raise BundleRefused("not a zip file") from e
    with z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(z.infolist()) > max_entries:
            raise BundleRefused(f"more than {max_entries} entries", too_large=True)
        if sum(i.file_size for i in infos) > max_total:
            raise BundleRefused(f"more than {max_total // (1024 * 1024)} MB uncompressed", too_large=True)
        errors: list[tuple[str, str]] = []
        entries: dict[str, zipfile.ZipInfo] = {}
        for i in infos:
            why = _bad_name(i.filename)
            norm = PurePosixPath(i.filename).as_posix()
            if why is None and norm in entries:
                why = f"repeats the entry {norm}" if norm == i.filename else f"names the same file as {norm}"
            if why is None and stat.S_ISLNK(i.external_attr >> 16):
                why = "symbolic link"
            if why is None and i.file_size > max_file:
                why = f"larger than {max_file // (1024 * 1024)} MB"
            if why is not None:
                errors.append((i.filename, why))
            else:
                entries[norm] = i
        if errors:  # name problems make "the single top folder" meaningless: report them as they are
            return Unpacked("", None, "", {}, {}, errors)
        tops = {PurePosixPath(n).parts[0] for n in entries}
        if len(tops) != 1 or any(len(PurePosixPath(n).parts) < 2 for n in entries):
            raise BundleRefused("a bundle holds exactly one top folder: <name>/config.toml, ...")
        top = tops.pop()
        if f"{top}/config.toml" not in entries:
            raise BundleRefused(f"{top}/config.toml is missing")
        if f"{top}/manifest.json" not in entries:
            raise BundleRefused(f"{top}/manifest.json is missing: only bundles from Download bundle can be imported")
        try:
            manifest = json.loads(z.read(entries[f"{top}/manifest.json"]).decode("utf-8"))
            listed = manifest["files"]
        except (ValueError, KeyError, TypeError) as e:
            raise BundleRefused("manifest.json is not a bundle manifest") from e
        except _DAMAGED as e:
            raise BundleRefused(f"manifest.json cannot be read from the zip: {e}") from e
        if not isinstance(listed, dict):
            raise BundleRefused("manifest.json is not a bundle manifest")
        rels = {n[len(top) + 1 :]: i for n, i in entries.items()}
        for rel in rels:
            if rel != "manifest.json" and rel not in listed:
                errors.append((rel, "not listed in manifest.json"))
        for rel in listed:
            if rel not in rels:
                errors.append((rel, "listed in manifest.json but not in the zip"))
        files: dict[str, Path] = {}
        originals: dict[str, str] = {}
        config = ""
        spent = 0
        for rel, info in rels.items():
            meta = listed.get(rel)
            if rel == "manifest.json" or rel not in listed:
                continue
            if not isinstance(meta, dict):
                errors.append((rel, "its manifest.json entry is not an object"))
                continue
            # each path component is held to the store's upload rule: a plain name, no leading dot
            if rel != "config.toml" and not all(FILENAME.fullmatch(c) for c in PurePosixPath(rel).parts):
                errors.append((rel, "file name not allowed (plain names only, no leading dot)"))
                continue
            dest = staging / rel
            h, n = hashlib.sha256(), 0
            try:
                dest.parent.mkdir(parents=True, exist_ok=True)
                with z.open(info) as r, dest.open("wb") as w:
                    while chunk := r.read(1 << 20):  # count what is really there: the declared size can lie
                        n += len(chunk)
                        spent += len(chunk)
                        if n > max_file or spent > max_total:
                            raise BundleRefused("the bundle expands to more than its declared size", too_large=True)
                        h.update(chunk)
                        w.write(chunk)
            except _DAMAGED as e:  # bad CRC, encrypted, unsupported compression, a file where a folder is needed
                errors.append((rel, f"cannot be extracted: {e}"))
                continue
            if h.hexdigest() != meta.get("sha256"):
                errors.append((rel, "sha256 does not match manifest.json"))
                continue
            if rel == "config.toml":
                try:
                    config = dest.read_bytes().decode("utf-8")
                except UnicodeDecodeError:
                    errors.append((rel, "not UTF-8 text"))
                continue
            files[rel] = dest
            if isinstance(meta.get("original_path"), str):
                originals[rel] = meta["original_path"]
    pipeline = manifest.get("pipeline")
    missing = [m for m in manifest.get("missing") or [] if isinstance(m, str)] if isinstance(manifest, dict) else []
    return Unpacked(top, pipeline if isinstance(pipeline, str) else None, config, files, originals, errors, missing)


def _escapes(value: str) -> bool:
    p = Path(value)
    return p.is_absolute() or ".." in p.parts or value.startswith("~") or bool(re.match(r"^[A-Za-z]:", value))


def rewrite_inputs(text: str, originals: dict[str, str]) -> tuple[str, list[str], list[tuple[str, str]]]:
    """Point the config's input paths at the bundle's copies: (new text, notes, errors).

    `originals` is `Unpacked.originals`, {relative path: the absolute path the config named when it was exported}.
    An input naming one of those is rewritten to its copy; any other absolute, `~` or `..` input path is an error,
    so a hostile config cannot point a run at a file outside the pipeline. A copy the config no longer names is an
    error too. `text` must be TOML."""
    cfg = tomllib.loads(text)
    by_original = {orig: rel for rel, orig in originals.items()}
    used: set[str] = set()
    notes: list[str] = []
    errors: list[tuple[str, str]] = []
    for table, key, value in named_inputs(cfg):
        if value in by_original:
            text = set_value(text, f"{table}.{key}", by_original[value])
            notes.append(f"{table}.{key}: {value} -> {by_original[value]}")
            used.add(value)
        elif _escapes(value):
            errors.append((f"{table}.{key}", f"{value!r} points outside the bundle and was not exported with it"))
    errors += [(rel, f"the config does not name {orig}") for orig, rel in by_original.items() if orig not in used]
    return text, notes, errors
