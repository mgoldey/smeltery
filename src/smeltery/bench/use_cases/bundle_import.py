"""Unpack a pipeline bundle (the zip `GET /api/pipelines/{name}/bundle` writes) into a staging folder, safely.

Nothing in the zip is trusted: entry names, the sizes it declares and its manifest are all checked, and a bundle with
any problem is refused whole (the caller creates nothing). Problems come back per entry as `(path, message)`.
"""

from __future__ import annotations

import hashlib
import json
import re
import stat
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

#: One path component of an unpacked input: a plain name, no leading dot (the store's upload rule).
COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 ()+._-]{0,127}")
MAX_ENTRIES = 500
MAX_FILE = 64 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024


class BundleRefused(Exception):
    """The zip as a whole is not acceptable (not a zip, too many entries, too large, no single top folder)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Unpacked:
    top: str
    pipeline: str | None
    config: str
    files: dict[str, Path]  # relative path -> staged file (config.toml and manifest.json excluded)
    originals: dict[str, str]  # relative path -> the absolute path the config named when it was exported
    errors: list[tuple[str, str]] = field(default_factory=list)


def _bad_name(name: str) -> str | None:
    if "\\" in name or "\x00" in name:
        return "backslash or NUL in the entry name"
    p = PurePosixPath(name)
    if name.startswith("/") or p.is_absolute() or re.match(r"^[A-Za-z]:", name):
        return "absolute path"
    if ".." in p.parts:
        return "path leaves the bundle ('..')"
    return None


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
            raise BundleRefused(f"more than {max_entries} entries", 413)
        if sum(i.file_size for i in infos) > max_total:
            raise BundleRefused(f"more than {max_total // (1024 * 1024)} MB uncompressed", 413)
        errors: list[tuple[str, str]] = []
        entries: dict[str, zipfile.ZipInfo] = {}
        for i in infos:
            why = _bad_name(i.filename)
            if why is None and stat.S_ISLNK(i.external_attr >> 16):
                why = "symbolic link"
            if why is None and i.file_size > max_file:
                why = f"larger than {max_file // (1024 * 1024)} MB"
            if why is not None:
                errors.append((i.filename, why))
            else:
                entries[i.filename] = i
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
            assert isinstance(listed, dict)
        except (ValueError, KeyError, AssertionError) as e:
            raise BundleRefused("manifest.json is not a bundle manifest") from e
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
            if rel == "manifest.json" or not isinstance(meta, dict):
                continue
            if rel != "config.toml" and not all(COMPONENT.fullmatch(c) for c in PurePosixPath(rel).parts):
                errors.append((rel, "file name not allowed (plain names only, no leading dot)"))
                continue
            dest = staging / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            h, n = hashlib.sha256(), 0
            with z.open(info) as r, dest.open("wb") as w:
                while chunk := r.read(1 << 20):  # count what is really there: the declared size can lie
                    n += len(chunk)
                    spent += len(chunk)
                    if n > max_file or spent > max_total:
                        raise BundleRefused("the bundle expands to more than its declared size", 413)
                    h.update(chunk)
                    w.write(chunk)
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
    return Unpacked(top, pipeline if isinstance(pipeline, str) else None, config, files, originals, errors)
