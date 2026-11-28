"""Generator rejections as a file a run can read, and as the block the run record holds.

`write_generation` saves what a generator did (`GenerationResult`) as `generation.json`; a campaign config names it
with `[generation] file = "generation.json"`, exactly as `[decisions]` names a decisions file. The run then records
`results.generation_in` (the file and its sha256), a leading `generate` row in `results.funnel`, and the same block
under `ext.smeltery.generation` of the core record. A config without `[generation]` is unchanged in every byte.

The block holds the number of raw outputs, the number returned, a count per closed rejection reason
(`generate.base.REASONS`; every reason is listed, zero included, so two runs compare), and at most
`REJECTED_SMILES_CAP` rejected molecules (index, reason, detail, SMILES) in raw-output order. The counts are always
complete; `rejected_listed` and `rejected_total` say when the list was cut.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .base import REASONS, GenerationResult

SCHEMA = "smeltery.generation/1"
#: The most rejected molecules a run record lists. The file holds all of them; the record stays small.
REJECTED_SMILES_CAP = 50


class GenerationFileError(ValueError):
    """The generation file is unreadable or does not say what a generation file says."""


def write_generation(path: str | Path, result: GenerationResult, *, generator: str | None = None) -> Path:
    """Write `result`'s counts and every rejection (not the candidates: those go in the config's `[candidates]`)."""
    doc = {
        "schema": SCHEMA,
        "generator": generator,
        "n_returned": len(result.candidates),
        "rejected": [
            {"index": r.index, "reason": r.reason, "detail": r.detail, "smiles": r.smiles} for r in result.rejected
        ],
    }
    path = Path(path)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    return path


def load_generation(path: str | Path) -> dict:
    """Read and validate a generation file; every refusal is a `GenerationFileError`."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise GenerationFileError(f"cannot read {str(path)!r}: {e}") from e
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        raise GenerationFileError(f"{Path(path).name} is not a {SCHEMA} file")
    n = doc.get("n_returned")
    rejected = doc.get("rejected")
    if isinstance(n, bool) or not isinstance(n, int) or n < 0 or not isinstance(rejected, list):
        raise GenerationFileError(f"{Path(path).name}: n_returned must be a count and rejected a list")
    for i, r in enumerate(rejected):
        if not isinstance(r, dict) or r.get("reason") not in REASONS:
            raise GenerationFileError(f"{Path(path).name}: rejected[{i}] reason is not one of {list(REASONS)}")
        if isinstance(r.get("index"), bool) or not isinstance(r.get("index"), int):
            raise GenerationFileError(f"{Path(path).name}: rejected[{i}] has no integer index")
        if r.get("smiles") is not None and not isinstance(r["smiles"], str):
            raise GenerationFileError(f"{Path(path).name}: rejected[{i}] smiles must be a string or null")
    return doc


def generation_block(doc: dict, cap: int = REJECTED_SMILES_CAP) -> dict:
    """What the run record holds about a loaded generation file (see the module docstring)."""
    rejected = sorted(doc["rejected"], key=lambda r: r["index"])
    counts = Counter(r["reason"] for r in rejected)
    return {
        "generator": doc.get("generator"),
        "n_raw": int(doc["n_returned"]) + len(rejected),
        "n_returned": int(doc["n_returned"]),
        "rejected_by_reason": {reason: counts.get(reason, 0) for reason in REASONS},
        "rejected_total": len(rejected),
        "rejected_listed": min(cap, len(rejected)),
        "rejected": [
            {"index": r["index"], "reason": r["reason"], "detail": r.get("detail") or "", "smiles": r.get("smiles")}
            for r in rejected[:cap]
        ],
    }
