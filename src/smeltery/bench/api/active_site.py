"""The active-site step's routes: `POST /api/pipelines/pocket_preview`.

Its own module so the tasks building step views never edit the same file.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Literal

import numpy as np
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from smeltery.gui.config_text import ConfigTextError, apply
from smeltery.gui.pipeline import Pipeline
from smeltery.gui.scenes import (
    CANDIDATE_CARBON,
    CHARGE_NEGATIVE,
    CHARGE_POSITIVE,
    CHARGE_ZERO,
    data_uri,
)
from smeltery.pocket.loader import PDB2PQR, load_pocket, pdb2pqr_exe

from . import models as M
from .common import ports
from .pipelines import EditedText, _parse_or_400, _patch, _workspace_defaults

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


Xyz = tuple[float, float, float]


def _xyz(v: Any) -> Xyz:
    a, b, c = (float(x) for x in v)
    return (a, b, c)


def _need_pdb2pqr() -> None:
    # `pdb2pqr_exe()`, not a bare `which`: it also looks beside `sys.executable`, which is where `pip install
    # pdb2pqr` puts the script. Running a venv's interpreter does not put that venv's bin on PATH, so a bare
    # `which` refused a preview the loader would then have charged happily — and it is the loader that does the work.
    if pdb2pqr_exe() is None:
        raise HTTPException(
            status_code=409,
            detail=f"`{PDB2PQR}` is not on PATH, and a .pdb needs it to assign charges: install it "
            "(pip install pdb2pqr) or point [pocket] file at a pre-made .pqr",
        )


def _base(request: Request, name: str | None) -> Path:
    if name is None:
        return Path(".")
    try:
        return Path(ports(request).pipeline_store.get(name).folder)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=404, detail=f"no pipeline {name}") from e


def _scene(field: list, center: Xyz | None, cutoff: float | None, ligand: tuple[str, dict] | None) -> dict:
    from molviewspec import ComponentExpression, create_builder

    builder = create_builder()
    if ligand is not None:
        text, sel = ligand
        expr = ComponentExpression(label_comp_id=sel["res"], auth_asym_id=sel.get("chain"), auth_seq_id=sel.get("num"))
        lig = builder.download(url=data_uri(text.encode())).parse(format="pdb").model_structure(ref="reference_ligand")
        lig.component(selector=expr).representation(type="ball_and_stick").color(color=CANDIDATE_CARBON)
    prims = builder.primitives(opacity=0.8, ref="field")
    for pc in field:
        q = float(pc.q)
        color = CHARGE_POSITIVE if q > 0 else CHARGE_NEGATIVE if q < 0 else CHARGE_ZERO
        prims.sphere(
            center=_xyz(pc.xyz_ang),
            radius=0.3 + 0.25 * min(abs(q), 2.0),
            color=color,
            tooltip=f"q = {q:+.3g} e",
        )
    if center is not None and cutoff is not None:
        ring = builder.primitives(opacity=0.08, ref="cutoff")
        ring.sphere(center=center, radius=cutoff, color=CHARGE_ZERO, tooltip=f"pocket_cutoff {cutoff:g} A")
    return json.loads(builder.get_state(title="Active site").dumps(indent=None))


def _nearest(field: list, center: Xyz | None) -> float | None:
    if center is None or not len(field):
        return None
    xyz = np.array([pc.xyz_ang for pc in field], dtype=float)
    return float(np.linalg.norm(xyz - np.asarray(center), axis=1).min())


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
    if "structure" in cfg:
        return _from_structure(cfg, base)
    pocket = cfg.get("pocket", {})
    if "file" in pocket:
        return _from_file(cfg, pocket, base)
    sites = pocket.get("site") or []
    if sites:
        return PocketPreview(
            n_charges=len(sites),
            net_charge=float(sum(float(s.get("q", 1.0)) for s in sites)),
            nearest_ang=None,
            scene=None,
            source="hand-placed charges; they are placed on the parent's pose 0 when the pipeline runs",
        )
    raise HTTPException(status_code=400, detail="no active site yet: add [structure], [pocket] file, or place a charge")


def _from_file(cfg: dict, pocket: dict, base: Path) -> PocketPreview:
    path = Path(pocket["file"])
    path = path if path.is_absolute() else base / path
    if not path.is_file():
        raise HTTPException(status_code=400, detail=f"[pocket] file {str(path)!r} does not exist")
    if path.suffix.lower() == ".pdb":
        _need_pdb2pqr()
    try:
        field = load_pocket(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    box = cfg.get("docking", {}).get("box_center")
    center = _xyz(box) if box else None
    return PocketPreview(
        n_charges=len(field),
        net_charge=float(sum(pc.q for pc in field)),
        nearest_ang=_nearest(field, center),
        scene=_scene(field, center, None, None),
        source=f"{path.name}, all {len(field)} charges",
    )


def _from_structure(cfg: dict, base: Path) -> PocketPreview:
    from smeltery.prep import PrepConfigError, PrepError, clean_receptor, obtain_structure, reference_ligand_centroid

    s = cfg["structure"]
    for need in ("spec", "pocket_cutoff"):
        if need not in s:
            raise HTTPException(status_code=400, detail=f"[structure] needs {need}")
    if "center" not in s and "reference_ligand" not in s:
        raise HTTPException(status_code=400, detail="[structure] needs a reference_ligand or a center")
    _need_pdb2pqr()
    try:
        result = obtain_structure(s["spec"], s.get("provider", "pdb"), base, bool(s.get("allow_network", False)))
        if s.get("center") is not None:
            center = _xyz(s["center"])
        else:
            center, _ = reference_ligand_centroid(result.text, s["reference_ligand"])
        pdb_text, _ = clean_receptor(result.text, s.get("chains"))
    except (PrepConfigError, PrepError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    cutoff = float(s["pocket_cutoff"])
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        pdb = Path(tmp) / "receptor.pdb"
        pdb.write_text(pdb_text)
        try:
            field = load_pocket(pdb, cutoff_ang=cutoff, center_ang=center)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
    ligand = None
    ref = s.get("reference_ligand")
    if ref:
        parts = ref.split(":")
        sel: dict[str, Any] = {"res": parts[0]}
        if len(parts) > 1 and parts[1]:
            sel["chain"] = parts[1]
        if len(parts) > 2 and parts[2].lstrip("-").isdigit():
            sel["num"] = int(parts[2])
        ligand = (result.text, sel)
    return PocketPreview(
        n_charges=len(field),
        net_charge=float(sum(pc.q for pc in field)),
        nearest_ang=_nearest(field, center),
        scene=_scene(field, center, cutoff, ligand),
        source=f"{len(field)} of {field.provenance['n_total']} charges within {cutoff:g} A of "
        f"{tuple(round(v, 2) for v in center)}",
    )


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


def _vec3(v: list[float] | None, what: str) -> list[float] | None:
    if v is None:
        return None
    if len(v) != 3:
        raise ValueError(f"{what} is three numbers")
    return [float(x) for x in v]


def _choose(p: Pipeline, b: ActiveSiteChoice, defaults: dict[str, dict[str, Any]]) -> None:
    def ensure(key: str) -> None:
        if key not in p.keys():
            p.add(key, defaults=defaults)

    if b.choice == "structure":
        if not b.spec or b.pocket_cutoff is None or (b.reference_ligand is None and b.center is None):
            raise ValueError("a structure needs spec, pocket_cutoff and a reference_ligand or a center")
        ensure("structure")
        p.set(
            "structure",
            spec=b.spec,
            reference_ligand=b.reference_ligand,
            center=_vec3(b.center, "center") if b.reference_ligand is None else None,
            pocket_cutoff=b.pocket_cutoff,
            allow_network=True if b.allow_network else None,
        )
        if "pocket" in p.keys():
            p.remove("pocket")
        ensure("docking")
        p.set("docking", receptor=None, box_center=None)
    elif b.choice == "pocket_file":
        if not b.file or not b.receptor:
            raise ValueError("a pocket file needs the file and the prepared receptor")
        if "structure" in p.keys():
            p.remove("structure")
        ensure("pocket")
        p.set("pocket", file=b.file, site=None)
        ensure("docking")
        p.set("docking", receptor=b.receptor, box_center=_vec3(b.box_center, "box_center"))
        if b.box_size is not None:
            p.set("docking", box_size=_vec3(b.box_size, "box_size"))
    else:
        if "structure" in p.keys():
            p.remove("structure")
        if "docking" in p.keys():
            p.remove("docking")
        ensure("pocket")
        p.set("pocket", file=None)


@router.post("/active_site", response_model=EditedText, responses={400: {"model": M.Problem}})
def choose_active_site(request: Request, body: ActiveSiteChoice) -> EditedText:
    """Switch step 1 to one of its three ways, writing only the lines that change into the user's text
    (`config_text.apply`): a structure adds `[structure]` and `[docking]`, a pocket file keeps `[pocket] file` with
    the docking receptor and box, hand-placed charges drop both."""
    p = _parse_or_400(body.toml)
    if p is None:
        raise HTTPException(status_code=400, detail="this TOML is not a pipeline config; fix it in the TOML pane")
    before = p.to_config()
    try:
        _choose(p, body, _workspace_defaults(request))
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e
    target = tomllib.loads(body.toml)
    _patch(target, before, p.to_config())
    try:
        return EditedText(toml=apply(body.toml, target))
    except ConfigTextError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
