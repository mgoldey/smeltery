"""3D scenes of a run's poses as MolViewSpec states, for a Mol* viewer (`docs/gui-contract.md` section 2).

molviewspec is imported inside `pose_scene`, so `smeltery.gui` imports without it.

How the scene gets its data: MolViewSpec has no inline-data node; a structure is always a `download(url=...)`.
`pose_scene` therefore maps every file it shows through `data_url(path) -> url`, where `path` is the file's path
as the run recorded it (`poses/<file>.sdf`, relative to OUT; the receptor's path, relative to OUT or absolute).
The default turns the sha256-checked file into a `data:text/plain;base64,...` URI, so the returned dict is
self-contained: hand it to any Mol* viewer (`molviewspec.molstar_html(state)`, Mol*'s `loadMvsData(json, 'mvsj')`)
and nothing is fetched. Pass `data_url` to serve the files instead, e.g. `lambda p: f"/files/{p}"` for a server
that maps OUT, or `lambda p: p` together with an MVSX archive holding those paths
(`molviewspec.molstar_html(state, data={p: bytes})`). Each SDF is the candidate's whole pose file (verified
against the record); the pose is picked with `model_structure(block_index=pose)`, so the same URL serves every pose.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Callable

import numpy as np
from rdkit import Chem

from .results import Run, _verified_bytes, pose_output

#: Receptor residues with any atom this close (Angstrom) to the candidate's pose are drawn as ball-and-stick.
NEAR_ANGSTROM = 5.0
CANDIDATE_CARBON = "#33a02c"
PARENT_COLOR = "#a0a0a0"
PARENT_OPACITY = 0.45
RECEPTOR_CARBON = "#c8c8c8"
#: Point-charge colours by sign: electrostatics convention, positive blue, negative red.
CHARGE_POSITIVE = "#2166ac"
CHARGE_NEGATIVE = "#b2182b"
CHARGE_ZERO = "#808080"
_ELEMENT_COLORS = {
    "H": "#f0f0f0",
    "N": "#3050f8",
    "O": "#ff0d0d",
    "S": "#e6c619",
    "P": "#ff8000",
    "F": "#90e050",
    "Cl": "#1ff01f",
    "Br": "#a62929",
    "I": "#940094",
}


def data_uri(data: bytes) -> str:
    """The default `data_url` target: the file's bytes as a base64 data URI."""
    return "data:text/plain;base64," + base64.b64encode(data).decode("ascii")


def _pose_coords(sdf: str, pose: int, what: str) -> np.ndarray:
    mols = list(Chem.ForwardSDMolSupplier(io.BytesIO(sdf.encode()), removeHs=False, sanitize=False))
    if not 0 <= pose < len(mols):
        raise IndexError(f"{what} has {len(mols)} pose(s); no pose {pose}")
    if mols[pose] is None:
        raise ValueError(f"{what}: pose {pose} does not parse")
    return mols[pose].GetConformer().GetPositions()


def _near_residues(pdb: str, ligand: np.ndarray, cutoff: float) -> list[tuple[str, int, str]]:
    """(chain, resSeq, iCode) of receptor residues with an atom within `cutoff` of any ligand atom."""
    near: dict[tuple[str, int, str], None] = {}
    for line in pdb.splitlines():
        if not line.startswith(("ATOM  ", "HETATM")):
            continue
        xyz = np.array([float(line[30:38]), float(line[38:46]), float(line[46:54])])
        if np.min(np.linalg.norm(ligand - xyz, axis=1)) <= cutoff:
            near[(line[21].strip(), int(line[22:26]), line[26].strip())] = None
    return list(near)


def _color_by_element(rep, carbon: str) -> None:
    from molviewspec import ComponentExpression

    rep.color(color=carbon)
    for symbol, color in _ELEMENT_COLORS.items():
        rep.color(color=color, selector=ComponentExpression(type_symbol=symbol.upper()))


def pose_scene(
    run: Run,
    candidate: str,
    pose: int,
    *,
    with_parent: bool = True,
    data_url: Callable[[str], str] | None = None,
) -> dict:
    """A MolViewSpec state as a JSON-ready dict (builder `.get_state()`, then `json.loads`): the receptor as cartoon
    plus ball-and-stick for residues within 5 A of the ligand, when the run has one; the candidate's pose `pose`
    (element colours, green carbons); the parent's pose `pose` in muted grey, translucent, when `with_parent` and
    the candidate is not the parent; the field charges as spheres, blue positive and red negative, radius growing
    with |q|, tooltip giving q. The camera focuses the candidate.

    `data_url(path)` gives the URL for a file the run recorded (see the module docstring); by default a base64
    data URI of the file's verified bytes, so the dict carries all its data. Files are sha256-checked against the
    record either way: ValueError naming the file when one is missing or changed."""
    import json

    from molviewspec import ComponentExpression, create_builder

    poses = pose_output(run, "poses")
    if candidate not in poses:
        raise KeyError(f"no poses recorded for {candidate!r}; candidates with poses: {sorted(poses)}")

    def url(entry: dict, what: str) -> tuple[bytes, str]:
        data = _verified_bytes(run, entry, what)
        return data, (data_url(entry["file"]) if data_url is not None else data_uri(data))

    builder = create_builder()
    sdf, sdf_url = url(poses[candidate], f"poses of {candidate!r}")
    ligand_xyz = _pose_coords(sdf.decode(), pose, f"poses of {candidate!r}")

    receptor = pose_output(run, "receptor")
    if receptor is not None:
        pdb, pdb_url = url(receptor, "receptor")
        rec = builder.download(url=pdb_url).parse(format="pdb").model_structure(ref="receptor")
        rec.component(selector="polymer").representation(type="cartoon").color(color=RECEPTOR_CARBON)
        near = _near_residues(pdb.decode(), ligand_xyz, NEAR_ANGSTROM)
        if near:
            sel = [
                ComponentExpression(auth_asym_id=c or None, auth_seq_id=s, pdbx_PDB_ins_code=i or None)
                for c, s, i in near
            ]
            _color_by_element(
                rec.component(selector=sel).representation(type="ball_and_stick", size_factor=0.7), RECEPTOR_CARBON
            )

    if with_parent and candidate != run.parent:
        _, parent_url = url(poses[run.parent], f"poses of parent {run.parent!r}")
        par = builder.download(url=parent_url).parse(format="sdf").model_structure(block_index=pose, ref="parent")
        par.component(selector="all").representation(type="ball_and_stick", size_factor=0.8).color(
            color=PARENT_COLOR
        ).opacity(opacity=PARENT_OPACITY)

    lig = builder.download(url=sdf_url).parse(format="sdf").model_structure(block_index=pose, ref="candidate")
    comp = lig.component(selector="all")
    _color_by_element(comp.representation(type="ball_and_stick"), CANDIDATE_CARBON)
    comp.focus()

    field = pose_output(run, "field")
    if field:
        prims = builder.primitives(opacity=0.7, ref="field")
        for q, x, y, z in field:
            color = CHARGE_POSITIVE if q > 0 else CHARGE_NEGATIVE if q < 0 else CHARGE_ZERO
            radius = 0.3 + 0.25 * min(abs(float(q)), 2.0)
            prims.sphere(center=(float(x), float(y), float(z)), radius=radius, color=color, tooltip=f"q = {q:+.3g} e")

    state = builder.get_state(title=f"{candidate} pose {pose}", description=f"{run.campaign}: {candidate} pose {pose}")
    return json.loads(state.dumps(indent=None))
