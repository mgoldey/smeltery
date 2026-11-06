"""The 2026-10-16 danuglipron halogen record (`examples/danuglipron_halogen.py`), as a Bench run.

That record predates the contract: its per-analogue paired values are `results.paired_ddE_amber`, it keeps no
pose files, no pocket coordinates and no per-candidate dE_int. This importer maps what is there and leaves the
rest out, so `results.not_recorded` names it.

The record's `inputs` and `tiers` are kept as they are, byte for value: nothing renamed, nothing dropped, nothing
added, so its own `input_digest` is still the hash of them. What the contract reads but the original does not hold in
that shape is derived, and lives only under `results.imported.derived` as `{"value": ..., "from": "..."}`, which
`smeltery.gui.results.contract_record` lays over the record for the viewers:

- `inputs.parent` and `inputs.candidates`: the original's `inputs.parent` is the parent's SMILES, not a name; the
  parent is named "parent" here, beside the original's `inputs.analogues`;
- `inputs.floor`: the original's `results.declared_floor` (value, source);
- `inputs.cut` (keep=3, z=2.0): not a field of the record, but what `examples/danuglipron_halogen.py:583` passed to
  `cut()` (the record's cut note repeats keep=3);
- `results.funnel` and `results.candidate_order`: counts and order from the record's own paired_ddE_amber and cut.

No measured number is derived.
"""

from __future__ import annotations

import copy
from pathlib import Path

from smeltery.record import digest

from . import register

NAME = "paired_amber_2026_10"
#: The example's `cut(paired, keep=3, z=2.0, floor=...)`; the record's own cut note reads "keep=3 falls inside ...".
_KEEP, _Z = 3, 2.0
_PARENT = "parent"


def detect(record: dict) -> bool:
    res = record["results"]
    return (
        isinstance(res.get("paired_ddE_amber"), dict)
        and "paired_ddE" not in res
        and "criterion_3_pairing" in res
        and "declared_floor" in res
        and isinstance(res.get("cut"), dict)
        and "analogues" in record["inputs"]
    )


def convert(record: dict, original: Path) -> dict:
    res, inp = record["results"], record["inputs"]
    if digest({"inputs": inp, "tiers": record["tiers"]}) != record["input_digest"]:
        raise ValueError(f"{original}: its input_digest is not the hash of its own inputs and tiers")
    paired = {n: dict(m) for n, m in res["paired_ddE_amber"].items()}
    floor = res["declared_floor"]
    cut = res["cut"]
    n = len(paired)
    unranked = bool(cut["unranked"])
    funnel = [
        {
            "stage": "field_interaction",
            "kind": "measure",
            "candidates_in": n + 1,
            "candidates_out": n + 1,
            "seconds": None,
        },
        {
            "stage": "cut",
            "kind": "rank",
            "candidates_in": n,
            "candidates_out": None if unranked else len(cut["survivors"] or []),
            "unranked": unranked,
        },
    ]
    derived = {
        "inputs.parent": {
            "value": _PARENT,
            "from": "this importer's name for the parent: the original's inputs.parent is the parent's SMILES",
        },
        "inputs.candidates": {
            "value": {_PARENT: inp["parent"], **inp["analogues"]},
            "from": "the original's inputs.parent (SMILES) under the name 'parent', then its inputs.analogues",
        },
        "inputs.floor": {
            "value": {"value": floor["value"], "source": floor["source"]},
            "from": "the original's results.declared_floor (value and source)",
        },
        "inputs.cut": {
            "value": {"keep": _KEEP, "z": _Z},
            "from": (
                "the cut(keep=3, z=2.0) call (line 583) of the example script that wrote this record, named in this"
                " importer's docstring; the record's cut note says keep=3"
            ),
        },
        "results.funnel": {
            "value": funnel,
            "from": "counts from the original's paired_ddE_amber (12 analogues) plus the parent, and its results.cut",
        },
        "results.candidate_order": {
            "value": [_PARENT, *inp["analogues"]],
            "from": "the parent, then the original's inputs.analogues in their recorded order",
        },
    }
    return {
        "campaign": record["campaign"],
        "ferric": record["ferric"],
        "host": record["host"],
        "input_digest": record["input_digest"],
        "inputs": copy.deepcopy(inp),
        "results": {
            "paired_ddE": paired,
            "unpaired_sem": {k: v["unpaired_sem"] for k, v in res["criterion_3_pairing"].items()},
            "cut": {k: cut[k] for k in ("groups", "survivors", "unranked")},
            "imported": {
                "derived": derived,
                "input_digest": (
                    "the original record's input_digest, the hash of its inputs and tiers, which this record keeps"
                    " unchanged; derived values are not in inputs and are not hashed"
                ),
            },
        },
        "smeltery_version": record["smeltery_version"],
        "started_utc": record["started_utc"],
        "tiers": copy.deepcopy(record["tiers"]),
    }


register(NAME, detect, convert)
