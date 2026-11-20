"""The ligands step's routes: per-candidate checks that need no SCF and no docking, and the candidate-list edit.

Its own module so the tasks building step views never edit the same file. The checks are `use_cases.check_ligands`
and the edits `use_cases.edit_pipeline`; this module only translates HTTP.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Literal, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from smeltery.bench import ligand_io
from smeltery.bench.use_cases import edit_pipeline as editing
from smeltery.bench.use_cases.check_ligands import _core_found, check_ligands, export_records

from . import models as M
from .common import MAX_UPLOAD, EditedText, apply_edit, parse_or_400, pipeline_or_400, saved_pipeline, too_large

# `_core_found` is re-exported: tests clear its cache through this module.
__all__ = ["_core_found"]

router = APIRouter(prefix="/api/pipelines", tags=["ligands"])


class LigandsRequest(BaseModel):
    toml: str


class LigandCheck(BaseModel):
    name: str
    smiles: str
    is_parent: bool
    smiles_ok: bool
    formal_charge: int | None = Field(
        description="sum of the SMILES' formal charges, as the CLI uses; null if unparseable"
    )
    electrons: int | None = Field(description="electrons at that charge, hydrogens added: the CLI parity rule")
    electrons_even: bool | None = Field(description="false means the closed-shell RHF tiers cannot run it")
    sto3g_ok: bool | None = Field(description="false when an element has no STO-3G in ferric's bundled basis set")
    sto3g_missing: list[str] = Field(
        description="the elements without STO-3G: ferric's bundled set reaches Xe, so Cs and beyond"
    )
    core_atoms: list[int] = Field(
        description="atoms of this candidate (SMILES with explicit hydrogens, AddHs order) that PairedPoses copies "
        "exactly from the parent; empty when there is no core. For the parent: every atom"
    )
    problems: list[str] = Field(description="every reason this candidate would fail a run, in words")


class LigandsOut(BaseModel):
    parent: str
    candidates: list[LigandCheck]
    problems: list[str] = Field(description="problems with the list as a whole, e.g. a parent that is not a candidate")


class LigandsEdit(BaseModel):
    """One operation on the live text. The server reads the candidates from `toml` itself, so an edit can never be
    built from a stale copy of the list."""

    toml: str
    op: Literal["add", "remove", "rename", "set_smiles", "set_parent"]
    name: str = Field(description="the candidate the op applies to (for add: the new name)")
    smiles: str | None = Field(default=None, description="add and set_smiles: the SMILES")
    new_name: str | None = Field(default=None, description="rename: the new name")


@router.post("/ligands", response_model=LigandsOut, responses={400: {"model": M.Problem}})
def ligands_check(body: LigandsRequest) -> LigandsOut:
    """What can be said about each candidate before anything runs: parses, charge, electron parity, STO-3G cover
    and the core it shares with the parent. RDKit only; fast enough to call on every edit."""
    facts = check_ligands(pipeline_or_400(body.toml))
    return LigandsOut(
        parent=facts.parent,
        candidates=[LigandCheck(**asdict(c)) for c in facts.candidates],
        problems=facts.problems,
    )


def _refuse(detail: str) -> NoReturn:
    raise HTTPException(status_code=400, detail=detail)


@router.post("/ligands/edit", response_model=EditedText, responses={400: {"model": M.Problem}})
def ligands_edit(body: LigandsEdit) -> EditedText:
    """Apply one add / remove / rename / set_smiles / set_parent to the user's live text (comments and layout stay).
    Removing the parent is refused: pick another parent first. A rename of the parent moves `parent` with it."""
    return apply_edit(
        lambda: editing.edit_candidates(body.toml, body.op, body.name, smiles=body.smiles, new_name=body.new_name)
    )


class LigandsImport(BaseModel):
    toml: str
    filename: str = Field(description="decides the format: .csv or .sdf")
    content: str = Field(description="the file's text")


class RejectedRow(BaseModel):
    row: int = Field(description="1-based data row of a CSV, or record of an SDF; 0 for the file as a whole")
    input: str
    reason: str


class ImportedLigands(BaseModel):
    toml: str = Field(description="the live text with the accepted rows appended")
    accepted: list[str] = Field(description="names of the candidates appended")
    rejected: list[RejectedRow]


@router.post(
    "/ligands/import", response_model=ImportedLigands, responses={400: {"model": M.Problem}, 413: {"model": M.Problem}}
)
def ligands_import(body: LigandsImport) -> ImportedLigands:
    """Append the molecules of a CSV (smiles column, optional name column) or SDF to the live text's candidates.
    Rows RDKit cannot read, and names or molecules already present, are returned as `rejected` with the reason."""
    if len(body.content) > MAX_UPLOAD:
        raise too_large(body.filename, MAX_UPLOAD)
    p = pipeline_or_400(body.toml)
    rows, bad = ligand_io.parse(body.filename, body.content)
    new, rejected = ligand_io.accept(rows, bad, dict(p.candidates))
    if new:
        out = apply_edit(lambda: editing.write_candidates(body.toml, {**p.candidates, **new}, p.parent))
    else:
        out = EditedText(toml=body.toml)
    return ImportedLigands(toml=out.toml, accepted=list(new), rejected=[RejectedRow(**vars(r)) for r in rejected])


def _attachment(request: Request, name: str, render: Callable[[list[dict]], str], ext: str, media: str) -> Response:
    p = parse_or_400(saved_pipeline(request, name).text)
    if p is None:
        _refuse("this pipeline's TOML is not a pipeline config")
    return Response(
        render(export_records(p)),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}-ligands.{ext}"'},
    )


@router.get("/{name}/ligands.csv", responses={404: {"model": M.Problem}})
def ligands_csv(request: Request, name: str) -> Response:
    """The saved pipeline's candidates: name, SMILES, role and the RDKit-only checks."""
    return _attachment(request, name, ligand_io.to_csv, "csv", "text/csv")


@router.get("/{name}/ligands.sdf", responses={404: {"model": M.Problem}})
def ligands_sdf(request: Request, name: str) -> Response:
    """The saved pipeline's candidates as 2D structures with the same properties."""
    return _attachment(request, name, ligand_io.to_sdf, "sdf", "chemical/x-mdl-sdfile")
