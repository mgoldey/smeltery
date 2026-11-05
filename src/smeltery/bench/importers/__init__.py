"""Importers: bring a run record written before the Bench contract into the contract's shape.

An importer is a pair: `detect(record)` says whether a parsed record has the shape it understands, and
`convert(record, original)` returns the contract-shaped record. `import_run` copies the original BYTE FOR BYTE
under `out_dir/original/`, writes the converted record as `out_dir/run-<digest12>.json`, and returns its path.
The converted record names what it came from in `results.imported` and lists the contract keys the original
could not supply in `results.not_recorded`: `results` keys bare, `inputs` keys prefixed "inputs.". The original's
`inputs` and `tiers` are kept unchanged, so its `input_digest` still hashes them. A value an importer derives for a
key the original lacks is never written in place: it goes under `results.imported.derived["<inputs|results>.<key>"]`
as `{"value": ..., "from": "..."}`, and `smeltery.gui.results.contract_record` lays it over the record for viewers.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from smeltery.gui.results import CONTRACT_INPUTS

#: Every `results` key a viewer may read (`smeltery.gui.results`), plus the ones a run writes beside them.
CONTRACT_KEYS = (
    "paired_ddE",
    "unpaired_sem",
    "cut",
    "funnel",
    "candidate_order",
    "stages",
    "per_pose",
    "units",
    "poses",
    "field",
    "receptor",
    "calcs",
    "config",
)


@dataclass(frozen=True)
class Importer:
    name: str
    detect: Callable[[dict], bool]
    convert: Callable[[dict, Path], dict]


_REGISTRY: dict[str, Importer] = {}


def register(name: str, detect: Callable[[dict], bool], convert: Callable[[dict, Path], dict]) -> None:
    """Register an importer. Re-registering a name replaces it (a reload is not an error)."""
    _REGISTRY[name] = Importer(name, detect, convert)


def find(record: dict) -> Importer | None:
    """The first registered importer whose `detect` accepts `record`, or None for a shape nobody knows."""
    _load_builtin()
    for importer in _REGISTRY.values():
        try:
            if importer.detect(record):
                return importer
        except (KeyError, TypeError, AttributeError):
            continue
    return None


def import_run(original: Path, out_dir: Path) -> Path:
    """Import the record at `original` into `out_dir`; return the path of the converted `run-<digest12>.json`."""
    original = Path(original)
    raw = original.read_bytes()
    record = json.loads(raw)
    importer = find(record)
    if importer is None:
        raise ValueError(f"{original}: no importer recognises this record's shape")
    converted = importer.convert(record, original)
    results = converted["results"]
    results["imported"] = {
        **results.get("imported", {}),  # an importer's own notes, e.g. `derived`
        "importer": importer.name,
        "original": f"original/{original.name}",
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
    if converted["inputs"] != record["inputs"] or converted["input_digest"] != record["input_digest"]:
        raise ValueError(f"{importer.name}: the converted record must keep the original's inputs and input_digest")
    results["not_recorded"] = [k for k in CONTRACT_KEYS if k not in results] + [
        f"inputs.{k}" for k in CONTRACT_INPUTS if k not in record["inputs"]
    ]
    out_dir = Path(out_dir)
    (out_dir / "original").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(original, out_dir / "original" / original.name)
    target = out_dir / f"run-{converted['input_digest'][:12]}.json"
    target.write_text(json.dumps(converted, indent=2, sort_keys=True) + "\n")
    return target


def _load_builtin() -> None:
    from . import paired_amber_2026_10  # noqa: F401  (registers itself)
