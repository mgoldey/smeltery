"""Keep / reject / hold decisions a person made on a run, with their reasons: a JSON file the next run can read.

Core module (no GUI library), so `smeltery run` can read a decisions file named by `[decisions] file`
(docs/gui-contract.md section 3). Decisions are judgement, kept apart from the run record, which is for reproducing
the run. The file:

    {"schema": 1, "campaign": "...", "run": "<input_digest of the run the decisions were made on>",
     "decisions": [{"candidate": "4-F", "smiles": "OC(=O)c1ccc(F)cc1", "status": "keep",
                    "reason": "cut_survivor", "note": "", "user": "mg", "time": "2026-10-28T20:00:00Z"}]}

Rules, all enforced by `DecisionSet.validate()` (a ValueError naming every violation, never a silent repair):
`schema` is 1; `campaign` and `run` are strings; unknown keys, at the top or in an entry, are refused; `status` is
one of `STATUSES`; `reason` one of `REASONS`; `note` is non-blank when the reason is `within_tie_choice` or `other`;
`candidate` and `smiles` are non-empty and the SMILES parses (RDKit); `time` is UTC `YYYY-MM-DDTHH:MM:SSZ`; each
candidate appears at most once. `load` and `dump` validate, so a file on disk always passes.
"""

from __future__ import annotations

import dataclasses
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

SCHEMA = 1
STATUSES: tuple[str, ...] = ("keep", "reject", "hold")
REASONS: tuple[str, ...] = (
    "cut_survivor",
    "within_tie_choice",
    "liability",
    "synthesis",
    "pose_implausible",
    "novelty",
    "other",
)
#: Reasons that say nothing on their own: the note is where the choice is explained.
NOTE_REQUIRED: frozenset[str] = frozenset({"within_tie_choice", "other"})
TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TOP_KEYS = ("schema", "campaign", "run", "decisions")


def utc_now() -> str:
    """The current UTC time as `YYYY-MM-DDTHH:MM:SSZ`, the stamp `DecisionSet.set` writes."""
    return time.strftime(TIME_FORMAT, time.gmtime())


@dataclass(frozen=True)
class Decision:
    """One candidate's decision. `smiles` is the candidate's SMILES as the run had it, so a later config that reuses
    the name for another molecule is caught (`smeltery run` refuses the mismatch)."""

    candidate: str
    smiles: str
    status: str
    reason: str
    note: str = ""
    user: str = ""
    time: str = ""

    def problems(self) -> list[str]:
        """Every rule this entry breaks, as messages naming the candidate; empty when it is valid."""
        from rdkit import Chem

        who = f"decision for {self.candidate!r}"
        out = []
        for k in ("candidate", "smiles", "status", "reason", "note", "user", "time"):
            if not isinstance(getattr(self, k), str):
                out.append(f"{who}: {k} must be a string, got {type(getattr(self, k)).__name__}")
        if out:
            return out
        if not self.candidate.strip():
            out.append("a decision has an empty candidate name")
        if not self.smiles.strip():
            out.append(f"{who}: smiles is required (the candidate's SMILES in the run it was decided on)")
        elif Chem.MolFromSmiles(self.smiles) is None:
            out.append(f"{who}: unparseable SMILES {self.smiles!r}")
        if self.status not in STATUSES:
            out.append(f"{who}: status {self.status!r} is not one of {list(STATUSES)}")
        if self.reason not in REASONS:
            out.append(f"{who}: reason {self.reason!r} is not one of {list(REASONS)}")
        elif self.reason in NOTE_REQUIRED and not self.note.strip():
            out.append(f"{who}: reason {self.reason!r} needs a note saying why")
        try:
            time.strptime(self.time, TIME_FORMAT)
        except ValueError:
            out.append(f"{who}: time {self.time!r} is not UTC YYYY-MM-DDTHH:MM:SSZ")
        return out


@dataclass
class DecisionSet:
    """The decisions made on one run (`run` is its input_digest), in the order they were first made.

    `candidates` (name -> SMILES, not written to the file) lets `set` fill in a new entry's SMILES from the run;
    without it, `set` needs `smiles=` for a candidate it has no entry for.
    """

    campaign: str
    run: str
    decisions: list[Decision] = field(default_factory=list)
    candidates: dict[str, str] = field(default_factory=dict, repr=False, compare=False)
    schema: int = SCHEMA

    def get(self, candidate: str) -> Decision | None:
        return next((d for d in self.decisions if d.candidate == candidate), None)

    def by_status(self, status: str) -> list[str]:
        """Candidate names with this status, in file order. Refuses a status that does not exist."""
        if status not in STATUSES:
            raise ValueError(f"status {status!r} is not one of {list(STATUSES)}")
        return [d.candidate for d in self.decisions if d.status == status]

    def set(
        self, candidate: str, status: str, reason: str, note: str = "", user: str = "", *, smiles: str | None = None
    ) -> Decision:
        """Record a decision, stamped with the current UTC time, replacing any earlier one for `candidate` (in its
        place). The SMILES is `smiles`, else the existing entry's, else `candidates[candidate]`; none of them is a
        ValueError. The new entry is validated before anything changes (ValueError naming what is wrong)."""
        old = self.get(candidate)
        if smiles is None:
            smiles = old.smiles if old is not None else self.candidates.get(candidate)
        if smiles is None:
            raise ValueError(
                f"no SMILES for {candidate!r}: pass smiles=, or build the DecisionSet with candidates={{name: smiles}}"
            )
        d = Decision(candidate, smiles, status, reason, note, user, utc_now())
        bad = d.problems()
        if bad:
            raise ValueError("invalid decision: " + "; ".join(bad))
        if old is None:
            self.decisions.append(d)
        else:
            self.decisions[self.decisions.index(old)] = d
        return d

    def remove(self, candidate: str) -> None:
        """Forget the decision on `candidate`; KeyError if there is none."""
        old = self.get(candidate)
        if old is None:
            raise KeyError(f"no decision for {candidate!r}")
        self.decisions.remove(old)

    def validate(self) -> DecisionSet:
        """Raise ValueError listing every rule broken (module docstring); return self when valid."""
        bad = []
        if self.schema != SCHEMA or isinstance(self.schema, bool):
            bad.append(f"schema {self.schema!r} is not supported (expected {SCHEMA})")
        for k in ("campaign", "run"):
            if not isinstance(getattr(self, k), str):
                bad.append(f"{k} must be a string")
        seen: set[str] = set()
        for d in self.decisions:
            if not isinstance(d, Decision):
                bad.append(f"decisions must hold Decision entries, got {type(d).__name__}")
                continue
            bad += d.problems()
            if d.candidate in seen:
                bad.append(f"candidate {d.candidate!r} has more than one decision")
            seen.add(d.candidate)
        if bad:
            raise ValueError("invalid decisions: " + "; ".join(bad))
        return self

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "campaign": self.campaign,
            "run": self.run,
            "decisions": [dataclasses.asdict(d) for d in self.decisions],
        }

    @classmethod
    def from_dict(cls, data: dict) -> DecisionSet:
        """Build and validate. Missing or unknown keys are a ValueError, as is any rule `validate` enforces."""
        if not isinstance(data, dict):
            raise ValueError("a decisions file is a JSON object")
        missing = [k for k in _TOP_KEYS if k not in data]
        extra = sorted(set(data) - set(_TOP_KEYS))
        if missing or extra:
            raise ValueError(f"decisions file: missing keys {missing}, unknown keys {extra}; keys: {list(_TOP_KEYS)}")
        if not isinstance(data["decisions"], list):
            raise ValueError("decisions file: 'decisions' must be a list")
        names = [f.name for f in dataclasses.fields(Decision)]
        entries = []
        for i, e in enumerate(data["decisions"]):
            if not isinstance(e, dict):
                raise ValueError(f"decisions file: entry #{i} must be an object")
            need = [k for k in ("candidate", "smiles", "status", "reason", "time") if k not in e]
            unknown = sorted(set(e) - set(names))
            if need or unknown:
                raise ValueError(f"decisions file: entry #{i} is missing {need}, has unknown keys {unknown}")
            entries.append(Decision(**e))
        return cls(data["campaign"], data["run"], entries, schema=data["schema"]).validate()

    @classmethod
    def load(cls, path: str | Path) -> DecisionSet:
        """Read and validate a decisions file. Unreadable file or invalid JSON: ValueError naming the path."""
        try:
            data = json.loads(Path(path).read_text())
        except (OSError, json.JSONDecodeError) as e:
            raise ValueError(f"cannot read decisions file {str(path)!r}: {e}") from e
        try:
            return cls.from_dict(data)
        except ValueError as e:
            raise ValueError(f"{path}: {e}") from e

    def dump(self, path: str | Path) -> Path:
        """Validate, then write `path` (indented JSON, entries in order). Nothing is written when invalid."""
        self.validate()
        path = Path(path)
        path.write_text(json.dumps(self.to_dict(), indent=2) + "\n")
        return path
