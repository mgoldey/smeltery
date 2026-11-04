"""Copy the files a `smeltery run` config names into the run directory, so the run re-runs from there alone."""

from __future__ import annotations

import hashlib
import shutil
import tomllib
from pathlib import Path

from smeltery.gui.config_text import ConfigTextError, set_value

INPUT_PATHS: tuple[tuple[str, str], ...] = (
    ("structure", "spec"),
    ("pocket", "file"),
    ("docking", "receptor"),
    ("decisions", "file"),
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def stage_inputs(text: str, config_dir: Path, out_dir: Path) -> tuple[str, dict[str, dict]]:
    """Copy each `INPUT_PATHS` value that names an existing file (relative paths resolve against `config_dir`, as
    `smeltery run` does) to `out_dir/inputs/<sha256><suffix>` and return the config text with that value rewritten
    to the relative path `inputs/<sha256><suffix>`, plus `{"pocket.file": {"source", "sha256", "copy"}, ...}`.
    A value that is not a file (a PDB id for `structure.spec`) is left alone and not listed."""
    cfg = tomllib.loads(text)
    staged: dict[str, dict] = {}
    for table, key in INPUT_PATHS:
        value = cfg.get(table, {}).get(key) if isinstance(cfg.get(table), dict) else None
        if not isinstance(value, str):
            continue
        p = Path(value)
        src = p if p.is_absolute() else Path(config_dir) / p
        if not src.is_file():
            continue
        digest = _sha256(src)
        rel = f"inputs/{digest}{src.suffix}"
        dest = Path(out_dir) / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.exists():
            shutil.copyfile(src, dest)
        text = set_value(text, f"{table}.{key}", rel)
        staged[f"{table}.{key}"] = {"source": value, "sha256": digest, "copy": rel}
    check = tomllib.loads(text)  # set_value does not re-read its output
    for dotted, info in staged.items():
        table, key = dotted.split(".")
        if check.get(table, {}).get(key) != info["copy"]:
            raise ConfigTextError(f"{dotted} did not take the staged path {info['copy']!r}")
    return text, staged
