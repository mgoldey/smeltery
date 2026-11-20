"""Copy the files a `smeltery run` config names into the run directory, so the run re-runs from there alone.

Also the hashes that say whether two configs stage, or pose, the same: `staged_sha256` (the config text staging would
write) and `poses_key` (everything upstream of the poses, files by content).
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import tomllib
from pathlib import Path

from smeltery import config_text
from smeltery.methods import registry
from smeltery.record import file_sha256

#: Input files the config names outside any method's section: the decisions JSON (an input of the Ligands step).
_OTHER_INPUT_PATHS: tuple[tuple[str, str], ...] = (("decisions", "file"),)


def input_paths() -> tuple[tuple[str, str], ...]:
    """(table, key) of every config value that may name an input file: each registration's `file_knobs` under its
    section (`smeltery.methods.registry()`, in catalog order), then the decisions file."""
    own = tuple((s.section, k) for s in registry().values() if s.section is not None for k in s.file_knobs)
    return own + _OTHER_INPUT_PATHS


def named_inputs(cfg: dict):
    """(table, key, value) for each `input_paths()` entry a parsed config sets to a string, in that order. Read
    lazily: a caller may replace `cfg[table][key]` as it goes."""
    for table, key in input_paths():
        value = cfg.get(table, {}).get(key) if isinstance(cfg.get(table), dict) else None
        if isinstance(value, str):
            yield table, key, value


def __getattr__(name: str):
    if name == "INPUT_PATHS":  # derived on every read, so a registration made later is staged
        return input_paths()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


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
    known = _manifest(out_dir)
    cfg = tomllib.loads(text)
    staged: dict[str, dict] = {}
    for table, key, value in named_inputs(cfg):
        p = Path(value)
        src = p if p.is_absolute() else Path(config_dir) / p
        if not src.is_file():
            continue
        digest = file_sha256(src)
        source = known.get(value, {}).get("source", value) if value == f"inputs/{digest}/{src.name}" else value
        rel = f"inputs/{digest}/{src.name}"
        dest = Path(out_dir) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(src, dest)
        text = config_text.set_value(text, f"{table}.{key}", rel)
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
            raise config_text.ConfigTextError(f"{dotted} did not take the staged path {info['copy']!r}")
    return text, staged


def staged_sha256(text: str, config_dir: Path) -> str:
    """The sha256 of the config `stage_inputs` would write, computed by staging into a scratch directory: the staged
    text names each input by content (`inputs/<sha>/<name>`), not by OUT, so it is the same text OUT would get."""
    with tempfile.TemporaryDirectory(prefix="smeltery-stage-") as tmp:
        staged, _ = stage_inputs(text, Path(config_dir), Path(tmp))
    return hashlib.sha256(staged.encode()).hexdigest()


def write_staged(text: str, config_dir: Path, out_dir: Path) -> tuple[Path, str, dict[str, dict]]:
    """Stage `text` into `out_dir` (`stage_inputs`) and write it as `out_dir/config.toml`, only when that file does not
    already hold exactly it (a live run reads it). Returns (that path, the staged text, the staged inputs). Whether
    anything may be written into `out_dir` at all is the caller's to decide first."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    staged, inputs = stage_inputs(text, Path(config_dir), out_dir)
    dest = out_dir / "config.toml"
    if not dest.is_file() or dest.read_text() != staged:
        dest.write_text(staged)
    return dest, staged, inputs


# ---------------------------------------------------------------- which edits move the poses


def pose_sections() -> tuple[str, ...]:
    """The config sections that decide the poses; an edit outside them leaves every pose where it was: the sections
    of the methods on the input and geometry rungs (`smeltery.methods.registry()`), and the pipeline's own
    candidates, parent and decisions."""
    own = tuple(s.section for s in registry().values() if s.section is not None and s.rung in ("input", "geometry"))
    return (*own, "candidates", "parent", "decisions")


def poses_key(cfg: dict, base: Path) -> str:
    """A hash of everything upstream of the poses in a raw config (`tomllib` output). A file the config names is
    hashed by content, so a run's staged copy (`inputs/<sha>/name`) and the original path give the same key."""
    parts: dict = {}
    for sec in pose_sections():
        if sec in cfg:
            parts[sec] = json.loads(json.dumps(cfg[sec], sort_keys=True, default=str))
    for table, key, v in named_inputs(parts):
        f = Path(v) if Path(v).is_absolute() else Path(base) / v
        if f.is_file():
            parts[table][key] = {"sha256": file_sha256(f)}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()
