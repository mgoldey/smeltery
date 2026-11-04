"""Response and request shapes. These are the API's contract: `npm run gen:api` turns them into TypeScript."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class Verdict(BaseModel):
    state: Literal["RESOLVED", "UNRANKED", "INCOMPLETE"]
    winner: str | None
    floor: float | None
    missing: list[str]


class RunSummary(BaseModel):
    idx: int
    path: str
    campaign: str | None = None
    parent: str | None = None
    digest: str | None = None
    n_candidates: int | None = None
    verdict: Verdict | None = None
    error: str | None = None


class RunDetail(BaseModel):
    idx: int
    path: str
    out_dir: str
    campaign: str
    digest: str
    parent: str
    candidates: dict[str, str]
    analogues: list[str]
    groups: list[list[str]]
    survivors: list[str] | None
    floor: float
    floor_source: str
    z: float
    keep: int
    unranked: bool
    has_pose_output: bool
    n_poses: dict[str, int]
    unit: str
    verdict: Verdict


class DdeRow(BaseModel):
    candidate: str
    smiles: str
    mean: float
    sem: float
    n: int
    lo: float
    hi: float
    unpaired_sem: float | None
    group: int
    survivor: bool | None


class DdeTable(BaseModel):
    rows: list[DdeRow]
    floor: float
    floor_source: str
    z: float
    keep: int
    unit: str
    verdict: Verdict


class Resolved(BaseModel):
    names: list[str]
    matrix: list[list[bool]]


class PerPoseRow(BaseModel):
    candidate: str
    pose: int
    value: float
    parent_value: float
    paired: float


class PoseAtoms(BaseModel):
    candidate: str
    symbols: list[str]
    source: str = Field(description="where the symbols were read from: the candidate's pose SDF")


class Task(BaseModel):
    id: str
    name: str
    command: list[str]
    cwd: str
    status: str
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    returncode: int | None = None
    log_path: str = ""
    note: str = ""
    run_id: str | None = None


class TaskSubmit(BaseModel):
    name: str = ""
    command: list[str] | str
    cwd: str | None = None


class TaskLog(BaseModel):
    log: str


class Problem(BaseModel):
    reason: str


class Issue(BaseModel):
    level: Literal["error", "warning", "note"]
    stage: str | None
    message: str


class PipelineState(BaseModel):
    toml: str
    ok: bool
    issues: list[Issue]
    stages: list[dict[str, Any]]
    addable: list[str]


class PipelineCheck(BaseModel):
    toml: str


class PipelineEdit(BaseModel):
    toml: str
    op: Literal["add", "remove", "move", "set"]
    key: str
    params: dict[str, Any] | None = None
    at: int | None = None
    to: int | None = None


class SiteRequest(BaseModel):
    toml: str
    atom: int
    from_atom: int
    q: float = 1.0
    distance: float = 3.0


class PipelineFromRun(BaseModel):
    run: int


class LaunchRequest(BaseModel):
    toml: str
    out: str | None = None


class Provisional(BaseModel):
    smiles: str
    symbols: list[str]
    molblock: str
    note: str = "provisional geometry: for choosing atoms, never scored or recorded"


class StructureRequest(BaseModel):
    source: str
    allow_network: bool = False


class StructureText(BaseModel):
    name: str
    text: str


class StructureInfo(BaseModel):
    source: str
    sha256: str
    origin: str
    license: str | None = None
    attribution: str | None = None
    mean_plddt: float | None = None
    dropped: list[str]
    n_atoms: int
    file_url: str


class Vocabulary(BaseModel):
    statuses: list[str]
    reasons: list[str]
    note_required: list[str]


class DecisionIn(BaseModel):
    candidate: str
    status: str
    reason: str
    note: str = ""


class DecisionOut(BaseModel):
    candidate: str
    smiles: str
    status: str
    reason: str
    note: str
    user: str
    time: str


class CommentIn(BaseModel):
    anchor: str = "run"
    body: str


class CommentOut(BaseModel):
    id: str
    anchor: str
    author: str
    body: str
    created_at: str


class ProposalIn(BaseModel):
    smiles: str
    reason: str
    note: str = ""
    candidate: str = ""


class ProposalOut(BaseModel):
    id: str
    run: str
    smiles: str
    reason: str
    note: str
    candidate: str
    author: str
    created_at: str
    status: str


class ProposalVocabulary(BaseModel):
    reasons: list[str]


class Snapshot(BaseModel):
    path: str
    sha256: str
    external: list[str] = Field(description="the only external URLs in the file: pinned, integrity-hashed libraries")


class CampaignIn(BaseModel):
    name: str
    description: str = ""


class CampaignOut(BaseModel):
    id: str
    name: str
    description: str = ""
    created_at: str = ""


class EntryIn(BaseModel):
    title: str
    body: str = ""
    runs: list[int] = []
    supersedes: str | None = None


class EntryOut(BaseModel):
    id: str
    campaign_id: str
    title: str
    body: str
    author: str
    created_at: str
    artefacts: list[str]
    supersedes: str | None
    superseded_by: str | None = None


class StudyCell(BaseModel):
    condition: dict[str, str]
    label: str
    step: str
    state: Literal["cached", "to_run"]
    key: str


class Comparison(BaseModel):
    figure: dict[str, Any]
    caption: str
    summary: dict[str, Any]
