"""Write a pipeline bundle: the saved config.toml, every input file it names (and the other files in its folder) and a
manifest.json (sha256 and size of each), as one zip under `<name>/`.

`bundle_import` reads exactly this shape back. Inputs keep their path relative to the pipeline folder; an absolute
path is stored as `inputs/<basename>` and its original path recorded in the manifest, which is what lets an import
rewrite the config to point at the copy.
"""

from __future__ import annotations

import hashlib
import json
import tomllib
import zipfile
from pathlib import Path
from typing import Any

from smeltery import __version__ as smeltery_version
from smeltery.run_inputs import named_inputs


def _sources(text: str, folder: Path, stored: list[str]) -> tuple[dict[str, Path], dict[str, str], list[str]]:
    """({path in the bundle: file to read}, {path in the bundle: absolute path the config named}, inputs not found)."""
    sources: dict[str, Path] = {n: folder / n for n in stored}
    originals: dict[str, str] = {}
    missing: list[str] = []
    try:
        cfg = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        cfg = {}
    for _table, _key, value in named_inputs(cfg):
        if not value:
            continue
        path = Path(value)
        if path.is_absolute():
            rel, src = f"inputs/{path.name}", path
        else:
            rel, src = path.as_posix(), (folder / path)
            if ".." in path.parts:
                missing.append(value)
                continue
        if not src.is_file():
            # a value such as a PDB id for structure.spec is not a file reference at all
            if path.is_absolute() or "/" in value or "." in path.name:
                missing.append(value)
            continue
        if path.is_absolute():
            # two different absolute files may share a basename: the later one gets a name of its own
            n = 1
            while rel in sources and sources[rel] != src:
                n += 1
                rel = f"inputs/{n}-{path.name}"
            originals[rel] = value
        sources.setdefault(rel, src)
    return sources, originals, missing


def write_bundle(name: str, text: str, folder: Path, stored: list[str], dest: Path) -> None:
    """Write pipeline `name`'s bundle to `dest`. `text` is its saved config, `folder` its folder and `stored` the
    names of the files the store holds in it."""
    sources, originals, missing = _sources(text, folder, stored)
    files: dict[str, Any] = {}
    config_bytes = text.encode()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(f"{name}/config.toml", config_bytes)
        files["config.toml"] = {"sha256": hashlib.sha256(config_bytes).hexdigest(), "size": len(config_bytes)}
        for rel, src in sources.items():
            h = hashlib.sha256()
            with src.open("rb") as r, z.open(f"{name}/{rel}", "w", force_zip64=True) as w:
                while chunk := r.read(1 << 20):
                    h.update(chunk)
                    w.write(chunk)
            files[rel] = {"sha256": h.hexdigest(), "size": src.stat().st_size}
            if rel in originals:
                files[rel]["original_path"] = originals[rel]
        manifest = {"pipeline": name, "smeltery": smeltery_version, "files": files, "missing": missing}
        z.writestr(f"{name}/manifest.json", json.dumps(manifest, indent=2))
