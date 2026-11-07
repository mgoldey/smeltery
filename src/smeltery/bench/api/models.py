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
    per_pose: list[float] = Field(description="the per-pose values behind the mean, in pose order")
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
    #: the ORIGINAL pose index, the pairing key (`results.pose_index` after a pairwise drop, else the position)
    pose: int
    #: the pose's place in this candidate's per-pose values and SDF (what `/scene` and `/calcs` take)
    position: int
    #: the paired parent pose's place in the parent's per-pose values and SDF
    parent_position: int
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


class PipelineDoc(BaseModel):
    """A workspace pipeline: its config text as saved, the hash a save must quote, and what `check()` finds."""

    name: str
    toml: str
    sha256: str
    folder: str
    ok: bool
    issues: list[Issue]
    notes: list[str] = []


class PipelinePut(BaseModel):
    toml: str
    base_sha256: str | None = None


class BlankPipeline(BaseModel):
    campaign: str
    parent: str
    candidates: dict[str, str]


class PipelineCreate(BaseModel):
    name: str
    from_run: int | None = None
    from_prebuilt: str | None = None
    blank: BlankPipeline | None = None


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
    # A relative path is read from this pipeline's folder, as `smeltery run` reads the config's `[structure] spec`.
    pipeline: str | None = None


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


class CompareRow(BaseModel):
    """One comparison key. An arm that produced nothing for it has a null median and n == 0 (shown as "not run")."""

    key: str
    n_a: int
    n_b: int
    a: float | None
    b: float | None
    #: a - b of the arms' medians; null when unpaired
    diff: float | None
    #: SEM of the difference; null when either arm has n < 2
    sem: float | None
    #: beyond both the noise and the floor; null when unpaired or no floor is known
    resolved: bool | None
    unpaired: bool


class CandidateRow(BaseModel):
    """One analogue in either run; a null arm did not run it."""

    candidate: str
    smiles: str
    ddE_a: float | None
    sem_a: float | None
    ddE_b: float | None
    sem_b: float | None
    diff: float | None
    diff_sem: float | None
    resolved: bool | None


class Comparison(BaseModel):
    figure: dict[str, Any]
    caption: str
    summary: dict[str, Any]
    rows: list[CompareRow]
    unit: str
    #: null when the runs' parents differ (`candidates_note` says why)
    candidates: list[CandidateRow] | None
    candidates_note: str | None


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
    settings: dict[str, Any] | None = Field(description="None: the stage should have a tier but the record has none")


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


class MethodParam(BaseModel):
    """One setting of a method: what the code defaults it to, what it does, and the workspace's own default."""

    name: str
    kind: str
    default: Any = None
    required: bool
    optional: bool
    choices: list[Any]
    help: str = Field(description="one plain sentence, from the tier's own docs, saying what changing it does")
    workspace_default: Any = Field(description="copied into a step when the method is added; null when unset")


class MethodInfo(BaseModel):
    key: str
    label: str
    summary: str
    quantity: str | None
    params: list[MethodParam]
    cost_model: str = Field(description="how wall time is estimated, or 'cannot say: ...'")
    memory_model: str = Field(description="how memory is estimated, or 'cannot say: ...'")
    floor: float | None = Field(description="the tier's measured systematic floor; null while it is unmeasured")
    used_by: list[str] = Field(description="workspace pipelines that place this method")


class MethodDefaultsPut(BaseModel):
    """The workspace defaults of one method, replacing what was there; `{}` clears them."""

    defaults: dict[str, Any]
