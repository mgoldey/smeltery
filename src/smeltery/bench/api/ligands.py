"""The ligands step's routes: per-candidate checks that need no SCF and no docking, and the candidate-list edit.

Its own module so the tasks building step views never edit the same file.
"""

from __future__ import annotations

import tomllib
from collections.abc import Callable
from functools import lru_cache
from typing import Literal, NoReturn

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field
from rdkit import Chem

from smeltery.bench import ligand_io
from smeltery.cost import _STO3G_NBF
from smeltery.gui.config_text import ConfigTextError, apply
from smeltery.gui.pipeline import Pipeline
from smeltery.model import Candidate
from smeltery.tiers import NoCommonCoreError, PairedPoses, _net_charge

from . import models as M
from .pipelines import MAX_UPLOAD, EditedText, _get, _parse_or_400

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


class _NoCore(Exception):
    """Why a candidate has no core with the parent. Raised so `lru_cache` never stores a failure: only a core found
    is cached, and a timeout or a crash is tried again on the next check."""


@lru_cache(maxsize=512)
def _core_found(parent_smiles: str, smiles: str) -> tuple[int, ...]:
    """The analogue atoms `PairedPoses` copies from the parent: its own scaffold map, not a second MCS, with the
    run's own default search time so the check and the run agree. Pure in its two SMILES, so cached."""
    tier = PairedPoses(n_poses=1)
    parent = Candidate("parent", parent_smiles)
    try:
        tier.run([parent, Candidate("candidate", smiles)], {"parent": Candidate("parent", parent_smiles)})
    except NoCommonCoreError as e:
        if "timed out" in str(e):
            raise _NoCore(
                f"the common-core search with the parent timed out after {tier.mcs_timeout_s} s; "
                "a run searches the same way and would stop here too"
            ) from e
        raise _NoCore(str(e).removeprefix("candidate: ")) from e
    except Exception as e:  # noqa: BLE001 - one bad molecule is a row's problem, never a 500 for the table
        raise _NoCore(f"cannot pair with the parent: {e}") from e
    return tuple(sorted(a for a, _ in tier.scaffold_maps["candidate"]))


def _core(parent_smiles: str, name: str, smiles: str) -> tuple[list[int], str | None]:
    try:
        return list(_core_found(parent_smiles, smiles)), None
    except _NoCore as e:
        return [], f"{name}: {e}"


def _check(name: str, smiles: str, parent: Candidate, parent_ok: bool) -> LigandCheck:
    is_parent = name == parent.name
    mol = Chem.MolFromSmiles(smiles) if smiles.strip() else None
    if mol is None:
        return LigandCheck(
            name=name,
            smiles=smiles,
            is_parent=is_parent,
            smiles_ok=False,
            formal_charge=None,
            electrons=None,
            electrons_even=None,
            sto3g_ok=None,
            sto3g_missing=[],
            core_atoms=[],
            problems=[f"{name}: RDKit cannot read the SMILES {smiles!r}"],
        )
    molh = Chem.AddHs(mol)
    charge = _net_charge(smiles)
    electrons = sum(a.GetAtomicNum() for a in molh.GetAtoms()) - charge  # the CLI's _check_parity, exactly
    even = electrons % 2 == 0
    missing = sorted({a.GetSymbol() for a in molh.GetAtoms() if a.GetSymbol() not in _STO3G_NBF})
    problems = []
    if not even:
        problems.append(
            f"{name} has {electrons} electrons at charge {charge:+d}; the field-interaction tier is closed-shell "
            "RHF and is undefined for an odd electron count"
        )
    if missing:
        problems.append(f"{name} contains {', '.join(missing)}, which ferric's bundled STO-3G does not cover")
    if is_parent:
        core: list[int] = list(range(molh.GetNumAtoms()))
    elif not parent_ok:
        core = []  # the list-level problem says why, once
    else:
        core, why = _core(parent.smiles, name, smiles)
        if why:
            problems.append(why)
    return LigandCheck(
        name=name,
        smiles=smiles,
        is_parent=is_parent,
        smiles_ok=True,
        formal_charge=charge,
        electrons=electrons,
        electrons_even=even,
        sto3g_ok=not missing,
        sto3g_missing=missing,
        core_atoms=core,
        problems=problems,
    )


@router.post("/ligands", response_model=LigandsOut, responses={400: {"model": M.Problem}})
def ligands_check(body: LigandsRequest) -> LigandsOut:
    """What can be said about each candidate before anything runs: parses, charge, electron parity, STO-3G cover
    and the core it shares with the parent. RDKit only; fast enough to call on every edit."""
    p = _parse_or_400(body.toml)
    if p is None:
        raise HTTPException(status_code=400, detail="this TOML is not a pipeline config; fix it in the TOML pane")
    parent_smiles = p.candidates.get(p.parent, "")
    parent = Candidate(p.parent, parent_smiles)
    parent_ok = bool(parent_smiles.strip()) and Chem.MolFromSmiles(parent_smiles) is not None
    problems = []
    if p.candidates and p.parent not in p.candidates:
        problems.append(f"the parent {p.parent!r} is not among the candidates, so no core can be found")
    elif p.candidates and not parent_ok:
        problems.append(f"the parent {p.parent!r} has a SMILES that cannot be read, so no core can be found")
    return LigandsOut(
        parent=p.parent,
        candidates=[_check(n, s, parent, parent_ok) for n, s in p.candidates.items()],
        problems=problems,
    )


def _refuse(detail: str) -> NoReturn:
    raise HTTPException(status_code=400, detail=detail)


@router.post("/ligands/edit", response_model=EditedText, responses={400: {"model": M.Problem}})
def ligands_edit(body: LigandsEdit) -> EditedText:
    """Apply one add / remove / rename / set_smiles / set_parent to the user's live text (comments and layout stay).
    Removing the parent is refused: pick another parent first. A rename of the parent moves `parent` with it."""
    p = _parse_or_400(body.toml)
    if p is None:
        _refuse("this TOML is not a pipeline config; fix it in the TOML pane")
    cands = dict(p.candidates)
    parent = p.parent
    name = body.name.strip() if body.op == "add" else body.name
    if body.op == "add":
        if not name:
            _refuse("a candidate needs a name")
        if name in cands:
            _refuse(f"there is already a candidate named {name!r}; names must be unique")
        cands[name] = (body.smiles or "").strip()
    elif name not in cands:
        _refuse(f"no candidate named {name!r}")
    elif body.op == "remove":
        if name == parent:
            _refuse(
                f"{name!r} is the parent, and every pair is measured against it; "
                "pick another parent first, then remove it"
            )
        del cands[name]
    elif body.op == "set_smiles":
        cands[name] = body.smiles or ""
    elif body.op == "set_parent":
        parent = name
    else:  # rename (the text editor appends a new key, so the entry moves to the end)
        new = (body.new_name or "").strip()
        if not new:
            _refuse("a candidate needs a name")
        if new != name and new in cands:
            _refuse(f"there is already a candidate named {new!r}; names must be unique")
        cands = {(new if k == name else k): v for k, v in cands.items()}
        if parent == name:
            parent = new
    return _write_candidates(body.toml, cands, parent)


def _write_candidates(toml: str, cands: dict[str, str], parent: str) -> EditedText:
    """The one writer of `[candidates]` and `parent` into the live text, shared by every route that changes them."""
    target = tomllib.loads(toml)
    target["candidates"] = cands
    target["parent"] = parent
    try:
        return EditedText(toml=apply(toml, target))
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


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
        raise HTTPException(status_code=413, detail=f"{body.filename}: larger than {MAX_UPLOAD // (1024 * 1024)} MB")
    p = _parse_or_400(body.toml)
    if p is None:
        _refuse("this TOML is not a pipeline config; fix it in the TOML pane")
    rows, bad = ligand_io.parse(body.filename, body.content)
    new, rejected = ligand_io.accept(rows, bad, dict(p.candidates))
    out = _write_candidates(body.toml, {**p.candidates, **new}, p.parent) if new else EditedText(toml=body.toml)
    return ImportedLigands(toml=out.toml, accepted=list(new), rejected=[RejectedRow(**vars(r)) for r in rejected])


def _records(p: Pipeline) -> list[dict]:
    parent = Candidate(p.parent, p.candidates.get(p.parent, ""))
    out = []
    for name, smiles in p.candidates.items():
        c = _check(name, smiles, parent, parent_ok=False)  # the core search is not an export column: skip it
        out.append(
            {
                "name": name,
                "smiles": smiles,
                "role": "parent" if c.is_parent else "analogue",
                "formal_charge": c.formal_charge,
                "electrons": c.electrons,
                "electrons_even": c.electrons_even,
                "sto3g_ok": c.sto3g_ok,
            }
        )
    return out


def _attachment(request: Request, name: str, render: Callable[[list[dict]], str], ext: str, media: str) -> Response:
    p = _parse_or_400(_get(request, name).text)
    if p is None:
        _refuse("this pipeline's TOML is not a pipeline config")
    return Response(
        render(_records(p)),
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
