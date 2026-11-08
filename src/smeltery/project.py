"""Project / campaign / notebook entries: the ELN layer. Core module, no I/O, no toolkit.

A campaign holds runs, decisions, comments and dated entries. An entry is append-only and references
artefacts by sha256. Nothing here imports a store; persistence is a port's job.
"""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class NotebookEntry:
    """An append-only notebook entry. Once created it is never edited; a correction is a new entry
    that supersedes it by referencing the same artefacts."""

    id: str
    campaign_id: str
    title: str
    body: str
    author: str
    created_at: str
    artefacts: tuple[str, ...] = ()  # sha256 digests
    supersedes: str | None = None
    # attached files: {"digest", "filename", "media_type", "kind": "file"}; `artefacts` stay the attached runs
    files: tuple[dict[str, str], ...] = ()

    def problems(self) -> list[str]:
        out = []
        for k in ("id", "campaign_id", "title", "body", "author", "created_at"):
            if not isinstance(getattr(self, k), str):
                out.append(f"{k} must be a string")
        if not self.id.strip():
            out.append("id is required")
        if not self.campaign_id.strip():
            out.append("campaign_id is required")
        if not self.title.strip():
            out.append("title is required")
        if not self.author.strip():
            out.append("author is required")
        try:
            # accept the project's UTC format
            datetime.strptime(self.created_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            out.append(f"created_at {self.created_at!r} is not UTC YYYY-MM-DDTHH:MM:SSZ")
        for a in self.artefacts:
            if not isinstance(a, str) or len(a) != 64:
                out.append(f"artefact ref {a!r} is not a sha256 hex digest")
        for f in self.files:
            d = f.get("digest") if isinstance(f, dict) else None
            if not isinstance(d, str) or len(d) != 64 or not f.get("filename"):
                out.append(f"file attachment {f!r} needs a sha256 digest and a filename")
        return out

    def validate(self) -> "NotebookEntry":
        bad = self.problems()
        if bad:
            raise ValueError("invalid notebook entry: " + "; ".join(bad))
        return self

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self) | {"artefacts": list(self.artefacts), "files": [dict(f) for f in self.files]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotebookEntry":
        d = dict(data)
        if "artefacts" in d and isinstance(d["artefacts"], list):
            d["artefacts"] = tuple(d["artefacts"])
        if isinstance(d.get("files"), list):
            d["files"] = tuple(d["files"])
        return cls(**d).validate()


@dataclass
class Campaign:
    id: str
    name: str
    created_at: str = field(default_factory=_utc_now)
    run_paths: list[str] = field(default_factory=list)
    description: str = ""

    def problems(self) -> list[str]:
        out = []
        for k in ("id", "name", "created_at", "description"):
            if not isinstance(getattr(self, k), str):
                out.append(f"{k} must be a string")
        if not self.id.strip():
            out.append("id is required")
        if not self.name.strip():
            out.append("name is required")
        if not isinstance(self.run_paths, list):
            out.append("run_paths must be a list")
        return out

    def validate(self) -> "Campaign":
        bad = self.problems()
        if bad:
            raise ValueError("invalid campaign: " + "; ".join(bad))
        return self


@dataclass(frozen=True)
class Proposal:
    """A bench chemist's request for a candidate that does not exist yet. Never launches compute."""

    id: str
    run: str
    smiles: str
    reason: str
    note: str = ""
    candidate: str = ""  # proposed name
    user: str = ""
    time: str = ""
    author: str = ""
    created_at: str = ""
    status: str = "pending"  # pending | reviewed | accepted | rejected

    from typing import ClassVar

    REASONS: ClassVar[tuple[str, ...]] = ("substitution", "analog", "followup", "other", "synthesis")
    STATUSES: ClassVar[tuple[str, ...]] = ("pending", "reviewed", "accepted", "rejected")

    def __post_init__(self):
        # sync aliases
        if self.author and not self.user:
            object.__setattr__(self, "user", self.author)
        if self.created_at and not self.time:
            object.__setattr__(self, "time", self.created_at)
        if self.user and not self.author:
            object.__setattr__(self, "author", self.user)
        if self.time and not self.created_at:
            object.__setattr__(self, "created_at", self.time)

    def problems(self) -> list[str]:
        from rdkit import Chem

        out = []
        for k in (
            "id",
            "run",
            "candidate",
            "smiles",
            "reason",
            "note",
            "user",
            "time",
            "status",
            "author",
            "created_at",
        ):  # noqa: E501
            if not isinstance(getattr(self, k), str):
                out.append(f"{k} must be a string")
        if out:
            return out
        if not self.id.strip():
            out.append("id is required")
        # candidate required only if provided? plan test doesn't pass candidate, so make optional
        if self.smiles.strip() and Chem.MolFromSmiles(self.smiles) is None:
            out.append(f"unparseable SMILES {self.smiles!r}")
        # require non-empty smiles
        if not self.smiles.strip():
            out.append(f"unparseable SMILES {self.smiles!r}")
        if self.reason not in self.REASONS:
            out.append(f"reason {self.reason!r} is not one of {list(self.REASONS)}")
        if self.reason == "other" and not self.note.strip():
            out.append("reason 'other' needs a note")
        if self.status not in self.STATUSES:
            out.append(f"status {self.status!r} is not one of {list(self.STATUSES)}")
        if self.time:
            try:
                datetime.strptime(self.time, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                out.append(f"time {self.time!r} is not UTC YYYY-MM-DDTHH:MM:SSZ")
        if self.created_at:
            try:
                datetime.strptime(self.created_at, "%Y-%m-%dT%H:%M:%SZ")
            except ValueError:
                out.append(f"created_at {self.created_at!r} is not UTC YYYY-MM-DDTHH:MM:SSZ")
        return out

    def validate(self) -> "Proposal":
        bad = self.problems()
        if bad:
            raise ValueError("invalid proposal: " + "; ".join(bad))
        return self

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Proposal":
        return cls(**data).validate()


def make_entry_id(campaign_id: str, title: str, when: str) -> str:
    return hashlib.sha256(f"{campaign_id}|{title}|{when}".encode()).hexdigest()[:12]


def utc_now() -> str:
    return _utc_now()
