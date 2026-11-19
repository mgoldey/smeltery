"""The target definition file: a `.toml` describing a binding pocket, and nothing else (docs/targets.md).

A screen is a target plus ligands plus a measurement. The ligands come from the caller (`ligand_set`) and the
measurement from the target class (`smeltery.mcp.targets`); a target file holds only what describes the target:

    schema_version = 1        # required; the only version there is
    [pocket] / [[pocket.site]]  the point-charge field: a .pdb/.pqr file, or explicit charges
    [structure]               a receptor structure to prepare (a .pdb path, a PDB id, an AlphaFold accession)
    [docking]                 a prepared receptor and box to dock the reference molecule into
    [gates]                   which pose checks to run before any calculation

Each section is the campaign config's own section, with the same keys and rules, checked by the same validator
`smeltery run` uses (`cli.validate_config`): an unknown section, an unknown key in a section, or a bad value is
refused with the reason, naming the file. Relative file paths inside the file resolve against the file, so a
target can be used from anywhere.

`load(path)` returns the sections (absolute paths, `schema_version` dropped), ready to merge into a campaign config.
`smeltery target check FILE` runs the same check from the command line.
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from pathlib import Path

SCHEMA_VERSION = 1
SECTIONS = ("pocket", "structure", "docking", "gates")
#: The ligands and cut the check borrows to validate the sections the way `smeltery run` would see them.
_PROBE = {"campaign": "target-check", "parent": "a", "candidates": {"a": "CCO", "b": "CCCO"}, "cut": {"keep": 1}}


class TargetFileError(ValueError):
    """The file is not a valid target definition; the message says why, naming the file."""


def parse(cfg: dict, where: str = "the target definition") -> dict:
    """Check a parsed target definition and return its sections (without `schema_version`)."""
    from .cli import ConfigError, validate_config

    if not isinstance(cfg, dict):
        raise TargetFileError(f"{where} must be a TOML table")
    version = cfg.get("schema_version")
    if "schema_version" not in cfg:
        raise TargetFileError(f"{where} has no schema_version; write `schema_version = {SCHEMA_VERSION}` at the top")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise TargetFileError(
            f"{where} has schema_version = {version!r}; this smeltery reads schema_version = {SCHEMA_VERSION} only"
        )
    sections = {k: v for k, v in cfg.items() if k != "schema_version"}
    extra = sorted(set(sections) - set(SECTIONS))
    if extra:
        raise TargetFileError(
            f"{where} holds {extra}, which do not describe the target; a target definition may hold only "
            f"{list(SECTIONS)} (the ligands and the measurement are chosen elsewhere)"
        )
    if not ({"pocket", "structure"} & set(sections)):
        raise TargetFileError(
            f"{where} must describe the pocket: give [pocket] (a file, or [[pocket.site]] charges) or [structure]"
        )
    for name, body in sections.items():
        if not isinstance(body, dict):
            raise TargetFileError(f"{where}: [{name}] must be a table")
    try:
        validate_config(json.loads(json.dumps({**_PROBE, **sections})))
    except ConfigError as e:
        raise TargetFileError(f"{where}: {e}") from e
    return sections


def load(path: str | Path) -> dict:
    """Read, check and return a target definition's sections, file paths made absolute against the file."""
    p = Path(path).expanduser()
    try:
        cfg = tomllib.loads(p.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise TargetFileError(f"cannot read the target definition {str(path)!r}: {e}") from e
    where = f"the target definition {str(path)!r}"
    cfg = _absolute_paths(cfg, p.resolve().parent)
    for table, key in (("pocket", "file"), ("docking", "receptor")):
        value = cfg.get(table, {}).get(key) if isinstance(cfg.get(table), dict) else None
        if isinstance(value, str) and not Path(value).is_file():
            raise TargetFileError(
                f"{where}: [{table}] {key} = {value!r} does not exist (a relative path resolves against the file)"
            )
    return parse(cfg, where)


def _absolute_paths(cfg: dict, base: Path) -> dict:
    from .run_inputs import input_paths

    out = json.loads(json.dumps(cfg, default=str))
    for table, key in input_paths():
        section = out.get(table)
        value = section.get(key) if isinstance(section, dict) else None
        if isinstance(value, str) and not Path(value).is_absolute() and (base / value).is_file():
            section[key] = str((base / value).resolve())
    return out


def main(argv: list[str] | None = None) -> int:
    """`smeltery target check FILE`: 0 valid (prints its sections), 2 refused (prints why)."""
    ap = argparse.ArgumentParser(prog="smeltery target", description="Target definition files (docs/targets.md).")
    sub = ap.add_subparsers(dest="what", required=True)
    check = sub.add_parser("check", help="validate a target .toml as screen_candidates reads it")
    check.add_argument("file", type=Path)
    args = ap.parse_args(argv)
    try:
        sections = load(args.file)
    except TargetFileError as e:
        print(f"smeltery target: {e}", file=sys.stderr)
        return 2
    print(f"{args.file}: valid target definition (schema_version {SCHEMA_VERSION}): {', '.join(sections)}")
    return 0
