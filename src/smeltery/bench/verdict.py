from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from smeltery.gui.results import _RESULT_KEYS, contract_record


@dataclass(frozen=True)
class Verdict:
    state: Literal["RESOLVED", "UNRANKED", "INCOMPLETE"]
    winner: str | None
    floor: float | None
    missing: tuple[str, ...]


def verdict_of(record: dict) -> Verdict:
    if isinstance(record, dict):
        record = contract_record(record)  # an imported record's derived floor and funnel
    results = record.get("results", {}) if isinstance(record, dict) else {}
    if not isinstance(results, dict):
        results = {}
    missing = tuple(k for k in _RESULT_KEYS if k not in results)
    # floor from inputs if available
    floor = None
    try:
        floor = float(record["inputs"]["floor"]["value"])
    except Exception:
        floor = None
    if missing:
        return Verdict(state="INCOMPLETE", winner=None, floor=floor, missing=missing)
    cut = results.get("cut", {})
    unranked = bool(cut.get("unranked", False))
    survivors = cut.get("survivors")
    if unranked:
        winner = None
        state = "UNRANKED"
    else:
        winner = survivors[0] if survivors else None
        state = "RESOLVED" if winner is not None else "UNRANKED"
    return Verdict(state=state, winner=winner, floor=floor, missing=())
