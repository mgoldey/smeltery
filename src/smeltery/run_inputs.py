"""Copy the files a `smeltery run` config names into the run directory, so the run re-runs from there alone."""

from __future__ import annotations

import hashlib
import json
import shutil
import tomllib
from pathlib import Path

#: Input files the config names outside any method's section: the decisions JSON (an input of the Ligands step).
_OTHER_INPUT_PATHS: tuple[tuple[str, str], ...] = (("decisions", "file"),)


def input_paths() -> tuple[tuple[str, str], ...]:
    """(table, key) of every config value that may name an input file: each registration's `file_knobs` under its
    section (`smeltery.methods.registry()`, in catalog order), then the decisions file."""
    from smeltery.methods import registry

    own = tuple((s.section, k) for s in registry().values() if s.section is not None for k in s.file_knobs)
    return own + _OTHER_INPUT_PATHS


def __getattr__(name: str):
    if name == "INPUT_PATHS":  # derived on every read, so a registration made later is staged
        return input_paths()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


MANIFEST = "inputs/manifest.json"


def _manifest(out_dir: Path) -> dict[str, dict]:
    try:
        return json.loads((Path(out_dir) / MANIFEST).read_text())
    except (OSError, ValueError):
        return {}


def stage_inputs(text: str, config_dir: Path, out_dir: Path) -> tuple[str, dict[str, dict]]:
    """Copy each `input_paths()` value that names an existing file (relative paths resolve against `config_dir`, as
    `smeltery run` does) to `out_dir/inputs/<sha256>/<basename>` and return the config text with that value rewritten
    to the relative path `inputs/<sha256>/<basename>`, plus `{"pocket.file": {"source", "sha256", "copy"}, ...}`.
    A value that is not a file (a PDB id for `structure.spec`) is left alone and not listed.

    `out_dir/inputs/manifest.json` maps each copy to the source it was first staged from. A value that already names
    a copy inside `out_dir` (a config staged earlier, e.g. by Bench's Launch and then again by `smeltery run`) keeps
    that recorded source instead of becoming "inputs/<sha>/<name>", so the manifest is the one source of truth.

    The original basename is kept so every name the plan records (and therefore the input digest) is the same with
    and without staging; the contents stay addressed by the hash directory."""
    # Imported here, not at module level: staging is core `smeltery run`, while config_text needs tomlkit, which
    # only the `gui` and `bench` extras install. A module-level import makes `smeltery run` need the GUI's deps.
    from smeltery.gui.config_text import ConfigTextError, set_value

    known = _manifest(out_dir)
    cfg = tomllib.loads(text)
    staged: dict[str, dict] = {}
    for table, key in input_paths():
        value = cfg.get(table, {}).get(key) if isinstance(cfg.get(table), dict) else None
        if not isinstance(value, str):
            continue
        p = Path(value)
        src = p if p.is_absolute() else Path(config_dir) / p
        if not src.is_file():
            continue
        digest = _sha256(src)
        source = known.get(value, {}).get("source", value) if value == f"inputs/{digest}/{src.name}" else value
        rel = f"inputs/{digest}/{src.name}"
        dest = Path(out_dir) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(src, dest)
        text = set_value(text, f"{table}.{key}", rel)
        staged[f"{table}.{key}"] = {"source": source, "sha256": digest, "copy": rel}
        known[rel] = {"source": source, "sha256": digest}
    if staged:  # rewritten only when it changes: re-staging an identical config touches nothing
        manifest, body = Path(out_dir) / MANIFEST, json.dumps(known, indent=2, sort_keys=True)
        if not manifest.is_file() or manifest.read_text() != body:
            manifest.write_text(body)
    check = tomllib.loads(text)  # set_value does not re-read its output
    for dotted, info in staged.items():
        table, key = dotted.split(".")
        if check.get(table, {}).get(key) != info["copy"]:
            raise ConfigTextError(f"{dotted} did not take the staged path {info['copy']!r}")
    return text, staged
