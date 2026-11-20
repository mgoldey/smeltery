"""Runs: the record, its verdict and tables, the figures Python already draws, poses, and the judgement layered on
top (decisions, comments, proposals). Every number comes from `smeltery.run_view`; nothing here recomputes it."""

from __future__ import annotations

import io
import json
import math
import zipfile
from pathlib import Path
from typing import Any, Literal, get_args

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from molviewspec.nodes import RepresentationTypeT, validate_state_tree
from pydantic import BaseModel, Field
from rdkit import Chem

from smeltery import run_view as R
from smeltery.bench.pymol_export import ligand_filename, ligand_stems, pymol_session
from smeltery.bench.use_cases import read_run
from smeltery.bench.use_cases import record_decision as decisions
from smeltery.bench.use_cases.comment_on_run import CommentOnRun, CommentRefused
from smeltery.bench.use_cases.compare_arms import CompareArms
from smeltery.bench.use_cases.launch_run import LaunchRun, ResumeRefused
from smeltery.bench.use_cases.propose_candidate import ProposalRefused, ProposeCandidate
from smeltery.bench.use_cases.publish_report import REPORT_EXTERNAL as REPORT_EXTERNAL  # the report's own list
from smeltery.bench.use_cases.publish_report import PublishReport, report_name
from smeltery.bench.verdict import verdict_of
from smeltery.decisions import NOTE_REQUIRED, REASONS, STATUSES, to_csv, to_sdf
from smeltery.gui.limits import floor_statement
from smeltery.gui.scenes import pose_scene
from smeltery.gui.views._style import themed
from smeltery.gui.views.compare import compare_figure, comparison_caption
from smeltery.gui.views.forest import forest_figure
from smeltery.gui.views.matrix import matrix_figure
from smeltery.gui.views.per_pose import per_pose_figure
from smeltery.gui.views.per_pose_values import per_pose_values_figure
from smeltery.project import Proposal
from smeltery.sdf_export import enriched_sdf

from . import models as M
from .common import confined_out, ports, record, row, rows, run, submitted_task, utc_iso

router = APIRouter(prefix="/api", tags=["runs"])

QUANTITY = R.QUANTITY


def _verdict(raw: dict) -> M.Verdict:
    v = verdict_of(raw)
    return M.Verdict(state=v.state, winner=v.winner, floor=v.floor, missing=list(v.missing))


def _plain(fig: Any) -> dict[str, Any]:
    """A Plotly figure as plain JSON (`to_json` handles the numpy arrays `to_plotly_json` would leave in)."""
    return json.loads(fig.to_json())


@router.get("/runs", response_model=list[M.RunSummary])
def list_runs(request: Request) -> list[M.RunSummary]:
    out = []
    store = ports(request).run_store
    for r in rows(request):
        raw = store.read_record(r["path"])
        verdict = _verdict(raw) if raw is not None else None
        digest = raw.get("input_digest") if raw is not None else None
        cands = r.get("candidates")
        out.append(
            M.RunSummary(
                id=r["id"],
                path=r["path"],
                campaign=r.get("campaign"),
                parent=r.get("parent"),
                digest=digest,
                n_candidates=len(cands) if isinstance(cands, list) else None,
                verdict=verdict,
                error=None if r.get("incomplete") else r.get("error"),
            )
        )
    return out


@router.get("/runs/{run_id}", response_model=M.RunDetail)
def run_detail(request: Request, run_id: str) -> M.RunDetail:
    raw = record(request, run_id)
    r = run(request, run_id)
    found = row(request, run_id)
    poses = r.record["results"].get("poses", {}) if r.has_pose_output else {}
    return M.RunDetail(
        id=found["id"],
        path=found["path"],
        out_dir=str(r.out_dir),
        campaign=r.campaign,
        digest=r.record["input_digest"],
        parent=r.parent,
        candidates=dict(r.candidates),
        analogues=list(r.analogues),
        groups=[list(g) for g in r.groups],
        survivors=list(r.survivors) if r.survivors is not None else None,
        floor=r.floor,
        floor_source=r.floor_source,
        floor_statement=floor_statement(r.floor, r.unit, r.floor_source, r.z),
        z=r.z,
        keep=r.keep,
        unranked=r.unranked,
        has_pose_output=r.has_pose_output,
        n_poses={k: int(v["n_poses"]) for k, v in poses.items()},
        unit=r.unit,
        verdict=_verdict(raw),
        decisions_in=_decisions_in(raw),
    )


def _decisions_in(raw: dict) -> M.DecisionsIn | None:
    """`results.decisions_in`, or None when this round carried no decisions file.

    Written only when the config had a `[decisions]` section, so absence is the ordinary case and is reported as
    "nothing carried in" rather than as a missing field. A record that has the key but not its shape (an imported
    one) is also None: inventing a file name or an empty `excluded` would claim a provenance the record does not
    hold.
    """
    got = raw.get("results", {}).get("decisions_in")
    if not isinstance(got, dict):
        return None
    file, sha = got.get("file"), got.get("sha256")
    excluded = got.get("excluded", [])
    if not isinstance(file, str) or not isinstance(sha, str) or not isinstance(excluded, list):
        return None
    return M.DecisionsIn(file=file, sha256=sha, excluded=[str(x) for x in excluded])


@router.get("/runs/{run_id}/verdict", response_model=M.Verdict)
def run_verdict(request: Request, run_id: str) -> M.Verdict:
    return _verdict(record(request, run_id))


@router.get("/runs/{run_id}/ddE", response_model=M.DdeTable)
def run_dde(request: Request, run_id: str) -> M.DdeTable:
    raw = record(request, run_id)
    r = run(request, run_id)
    return M.DdeTable(
        rows=[M.DdeRow(**x) for x in R.ddE_table(r)],
        floor=r.floor,
        floor_source=r.floor_source,
        z=r.z,
        keep=r.keep,
        unit=r.unit,
        verdict=_verdict(raw),
    )


@router.get("/runs/{run_id}/resolved", response_model=M.Resolved)
def run_resolved(request: Request, run_id: str) -> M.Resolved:
    names, matrix = R.resolved_matrix(run(request, run_id))
    return M.Resolved(names=names, matrix=matrix)


@router.get("/runs/{run_id}/per_pose", response_model=list[M.PerPoseRow])
def run_per_pose(request: Request, run_id: str, quantity: str = QUANTITY) -> list[M.PerPoseRow]:
    try:
        return [M.PerPoseRow(**x) for x in R.per_pose_table(run(request, run_id), quantity)]
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e


FIGURES = ("forest", "matrix", "per_pose", "per_pose_values")
#: The colour scheme a figure is drawn for. Python owns the palette for both (smeltery.gui.views._style).
Theme = Literal["light", "dark"]


@router.get("/runs/{run_id}/figures/{name}")
def run_figure(request: Request, run_id: str, name: str, theme: Theme = "light") -> dict[str, Any]:
    """The figure `smeltery.gui.views` builds, as Plotly JSON, coloured for `theme`. The browser draws it; it never
    re-derives or recolours it."""
    return themed(_figure(request, run_id, name), theme)


def _figure(request: Request, run_id: str, name: str) -> dict[str, Any]:
    if name not in FIGURES:
        raise HTTPException(status_code=404, detail=f"no figure {name!r}; figures are {', '.join(FIGURES)}")
    r = run(request, run_id)
    table = R.ddE_table(r)
    if name == "forest":
        return _plain(forest_figure(table, r, unit=r.unit))
    if name == "matrix":
        names, matrix = R.resolved_matrix(r)
        return _plain(matrix_figure(names, matrix, table))
    try:
        pp = R.per_pose_table(r, QUANTITY)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    if name == "per_pose_values":
        return _plain(per_pose_values_figure(pp, r, QUANTITY))
    return _plain(per_pose_figure(pp, r, quantity=QUANTITY, unit=r.unit, rows=table))


@router.get("/runs/{run_id}/scene")
def run_scene(request: Request, run_id: str, candidate: str, pose: int = 0, core: bool = False) -> dict[str, Any]:
    r = run(request, run_id)
    if not r.has_pose_output:
        raise HTTPException(status_code=409, detail="this run record predates pose output; it has no poses to show")
    n = int(r.record["results"]["poses"].get(candidate, {}).get("n_poses", 0))
    if candidate not in r.candidates:
        raise HTTPException(status_code=400, detail=f"no candidate {candidate!r}")
    if not 0 <= pose < n:
        raise HTTPException(status_code=400, detail=f"pose {pose} out of range: {candidate} has {n} poses")
    scene = pose_scene(r, candidate, pose)
    atoms = _core_atoms(r, candidate) if core else None
    return _core_solid(scene, atoms) if atoms else scene


def _core_atoms(r: R.Run, candidate: str) -> list[int]:
    """The candidate's atoms copied exactly from the parent's pose, from `results.pose_maps` (pairs of analogue atom,
    parent atom). Empty for the parent and for a record written before pose maps."""
    maps = r.record["results"].get("pose_maps") or {}
    return sorted({int(pair[0]) for pair in maps.get(candidate, [])})


def _core_solid(scene: dict[str, Any], core: list[int]) -> dict[str, Any]:
    """The same MolViewSpec state with the candidate drawn as two components: its core atoms in the representation
    the scene already gave it (solid), every other atom small and translucent (outlined). Nothing but the candidate's
    structure node changes; the colours, parent, receptor and field are as `pose_scene` built them."""
    out = json.loads(json.dumps(scene))

    def structure(node: dict[str, Any]) -> dict[str, Any] | None:
        if node.get("kind") == "structure" and node.get("ref") == "candidate":
            return node
        for ch in node.get("children", []):
            found = structure(ch)
            if found:
                return found
        return None

    node = structure(out["root"])
    if node is None:
        return out
    (solid,) = [c for c in node["children"] if c.get("kind") == "component"]
    solid["params"]["selector"] = [{"atom_index": i} for i in core]
    others = json.loads(json.dumps(solid))
    others["params"]["selector"] = "all"
    for rep in others["children"]:
        if rep.get("kind") == "representation":
            rep["params"] = {"type": "ball_and_stick", "size_factor": 0.35}
            rep["children"] = [*rep.get("children", []), {"kind": "opacity", "params": {"opacity": 0.4}}]
    others["children"] = [c for c in others["children"] if c.get("kind") != "focus"]
    node["children"].append(others)
    validate_state_tree(json.dumps(out))
    _check_representations(out["root"])  # an unknown type fails here, not as a blank viewer
    return out


def _check_representations(node: dict[str, Any]) -> None:
    """molviewspec's State model leaves representation params loose, so check the type against its own Literal."""
    if node.get("kind") == "representation" and node["params"]["type"] not in get_args(RepresentationTypeT):
        raise ValueError(
            f"representation type {node['params']['type']!r} is not one of {get_args(RepresentationTypeT)}"
        )
    for ch in node.get("children", []):
        _check_representations(ch)


class PoseMaps(BaseModel):
    """`results.pose_maps`: per analogue, (analogue atom, parent atom) pairs copied exactly from the parent's pose."""

    maps: dict[str, list[list[int]]]


@router.get("/runs/{run_id}/pose_maps", response_model=PoseMaps)
def run_pose_maps(request: Request, run_id: str) -> PoseMaps:
    """Empty for a record written before pose maps were kept; the Poses view then cannot tell core from the rest."""
    return PoseMaps(maps=record(request, run_id).get("results", {}).get("pose_maps") or {})


class SensitivityRow(BaseModel):
    """One analogue's paired ddE under the primary charge model and under the other one (None: no pose on both)."""

    primary: float | None
    model: float | None
    delta: float | None
    n: int


class ModelSensitivity(BaseModel):
    primary_model: str | None = None
    paired_ddE: dict[str, SensitivityRow] = {}
    #: None when fewer than two analogues could be compared (`note` says why) or the model failed (`error`).
    sign_flips: int | None = None
    spearman: float | None = None
    floor: float | None = None
    #: analogues compared
    n: int | None = None
    n_dropped: int = 0
    dropped_poses: dict[str, list[int]] = {}
    parent_dropped_poses: list[int] = []
    excluded: list[str] = []
    #: {candidate: {pose: error}}: SCFs under this model that did not give a value
    failures: dict[str, dict[str, str]] = {}
    #: provenance of this model's pocket field (pdb2pqr version, force field, file digest, cutoff)
    field: dict[str, Any] | None = None
    note: str | None = None
    #: the model could not be measured at all; `stage` is where it failed (field, measure, summary)
    error: str | None = None
    stage: str | None = None


class ChargeSensitivity(BaseModel):
    """`results.charge_sensitivity`, one entry per extra charge model; `models` None: the analysis did not run."""

    models: dict[str, ModelSensitivity] | None


@router.get("/runs/{run_id}/charge_sensitivity", response_model=ChargeSensitivity)
def run_charge_sensitivity(request: Request, run_id: str) -> ChargeSensitivity:
    got = (record(request, run_id).get("results") or {}).get("charge_sensitivity")
    if not isinstance(got, dict):
        return ChargeSensitivity(models=None)
    return ChargeSensitivity(models={k: ModelSensitivity.model_validate(v) for k, v in got.items()})


class GateRow(BaseModel):
    candidate: str
    pose: int
    #: PoseBusters' verdict for the pose; None when that gate did not run for this candidate.
    posebusters: bool | None
    failed_checks: list[str]
    strain_kcal: float | None
    #: Strain above `max_strain_kcal`; None when no threshold is configured (strain is then only recorded).
    over_strain: bool | None
    #: False when neither gate judged this pose (no PoseBusters verdict, no strain limit): not a pass.
    checked: bool
    ok: bool


class Gates(BaseModel):
    """`results.gates` as one row per (candidate, pose). `raw` is the record's own dict, unchanged."""

    ran: list[str]
    max_strain_kcal: float | None
    rows: list[GateRow]
    raw: dict[str, Any]


@router.get("/runs/{run_id}/gates", response_model=Gates)
def run_gates(request: Request, run_id: str) -> Gates:
    return Gates.model_validate(read_run.gate_rows(record(request, run_id)))


class CalcLines(BaseModel):
    """The SCF log lines of one pose: every line of the run's calc log whose candidate and original pose match."""

    file: str | None
    #: the original pose index the lines were matched on (the log's own `pose`)
    pose: int
    lines: list[dict[str, Any]]


@router.get("/runs/{run_id}/calcs", response_model=CalcLines)
def run_calcs(request: Request, run_id: str, candidate: str, pose: int, field_model: str | None = None) -> CalcLines:
    """Lines from the file `results.calcs` names (calcs.jsonl, or calcs-<candidate>.jsonl for a partial run), read as
    written, of one field model: `field_model`, else the run's primary one (`results.calcs.primary`). A line naming
    no model (a run from before models were recorded) is the primary's. `pose` is the record's place in the
    candidate's poses, as `/scene` takes it; it is mapped through `results.pose_index` to the log's original index.
    409 when the run kept no SCF log."""
    raw = record(request, run_id)
    try:
        got = read_run.calc_lines(Path(row(request, run_id)["path"]).parent, raw, candidate, pose, field_model)
    except read_run.PoseOutOfRange as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except read_run.CalcsUnavailable as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    return CalcLines.model_validate(got)


class DroppedPose(BaseModel):
    """One entry of `results.dropped_poses`: a leg that failed every rung, so its pose was dropped pairwise."""

    candidate: str
    #: the original pose index
    pose: int
    leg: str
    #: None for a vacuum leg (shared by every field model)
    field_model: str | None = None
    reason: str | None = None
    outcome: str | None = None
    rungs_tried: list[int] = []
    #: the candidates whose ddE lost this pose; absent for a drop under an extra charge model only
    dropped_with: list[str] | None = None


class PoseDrops(BaseModel):
    """What a `--temporal` run dropped. `pose_index` is `results.pose_index` (empty: no pose was dropped, record `k`
    is pose `k`)."""

    pose_index: dict[str, list[int]]
    dropped: list[DroppedPose]


@router.get("/runs/{run_id}/dropped_poses", response_model=PoseDrops)
def run_dropped_poses(request: Request, run_id: str) -> PoseDrops:
    res = record(request, run_id).get("results") or {}
    return PoseDrops(
        pose_index={k: [int(i) for i in v] for k, v in (res.get("pose_index") or {}).items()},
        dropped=[DroppedPose.model_validate(d) for d in res.get("dropped_poses") or []],
    )


@router.get("/runs/{run_id}/pose_atoms", response_model=M.PoseAtoms)
def run_pose_atoms(request: Request, run_id: str, candidate: str) -> M.PoseAtoms:
    """Atom symbols in the order of the candidate's pose SDF -- the order `[[pocket.site]]` indexes. Read from the
    SDF bytes, never derived from the SMILES, so a mismatch with any depiction is detectable."""
    r = run(request, run_id)
    if not r.has_pose_output:
        raise HTTPException(status_code=409, detail="this run record predates pose output")
    try:
        sdf = R.pose_sdf(r, candidate)
    except (KeyError, ValueError, OSError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    block = sdf.split("$$$$")[0]
    mol = Chem.MolFromMolBlock(block, sanitize=False, removeHs=False)
    if mol is None:
        raise HTTPException(status_code=409, detail=f"{candidate}: pose SDF could not be parsed")
    return M.PoseAtoms(
        candidate=candidate,
        symbols=[a.GetSymbol() for a in mol.GetAtoms()],
        source=r.record["results"]["poses"][candidate]["file"],
    )


@router.get("/runs/{run_id}/pymol.zip")
def run_pymol(request: Request, run_id: str) -> Response:
    """The run's poses as a PyMOL bundle: receptor.pdb, one SDF per candidate (the run's own pose files, unchanged,
    sha256-checked) and session.pml, which loads them relative to itself and colours ligands by the cut."""
    r = run(request, run_id)
    if not r.has_pose_output:
        raise HTTPException(status_code=409, detail="this run record predates pose output; it has no poses to export")
    try:
        receptor = R.receptor_pdb(r)
        if receptor is None:
            raise HTTPException(status_code=409, detail="this run has no receptor PDB, so there is no PyMOL scene")
        names = [n for n in r.candidates if n in r.record["results"]["poses"]]
        if not names:
            raise HTTPException(
                status_code=409, detail="this run recorded no pose files, so there is nothing to export"
            )
        sdfs = {n: R.pose_sdf(r, n) for n in names}
        session = pymol_session(names, r.parent, r.groups, r.survivors, r.unranked)
    except KeyError as e:  # a record without results.cut (or another contract key)
        raise HTTPException(
            status_code=409, detail=f"the run record lacks {e}; it cannot be coloured by the cut"
        ) from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("session.pml", session)
        z.writestr("receptor.pdb", receptor)
        stems = ligand_stems(names)
        for n, text in sdfs.items():
            z.writestr(ligand_filename(stems[n]), text)
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="pymol-{r.record["input_digest"][:12]}.zip"'},
    )


@router.get("/runs/{run_id}/bundle.zip")
def run_bundle(request: Request, run_id: str) -> Response:
    """The whole run directory as one zip: record, poses, receptor, decisions, and a report if one was written.

    The unit of sharing is the run directory (`docs/golden-path.md`), and until now the only way to get one was
    `smeltery golden bundle` in a shell -- which the person reading a Bench they did not start cannot run. The
    zip is built in memory and nothing is written beside the run, so this answers on a `--read-only` server too;
    a GET that writes would slip past the middleware, which refuses by method.
    """
    r = run(request, run_id)
    out = Path(r.out_dir).resolve()
    if not out.is_dir():
        raise HTTPException(status_code=409, detail=f"the run directory {out.name} is no longer on disk")
    digest = r.record["input_digest"][:12]
    buf = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(out.rglob("*")):
            # Symlinks and anything resolving outside the directory are skipped rather than followed: the
            # directory is read off disk, so its contents are not to be trusted to stay inside it.
            if not p.is_file() or p.is_symlink() or not p.resolve().is_relative_to(out):
                continue
            z.write(p, Path(f"run-{digest}") / p.relative_to(out))
            total += 1
    if total == 0:
        raise HTTPException(status_code=409, detail=f"the run directory {out.name} holds no files to bundle")
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="run-{digest}-bundle.zip"'},
    )


# ---------------------------------------------------------------------------------------------------- decisions


def _record_decision(request: Request) -> decisions.RecordDecision:
    st = ports(request)
    return decisions.RecordDecision(st.run_store, st.identity, st.clock)


@router.get("/decisions/vocabulary", response_model=M.Vocabulary)
def decisions_vocabulary() -> M.Vocabulary:
    return M.Vocabulary(statuses=list(STATUSES), reasons=list(REASONS), note_required=sorted(NOTE_REQUIRED))


@router.get("/runs/{run_id}/decisions", response_model=list[M.DecisionOut])
def list_decisions(request: Request, run_id: str) -> list[M.DecisionOut]:
    r = run(request, run_id)
    if not decisions.decisions_path(r).is_file():
        return []
    return [M.DecisionOut(**d.__dict__) for d in decisions.load(r).decisions]


@router.post("/runs/{run_id}/decisions", response_model=M.DecisionOut)
def record_decision(request: Request, run_id: str, body: M.DecisionIn) -> M.DecisionOut:
    """Every refusal is `DecisionSet`'s own; this route adds no rule. The user is `Identity.current()`."""
    r = run(request, run_id)
    try:
        d = _record_decision(request).set(r, body.candidate, body.status, body.reason, body.note)
    except decisions.DecisionRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return M.DecisionOut(**d.__dict__)


@router.post("/runs/{run_id}/decisions/bulk", response_model=list[M.DecisionOut])
def record_decisions_bulk(request: Request, run_id: str, body: M.BulkDecisionIn) -> list[M.DecisionOut]:
    """One decision applied to many candidates, all or nothing: any unknown candidate or invalid decision is a 400
    naming every problem, and nothing is written. The file is written once."""
    r = run(request, run_id)
    try:
        done = _record_decision(request).set_many(r, body.candidates, body.status, body.reason, body.note)
    except decisions.DecisionRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return [M.DecisionOut(**d.__dict__) for d in done]


def _decisions_download(request: Request, run_id: str, ext: str, serialise, media_type: str) -> Response:
    r = run(request, run_id)
    text = serialise(decisions.load(r))
    name = decisions.decisions_path(r).with_suffix(f".{ext}").name
    return Response(
        content=text, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{name}"'}
    )


@router.get("/runs/{run_id}/decisions.csv")
def decisions_csv(request: Request, run_id: str) -> Response:
    return _decisions_download(request, run_id, "csv", to_csv, "text/csv; charset=utf-8")


@router.get("/runs/{run_id}/decisions.sdf")
def decisions_sdf(request: Request, run_id: str) -> Response:
    return _decisions_download(request, run_id, "sdf", to_sdf, "chemical/x-mdl-sdfile")


@router.get("/runs/{run_id}/export.sdf")
def export_sdf(request: Request, run_id: str) -> Response:
    """Every pose of the run as one 3D SDF, with ddE, tie group, survivor, gate results and decisions as properties
    (`smeltery.sdf_export`). Unlike decisions.sdf (2D, one record per decided candidate) this is the poses."""
    r = run(request, run_id)
    try:
        text = enriched_sdf(r, decisions.load(r))
    except (ValueError, KeyError) as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    name = f"poses-{r.record['input_digest'][:12]}.sdf"
    return Response(
        content=text,
        media_type="chemical/x-mdl-sdfile",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


# ---------------------------------------------------------------------------------------------------- comments


@router.get("/runs/{run_id}/comments", response_model=list[M.CommentOut])
def list_comments(request: Request, run_id: str) -> list[M.CommentOut]:
    digest = record(request, run_id).get("input_digest", "")
    return [
        M.CommentOut(
            id=c.id, anchor=c.anchor_text or "run", author=c.author, body=c.body, created_at=utc_iso(c.created_at)
        )
        for c in ports(request).comment_store.for_run(digest)
    ]


@router.post("/runs/{run_id}/comments", response_model=M.CommentOut, status_code=201)
def add_comment(request: Request, run_id: str, body: M.CommentIn) -> M.CommentOut:
    st = ports(request)
    uc = CommentOnRun(st.comment_store, st.identity, st.clock)
    try:
        uc.check(body.anchor, body.body)  # before the run is looked up: a bad comment is a 400 on any run
        c = uc.run(record(request, run_id).get("input_digest", ""), body.anchor, body.body)
    except CommentRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return M.CommentOut(
        id=c.id, anchor=c.anchor_text or body.anchor, author=c.author, body=c.body, created_at=utc_iso(c.created_at)
    )


# ---------------------------------------------------------------------------------------------------- proposals


def _proposal_out(p: Proposal) -> M.ProposalOut:
    return M.ProposalOut(
        id=p.id,
        run=p.run,
        smiles=p.smiles,
        reason=p.reason,
        note=p.note,
        candidate=p.candidate,
        author=p.author,
        created_at=p.created_at,
        status=p.status,
    )


@router.get("/proposals/vocabulary", response_model=M.ProposalVocabulary)
def proposals_vocabulary() -> M.ProposalVocabulary:
    return M.ProposalVocabulary(reasons=list(Proposal.REASONS))


@router.get("/proposals", response_model=list[M.ProposalOut])
def list_proposals(request: Request, run: str | None = None) -> list[M.ProposalOut]:
    """The computational chemist's review queue. Optionally one run's (by input digest)."""
    return [_proposal_out(p) for p in ports(request).project_store.list_proposals(run)]


@router.post("/runs/{run_id}/proposals", response_model=M.ProposalOut, status_code=201)
def propose(request: Request, run_id: str, body: M.ProposalIn) -> M.ProposalOut:
    """Store a proposal for review. This route never touches the task queue: a comment must not start compute."""
    st = ports(request)
    digest = record(request, run_id).get("input_digest", "")
    try:
        p = ProposeCandidate(st.project_store, st.identity, st.clock).run(
            digest, body.smiles, body.reason, body.note, body.candidate
        )
    except ProposalRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return _proposal_out(p)


# ---------------------------------------------------------------------------------------------------- config


@router.get("/runs/{run_id}/config", response_model=M.RunConfig)
def run_config(request: Request, run_id: str) -> M.RunConfig:
    """The config file this run was executed from, as text, comments and all.

    The record names the file it read (`results.config`) and keeps its sha256, so what is returned is the input
    itself rather than a config rebuilt from the record: a rebuild drops the comments, and in this project the
    comments are the documentation. A run whose config was not kept beside it reports `text = None` instead of a
    reconstruction, because a plausible-looking config that is not the one that ran is worse than none.
    """
    return M.RunConfig(**read_run.recorded_config(run(request, run_id)))


# ---------------------------------------------------------------------------------------------------- snapshot


def _publish(request: Request) -> PublishReport:
    st = ports(request)
    return PublishReport(st.comment_store, st.clock)


@router.post("/runs/{run_id}/snapshot", response_model=M.Snapshot)
def snapshot(request: Request, run_id: str) -> M.Snapshot:
    """Write the frozen report beside the run, unmodified. Its numbers, plots and tables are inline; its only
    external references are the pinned, integrity-hashed 2D/3D libraries, and it says so when they cannot load."""
    snap = _publish(request).write(run(request, run_id))
    return M.Snapshot(path=str(snap.path), sha256=snap.sha256, external=snap.external)


@router.get("/runs/{run_id}/report.html")
def run_report_download(request: Request, run_id: str) -> Response:
    """The same frozen report, as a download.

    `POST .../snapshot` leaves the file on the server and names a path, which only helps someone with a shell on
    that machine. This hands the bytes to whoever is reading the page.
    """
    r = run(request, run_id)
    html = _publish(request).render(r)
    return Response(
        content=html.encode("utf-8"),
        media_type="text/html; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{report_name(r)}"'},
    )


# ---------------------------------------------------------------------------------------------------- compare


@router.get("/compare", response_model=M.Comparison)
def compare(request: Request, a: str, b: str, theme: Theme = "light") -> M.Comparison:
    """Two runs as arms, one comparison key each. An arm compared with itself is refused, not drawn. Each arm is a
    run id (an old positional integer still resolves, as everywhere else)."""
    if row(request, a)["path"] == row(request, b)["path"]:
        raise HTTPException(
            status_code=400, detail="a run is in both arms; an arm compared with itself is not a comparison"
        )
    got = CompareArms(ports(request).run_store).runs(run(request, a), run(request, b))
    cmp = got.comparison
    rows = [
        M.CompareRow(
            key=k.key,
            n_a=k.n_a,
            n_b=k.n_b,
            a=k.a,
            b=k.b,
            diff=k.diff,
            sem=k.sem if math.isfinite(k.sem) else None,
            resolved=k.resolved,
            unpaired=k.unpaired,
        )
        for k in cmp.rows()
    ]
    return M.Comparison(
        figure=themed(_plain(compare_figure(cmp)), theme),
        caption=comparison_caption(cmp),
        summary=cmp.summary(),
        rows=rows,
        unit=cmp.unit,
        candidates=got.candidates,
        candidates_note=got.candidates_note,
    )


class ResumeRun(BaseModel):
    """Which half-finished run directory to pick up."""

    out: str = Field(description="the run directory, relative to ROOT when not absolute")


@router.post(
    "/runs/resume",
    response_model=M.Task,
    status_code=201,
    responses={400: {"model": M.Problem}, 409: {"model": M.Problem}},
)
def resume_run(request: Request, body: ResumeRun) -> Any:
    """Re-submit `smeltery run` on a run directory that stopped before it finished.

    The run reads the checkpoint already in the directory and recomputes only what is missing, so the SCFs an
    interrupted attempt finished are reused rather than paid for twice (`RunProgress.reused` is that count). The
    directory's own staged `config.toml` is what is re-run -- not a pipeline's, which may have been edited since,
    and which a run records no name for in any case.

    Refused when the directory holds a run record (that is a finished result, and overwriting it would destroy a
    conclusion someone may have cited) or when it holds no `config.toml` (nothing to re-run; inferring one would
    run different inputs under this directory's name)."""
    st = ports(request)
    root = Path(st.settings.root)
    # `confined_out` takes an empty request to mean "use the default", and it refuses the workspace root only when
    # the root was asked for by name. Resume has no sensible default -- there is one directory it means, and the
    # caller knows it -- so an empty `out` is a bad request, not a licence to run whatever config sits at ROOT.
    if not body.out.strip():
        return JSONResponse(status_code=400, content={"reason": "out is required: name the run directory to resume"})
    out = confined_out(root, body.out, root)
    if isinstance(out, JSONResponse):
        return out
    if not out.is_dir():
        return JSONResponse(status_code=409, content={"reason": f"{body.out} is not a directory on this Bench"})
    try:
        tid = LaunchRun(st.task_queue, st.run_store, st.artifact_store, st.identity, st.clock, root=root).resume(out)
    except ResumeRefused as e:
        return JSONResponse(status_code=409, content={"reason": str(e)})
    return submitted_task(request, tid)
