"""The active-site step's routes: `POST /api/pipelines/pocket_preview` and `POST /api/pipelines/active_site`.

Its own module so the tasks building step views never edit the same file. What the preview computes is
`use_cases.pocket_preview`; how a choice edits the text is `use_cases.edit_pipeline.choose_active_site`. This module
only translates HTTP.
"""

from __future__ import annotations

import tomllib
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from smeltery.bench.use_cases import edit_pipeline as editing
from smeltery.bench.use_cases import pocket_preview as previewing

from . import models as M
from .common import EditedText, apply_edit, ports, workspace_defaults

router = APIRouter(prefix="/api/pipelines", tags=["active_site"])


class PocketPreviewRequest(BaseModel):
    toml: str
    pipeline: str | None = Field(None, description="the saved pipeline: relative paths resolve against its folder")


class PocketPreview(BaseModel):
    n_charges: int
    net_charge: float = Field(description="sum of the point charges, e")
    nearest_ang: float | None = Field(description="nearest charge to the box centre / reference ligand, Angstrom")
    scene: dict[str, Any] | None = Field(
        description="a MolViewSpec state for the viewer; null when nothing is placed in 3D"
    )
    source: str = Field(description="what the field was built from, in words")


def _base(request: Request, name: str | None) -> Path:
    if name is None:
        return Path(".")
    try:
        return Path(ports(request).pipeline_store.get(name).folder)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"no pipeline {name}") from e


@router.post(
    "/pocket_preview", response_model=PocketPreview, responses={400: {"model": M.Problem}, 409: {"model": M.Problem}}
)
def pocket_preview(request: Request, body: PocketPreviewRequest) -> PocketPreview:
    """The point-charge field the run would build, without docking or an SCF: `[structure]` is cleaned and cut around
    its centre exactly as `smeltery run` does (minus the Meeko receptor), `[pocket] file` is loaded whole, and placed
    sites are only counted. 409 names the binary a .pdb needs when it is missing."""
    try:
        cfg = tomllib.loads(body.toml)
    except tomllib.TOMLDecodeError as e:
        raise HTTPException(status_code=400, detail=f"cannot read this TOML: {e}") from e
    base = _base(request, body.pipeline)
    try:
        return PocketPreview(**asdict(previewing.pocket_preview(cfg, base)))
    except previewing.ToolMissing as e:
        raise HTTPException(status_code=409, detail=str(e)) from e
    except previewing.PreviewRefused as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


class ActiveSiteChoice(BaseModel):
    toml: str
    choice: Literal["structure", "pocket_file", "hand_placed"]
    spec: str | None = Field(None, description="structure: a .pdb path, PDB id or accession")
    reference_ligand: str | None = Field(None, description="structure: HETATM RES[:CHAIN[:RESNUM]]; or center")
    center: list[float] | None = None
    pocket_cutoff: float | None = None
    allow_network: bool = False
    file: str | None = Field(None, description="pocket_file: a .pdb or .pqr in the receptor's frame")
    receptor: str | None = Field(None, description="pocket_file: the prepared .pdbqt docking uses")
    box_center: list[float] | None = None
    box_size: list[float] | None = None


@router.post("/active_site", response_model=EditedText, responses={400: {"model": M.Problem}})
def choose_active_site(request: Request, body: ActiveSiteChoice) -> EditedText:
    """Switch step 1 to one of its three ways, writing only the lines that change into the user's text
    (`config_text.apply`): a structure adds `[structure]` and `[docking]`, a pocket file keeps `[pocket] file` with
    the docking receptor and box, hand-placed charges drop both."""
    site = editing.ActiveSite(**body.model_dump(exclude={"toml"}))
    return apply_edit(
        lambda: editing.choose_active_site(
            body.toml, site, defaults=lambda: workspace_defaults(request, label="methods.toml")
        )
    )
