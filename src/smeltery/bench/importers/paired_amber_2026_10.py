"""The 2026-10-16 danuglipron halogen record (`examples/danuglipron_halogen.py`), as a Bench run.

That record predates the contract: its per-analogue paired values are `results.paired_ddE_amber`, it keeps no
pose files, no pocket coordinates and no per-candidate dE_int. This importer maps what is there and leaves the
rest out, so `results.not_recorded` names it.

Two things are derived, not read, and `results.imported.derived` says from where: the funnel's candidate counts (the
12 analogues plus the parent, from the record's own paired_ddE_amber and cut), and `inputs.cut` (keep=3, z=2.0), which
the record does not store as a field but `examples/danuglipron_halogen.py:583` passed to `cut()` (and the record's cut
note repeats keep=3). No measured number is derived.
"""

from __future__ import annotations

from pathlib import Path

from . import register

NAME = "paired_amber_2026_10"
#: The example's `cut(paired, keep=3, z=2.0, floor=...)`; the record's own cut note reads "keep=3 falls inside ...".
_KEEP, _Z = 3, 2.0
_PARENT = "parent"
DERIVED = {
    "inputs.cut": (
        "the cut(keep=3, z=2.0) call (line 583) of the example script that wrote this record, named in this"
        " importer's docstring; the record's cut note says keep=3"
    ),
    "results.funnel": "counts from paired_ddE_amber (12 analogues) plus the parent, and results.cut",
}


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
    paired = {n: dict(m) for n, m in res["paired_ddE_amber"].items()}
    order = [_PARENT, *inp["analogues"]]
    floor = res["declared_floor"]
    cut = res["cut"]
    n = len(paired)
    unranked = bool(cut["unranked"])
    out_inputs = {
        "candidates": {_PARENT: inp["parent"], **inp["analogues"]},
        "parent": _PARENT,
        "floor": {"value": floor["value"], "source": floor["source"]},
        "cut": {"keep": _KEEP, "z": _Z},
        "pocket": inp["pocket"],
        "sites": inp["sites"],
        "ionization": inp["ionization"],
        "dropped_pose_indices": inp["dropped_pose_indices"],
    }
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
    converted = {
        "campaign": record["campaign"],
        "ferric": record["ferric"],
        "host": record["host"],
        "input_digest": record["input_digest"],
        "inputs": out_inputs,
        "results": {
            "paired_ddE": paired,
            "unpaired_sem": {k: v["unpaired_sem"] for k, v in res["criterion_3_pairing"].items()},
            "cut": {k: cut[k] for k in ("groups", "survivors", "unranked")},
            "funnel": funnel,
            "candidate_order": order,
        },
        "smeltery_version": record["smeltery_version"],
        "started_utc": record["started_utc"],
        "tiers": record["tiers"],
    }
    converted["results"]["imported"] = {"derived": DERIVED}
    return converted


register(NAME, detect, convert)
