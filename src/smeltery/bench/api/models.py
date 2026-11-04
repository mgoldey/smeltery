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
    stages: list[dict[str, Any]] = Field(description="Pipeline.view(): stages as smeltery run executes them")
    placed: list[str] = Field(description="the stages the user placed, in their order")
    params: dict[str, dict[str, Any]] = Field(description="knobs the user set, per placed stage")
    addable: list[str]
    campaign: str
    parent: str
    candidates: dict[str, str]
    notes: list[str] = []


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


class ParamOut(BaseModel):
    name: str
    kind: str
    default: Any = None
    required: bool = False
    help: str = ""
    choices: list[Any] = []
    optional: bool = False


class MethodOut(BaseModel):
    key: str
    label: str
    rung: str
    role: str
    needs: list[str]
    provides: list[str]
    section: str | None
    gate_key: str | None
    tier: str | None
    required: bool
    implicit: bool
    wired: bool
    why_unwired: str
    extras: list[str]
    exclusive: list[str]
    summary: str
    rung_index: int
    produces: dict[str, str]
    floors: dict[str, float | None]
    is_delta_g: bool | None = Field(description="None for stages that are not measurement tiers")
    floor_status: str
    params: list[ParamOut]
    quantity: str | None = Field(description="the quantity this method measures; rigour compares only within one")
    phase: str
    rigour: str | None


class RungOut(BaseModel):
    name: str
    description: str
    methods: list[str]


class SiteField(BaseModel):
    name: str
    kind: str


class CatalogOut(BaseModel):
    rungs: list[RungOut]
    roles: list[str]
    artefacts: dict[str, str]
    kinds: list[str]
    site_fields: list[SiteField]
    methods: dict[str, MethodOut]


class LineagePose(BaseModel):
    pose: int
    value: float
    parent_value: float
    difference: float


class LineageStep(BaseModel):
    stage: str
    kind: str
    seconds: float | None
    settings: dict[str, Any]


class LineageField(BaseModel):
    n_charges: int
    provenance: dict[str, Any] | None


class LineageConfig(BaseModel):
    file: str
    sha256: str
    inputs: Any


class LineageCalcs(BaseModel):
    file: str
    n: int | None = None


class Lineage(BaseModel):
    """What made one candidate's paired ΔΔE: its poses, the steps that ran, and what the run recorded about itself."""

    candidate: str
    quantity: str
    parent: str
    mean: float
    sem: float
    n: int
    poses: list[LineagePose]
    steps: list[LineageStep]
    field: LineageField
    engine: dict[str, Any]
    smeltery: dict[str, Any] | None
    digest: str
    started_utc: str | None
    config: LineageConfig | None
    calcs: LineageCalcs | None
    missing: list[str] = Field(description="items above this run did not record (an older run, or no --out)")
