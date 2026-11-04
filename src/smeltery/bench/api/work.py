"""Tasks, pipelines and structures: the parts of Bench that start or prepare work.

A pipeline is always carried as its TOML. Every edit goes through `smeltery.gui.pipeline.Pipeline` and comes back as
TOML plus every issue `Pipeline.check()` finds, so the editor never holds a second copy of the rules or the state.
"""

from __future__ import annotations

import hashlib
import shlex
import tomllib
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from rdkit import Chem
from rdkit.Chem import AllChem

from smeltery.bench.provisional import place_site, provisional_pose
from smeltery.bench.use_cases.launch_run import LaunchRun
from smeltery.gui._toml import dumps
from smeltery.gui.catalog import catalog, catalog_json, method
from smeltery.gui.pipeline import Pipeline

from . import models as M
from .common import ports, run

router = APIRouter(prefix="/api", tags=["work"])


# ---------------------------------------------------------------------------------------------------- tasks


def _task(t: Any) -> M.Task:
    return M.Task(**(t.to_dict() if hasattr(t, "to_dict") else t))


@router.get("/tasks", response_model=list[M.Task], responses={503: {"model": M.Problem}})
def list_tasks(request: Request) -> Any:
    try:
        return [_task(t) for t in ports(request).task_queue.list()]
    except Exception as e:  # noqa: BLE001 - a dead backend degrades this panel, not the app
        return JSONResponse(status_code=503, content={"reason": str(e)})


@router.post("/tasks", response_model=M.Task, status_code=201, responses={503: {"model": M.Problem}})
def submit_task(request: Request, body: M.TaskSubmit) -> Any:
    try:
        command = shlex.split(body.command) if isinstance(body.command, str) else list(body.command)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"invalid command: {e}") from e
    if not command:
        raise HTTPException(status_code=400, detail="command required")
    q = ports(request).task_queue
    try:
        tid = q.submit(body.name or command[0], command, cwd=body.cwd)
        t = q.get(tid)
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"reason": str(e)})
    if t is None:
        raise HTTPException(status_code=500, detail=f"task {tid} was submitted but cannot be read back")
    return _task(t)


@router.get("/tasks/{task_id}/log", response_model=M.TaskLog)
def task_log(request: Request, task_id: str, lines: int = 200) -> M.TaskLog:
    try:
        return M.TaskLog(log=ports(request).task_queue.tail_log(task_id, lines=lines))
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no task {task_id}") from e


@router.post("/tasks/{task_id}/cancel", response_model=M.Task)
def cancel_task(request: Request, task_id: str) -> M.Task:
    q = ports(request).task_queue
    try:
        q.cancel(task_id)
    except KeyError as e:
        raise HTTPException(status_code=404, detail=f"no task {task_id}") from e
    t = q.get(task_id)
    if t is None:
        raise HTTPException(status_code=404, detail=f"no task {task_id}")
    return _task(t)


# ---------------------------------------------------------------------------------------------------- catalog


@router.get("/catalog", response_model=M.CatalogOut)
def get_catalog() -> dict[str, Any]:
    """`catalog_json()` plus, per method, the quantity it measures, its phase and rigour. Rigour orders methods only
    within one quantity (`Method.comparable_rigour`), so the palette groups by quantity and never draws one ladder."""
    out = catalog_json()
    for key, m in catalog().items():
        if key in out["methods"]:
            out["methods"][key].update(quantity=m.quantity, phase=m.phase, rigour=m.rigour)
    return out


# ---------------------------------------------------------------------------------------------------- pipeline


def _parse(text: str) -> Pipeline:
    try:
        return Pipeline.from_toml(text)
    except (tomllib.TOMLDecodeError, KeyError, ValueError, TypeError) as e:
        raise HTTPException(status_code=400, detail=f"cannot read this TOML: {e}") from e


def _state(p: Pipeline) -> M.PipelineState:
    issues = [M.Issue.model_validate({"level": i.level, "stage": i.stage, "message": i.message}) for i in p.check()]
    return M.PipelineState(
        toml=dumps(p.to_config()),
        ok=not any(i.level == "error" for i in issues),
        issues=issues,
        stages=p.view(),
        placed=p.keys(),
        params={st.method: {k: v for k, v in st.params.items() if v is not None} for st in p.stages},
        addable=p.addable(),
        campaign=p.campaign,
        parent=p.parent,
        candidates=dict(p.candidates),
    )


@router.post("/pipeline/check", response_model=M.PipelineState)
def pipeline_check(body: M.PipelineCheck) -> M.PipelineState:
    return _state(_parse(body.toml))


@router.post("/pipeline/from_run", response_model=M.PipelineState)
def pipeline_from_run(request: Request, body: M.PipelineFromRun) -> M.PipelineState:
    """A starting pipeline for the next round, rebuilt from what the run record holds: its candidates and parent,
    the cut's keep and z, a floor that came from the config, and the gates that ran. The record stores the field's
    charges as resolved positions, not the atom-index sites that produced them, so sites are not recovered: the
    notes say so, and click-to-place puts them back."""
    r = run(request, body.run)
    inputs = r.record.get("inputs", {})
    p = Pipeline.minimal(r.campaign, r.parent, dict(r.candidates))
    notes = []
    cut = {k: v for k, v in inputs.get("cut", {}).items() if k in ("keep", "z")}
    floor = inputs.get("floor", {})
    if str(floor.get("source", "")).startswith("config"):
        cut["floor"] = floor.get("value")
    p.set("cut", **cut)
    by_gate = {m.gate_key: k for k, m in catalog().items() if m.gate_key}  # the TOML's gate names -> stage keys
    for gate, on in inputs.get("gates", {}).items():
        key = by_gate.get(gate)
        if on is True and key and key in p.addable():
            p.add(key)
    if inputs.get("field"):
        notes.append(
            f"This run's {len(inputs['field'])} pocket charges are recorded as positions, not as the atom sites that "
            "placed them, so they are not carried over: place the sites again by clicking atoms."
        )
    state = _state(p)
    state.notes = notes
    return state


@router.post("/pipeline/edit", response_model=M.PipelineState)
def pipeline_edit(body: M.PipelineEdit) -> M.PipelineState:
    p = _parse(body.toml)
    try:
        if body.op == "add":
            p.add(body.key, body.params or None, at=body.at)
        elif body.op == "remove":
            p.remove(body.key)
        elif body.op == "move":
            if body.to is None:
                raise ValueError("move needs `to`")
            p.move(body.key, body.to)
        else:
            p.set(body.key, **(body.params or {}))
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e
    return _state(p)


@router.post("/pipeline/site", response_model=M.PipelineState)
def pipeline_site(body: M.SiteRequest) -> M.PipelineState:
    p = _parse(body.toml)
    try:
        place_site(p, body.atom, body.from_atom, q=body.q, distance=body.distance)
    except (KeyError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e).strip("'\"")) from e
    return _state(p)


@router.get("/provisional", response_model=M.Provisional)
def provisional(smiles: str) -> M.Provisional:
    """The provisional parent conformer: atoms in `Chem.AddHs` order, the order `[[pocket.site]]` indexes."""
    if Chem.MolFromSmiles(smiles) is None:
        raise HTTPException(status_code=400, detail=f"unparseable SMILES {smiles!r}")
    symbols, xyz = provisional_pose(smiles)
    mol = Chem.AddHs(Chem.MolFromSmiles(smiles))
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, (x, y, z) in enumerate(xyz):
        conf.SetAtomPosition(i, (float(x), float(y), float(z)))
    mol.RemoveAllConformers()
    mol.AddConformer(conf, assignId=True)
    AllChem.AssignStereochemistryFrom3D(mol)  # ty: ignore[unresolved-attribute]
    return M.Provisional(smiles=smiles, symbols=list(symbols), molblock=Chem.MolToMolBlock(mol))


@router.post("/pipeline/launch", response_model=M.Task, status_code=201, responses={409: {"model": M.Problem}})
def pipeline_launch(request: Request, body: M.LaunchRequest) -> Any:
    """Stage inputs into OUT by hash, write OUT/config.toml, and submit `smeltery run` on it. Refused while
    `Pipeline.check()` reports any error."""
    p = _parse(body.toml)
    errors = [i.message for i in p.check() if i.level == "error"]
    if errors:
        return JSONResponse(status_code=409, content={"reason": "; ".join(errors)})
    st = ports(request)
    root = Path(st.settings.root)
    out = Path(body.out) if body.out else root / "out" / f"{p.campaign}-{st.clock.now().strftime('%Y%m%d-%H%M%S')}"
    if not out.is_absolute():
        out = root / out
    tid = LaunchRun(st.task_queue, st.run_store, st.artifact_store, st.identity, st.clock).run(
        p, out_dir=out, config_dir=root
    )
    t = st.task_queue.get(tid)
    if t is None:
        raise HTTPException(status_code=500, detail=f"task {tid} was submitted but cannot be read back")
    return _task(t)


@router.get("/methods/{key}")
def get_method(key: str) -> dict[str, Any]:
    try:
        return catalog_json()["methods"][method(key).key]
    except KeyError as e:
        raise HTTPException(status_code=404, detail=str(e).strip("'\"")) from e


# ---------------------------------------------------------------------------------------------------- structure


def _origin(text: str, provider_kind: str | None) -> str:
    if provider_kind in ("EXPERIMENTAL", "PREDICTED"):
        return provider_kind
    head = text[:4000].upper()
    if "ALPHAFOLD" in head:
        return "PREDICTED"
    if any(line.startswith("EXPDTA") for line in text.splitlines()[:200]):
        return "EXPERIMENTAL"
    return "UNKNOWN (no EXPDTA record and no provider metadata)"


def _describe(request: Request, source: str, text: str, res: Any = None) -> M.StructureInfo:
    """Hash, origin and HETATM residues of a structure, remembered so its file can be served to the viewer."""
    data = text.encode()
    sha = hashlib.sha256(data).hexdigest()
    dropped: list[str] = []
    n_atoms = 0
    for line in text.splitlines():
        if line.startswith(("ATOM", "HETATM")):
            n_atoms += 1
        if line.startswith("HETATM"):
            name = line[17:21].strip()
            if name and name not in dropped:
                dropped.append(name)
    if n_atoms == 0:
        raise HTTPException(status_code=400, detail=f"{source}: no ATOM or HETATM records; is this a PDB file?")
    ports(request).structures[sha] = text
    return M.StructureInfo(
        source=source,
        sha256=sha,
        origin=_origin(text, getattr(res, "kind", None)),
        license=getattr(res, "license_id", None),
        attribution=getattr(res, "attribution", None),
        mean_plddt=getattr(res, "mean_plddt", None),
        dropped=dropped,
        n_atoms=n_atoms,
        file_url=f"/api/structure/{sha}.pdb",
    )


@router.post("/structure", response_model=M.StructureInfo)
def open_structure(request: Request, body: M.StructureRequest) -> M.StructureInfo:
    """Open a local .pdb path, a PDB id or a UniProt accession. Network sources need allow_network."""
    src = ports(request).structure_source
    looks_like_path = "/" in body.source or body.source.lower().endswith((".pdb", ".ent", ".cif"))
    if looks_like_path and not Path(body.source).is_file():
        raise HTTPException(status_code=400, detail=f"no such file: {body.source}")
    try:
        res = src.fetch_result(body.source, allow_network=body.allow_network)
    except PermissionError as e:
        raise HTTPException(status_code=400, detail=f"{body.source}: needs network access, which is off ({e})") from e
    except (OSError, ValueError) as e:
        raise HTTPException(status_code=400, detail=f"{body.source}: {e}") from e
    if res is None:
        raise HTTPException(status_code=400, detail=f"{body.source}: no such file, and not a fetchable id")
    return _describe(request, body.source, res.text, res)


@router.post("/structure/text", response_model=M.StructureInfo)
def open_structure_text(request: Request, body: M.StructureText) -> M.StructureInfo:
    """A structure dropped into the browser: it has a name and contents, but no path the server can read."""
    return _describe(request, body.name, body.text)


@router.get("/structure/{sha}.pdb")
def structure_file(request: Request, sha: str) -> Response:
    text = ports(request).structures.get(sha)
    if text is None:
        raise HTTPException(status_code=404, detail=f"no opened structure {sha}")
    return Response(content=text, media_type="chemical/x-pdb")
