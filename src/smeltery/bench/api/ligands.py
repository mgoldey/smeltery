"""The ligands step's routes: per-candidate checks that need no SCF and no docking, and the candidate-list edit.

Its own module so the tasks building step views never edit the same file.
"""

from __future__ import annotations

import tomllib

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from rdkit import Chem

from smeltery.cost import _STO3G_NBF
from smeltery.gui.config_text import ConfigTextError, apply
from smeltery.model import Candidate
from smeltery.tiers import NoCommonCoreError, PairedPoses, _net_charge

from . import models as M
from .pipelines import EditedText, _parse_or_400

router = APIRouter(prefix="/api/pipelines", tags=["ligands"])

#: A check runs on every edit, so the MCS search gets a short leash; a timeout is reported, not hidden.
CORE_TIMEOUT_S = 3


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
    sto3g_missing: list[str] = Field(description="the elements without STO-3G, e.g. Br and I")
    core_atoms: list[int] = Field(
        description="atoms of this candidate (SMILES with explicit hydrogens, AddHs order) that PairedPoses copies "
        "exactly from the parent; empty when there is no core. For the parent: every atom"
    )
    problems: list[str] = Field(description="every reason this candidate would fail a run, in words")


class LigandsOut(BaseModel):
    parent: str
    candidates: list[LigandCheck]


class LigandsEdit(BaseModel):
    toml: str
    candidates: dict[str, str] = Field(description="the whole new candidate list, name -> SMILES, in order")
    parent: str


def _core(parent: Candidate, cand: Candidate) -> tuple[list[int], str | None]:
    """The analogue atoms `PairedPoses` copies from the parent: its own scaffold map, not a second MCS."""
    tier = PairedPoses(n_poses=1, mcs_timeout_s=CORE_TIMEOUT_S)
    try:
        tier.run([Candidate(parent.name, parent.smiles), cand], {"parent": Candidate(parent.name, parent.smiles)})
    except NoCommonCoreError as e:
        return [], str(e)
    except (RuntimeError, ValueError) as e:  # an embedding that fails is a reason, not a crash
        return [], f"{cand.name}: cannot pair with the parent: {e}"
    return sorted(a for a, _ in tier.scaffold_maps[cand.name]), None


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
        core = []
        problems.append(f"{name}: no core, because the parent's SMILES cannot be read")
    else:
        core, why = _core(parent, Candidate(name, smiles))
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
    return LigandsOut(parent=p.parent, candidates=[_check(n, s, parent, parent_ok) for n, s in p.candidates.items()])


@router.post("/ligands/edit", response_model=EditedText, responses={400: {"model": M.Problem}})
def ligands_edit(body: LigandsEdit) -> EditedText:
    """Write the candidate list and the parent into the user's text (comments and layout stay). Removing the parent
    is refused: say which one to use instead first."""
    p = _parse_or_400(body.toml)
    if p is None:
        raise HTTPException(status_code=400, detail="this TOML is not a pipeline config; fix it in the TOML pane")
    if any(not n.strip() for n in body.candidates):
        raise HTTPException(status_code=400, detail="a candidate needs a name")
    if body.parent not in body.candidates:
        if body.parent == p.parent:
            raise HTTPException(
                status_code=400,
                detail=f"{body.parent!r} is the parent, and every pair is measured against it; "
                "pick another parent first, then remove it",
            )
        raise HTTPException(status_code=400, detail=f"the parent {body.parent!r} is not among the candidates")
    target = tomllib.loads(body.toml)
    target["candidates"] = dict(body.candidates)
    target["parent"] = body.parent
    try:
        return EditedText(toml=apply(body.toml, target))
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
