"""The point-charge field a run would build, without docking or an SCF.

`[structure]` is cleaned and cut around its centre exactly as `smeltery run` does (minus the Meeko receptor),
`[pocket] file` is loaded whole, and placed sites are only counted. A refusal is `PreviewRefused` (a 400 over HTTP),
or `ToolMissing` when a .pdb needs pdb2pqr and it cannot be run (a 409).
"""

from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from smeltery.gui.scenes import CANDIDATE_CARBON, CHARGE_ZERO, add_field_spheres, data_uri
from smeltery.pocket.loader import PDB2PQR, Pdb2PqrUnavailableError, load_pocket, pdb2pqr_exe


class PreviewRefused(ValueError):
    """The config cannot be previewed as it stands; the message says why."""


class ToolMissing(PreviewRefused):
    """A .pdb needs pdb2pqr to assign charges, and it is not there or cannot run."""


@dataclass
class Preview:
    n_charges: int
    net_charge: float
    nearest_ang: float | None
    scene: dict[str, Any] | None
    source: str


Xyz = tuple[float, float, float]


def _xyz(v: Any) -> Xyz:
    a, b, c = (float(x) for x in v)
    return (a, b, c)


def _need_pdb2pqr() -> None:
    # `pdb2pqr_exe()`, not a bare `which`: it also looks beside `sys.executable`, which is where `pip install
    # pdb2pqr` puts the script. Running a venv's interpreter does not put that venv's bin on PATH, so a bare
    # `which` refused a preview the loader would then have charged happily — and it is the loader that does the work.
    if pdb2pqr_exe() is None:
        raise ToolMissing(
            f"`{PDB2PQR}` is not on PATH, and a .pdb needs it to assign charges: install it "
            "(pip install pdb2pqr) or point [pocket] file at a pre-made .pqr"
        )


def _scene(field: list, center: Xyz | None, cutoff: float | None, ligand: tuple[str, dict] | None) -> dict:
    from molviewspec import ComponentExpression, create_builder

    builder = create_builder()
    if ligand is not None:
        text, sel = ligand
        expr = ComponentExpression(label_comp_id=sel["res"], auth_asym_id=sel.get("chain"), auth_seq_id=sel.get("num"))
        lig = builder.download(url=data_uri(text.encode())).parse(format="pdb").model_structure(ref="reference_ligand")
        lig.component(selector=expr).representation(type="ball_and_stick").color(color=CANDIDATE_CARBON)
    add_field_spheres(builder, [(float(pc.q), *_xyz(pc.xyz_ang)) for pc in field], opacity=0.8)
    if center is not None and cutoff is not None:
        ring = builder.primitives(opacity=0.08, ref="cutoff")
        ring.sphere(center=center, radius=cutoff, color=CHARGE_ZERO, tooltip=f"pocket_cutoff {cutoff:g} A")
    return json.loads(builder.get_state(title="Active site").dumps(indent=None))


def _nearest(field: list, center: Xyz | None) -> float | None:
    if center is None or not len(field):
        return None
    xyz = np.array([pc.xyz_ang for pc in field], dtype=float)
    return float(np.linalg.norm(xyz - np.asarray(center), axis=1).min())


def pocket_preview(cfg: dict, base: Path) -> Preview:
    """The field config `cfg` (as `tomllib` parses it) would build, its relative paths read from `base`."""
    if "structure" in cfg:
        return _from_structure(cfg, base)
    pocket = cfg.get("pocket", {})
    if "file" in pocket:
        return _from_file(cfg, pocket, base)
    sites = pocket.get("site") or []
    if sites:
        return Preview(
            n_charges=len(sites),
            net_charge=float(sum(float(s.get("q", 1.0)) for s in sites)),
            nearest_ang=None,
            scene=None,
            source="hand-placed charges; they are placed on the parent's pose 0 when the pipeline runs",
        )
    raise PreviewRefused("no active site yet: add [structure], [pocket] file, or place a charge")


def _from_file(cfg: dict, pocket: dict, base: Path) -> Preview:
    path = Path(pocket["file"])
    path = path if path.is_absolute() else base / path
    if not path.is_file():
        raise PreviewRefused(f"[pocket] file {str(path)!r} does not exist")
    if path.suffix.lower() == ".pdb":
        _need_pdb2pqr()
    try:
        field = load_pocket(path)
    except Pdb2PqrUnavailableError as e:
        raise ToolMissing(str(e)) from e
    except ValueError as e:
        raise PreviewRefused(str(e)) from e
    box = cfg.get("docking", {}).get("box_center")
    center = _xyz(box) if box else None
    return Preview(
        n_charges=len(field),
        net_charge=float(sum(pc.q for pc in field)),
        nearest_ang=_nearest(field, center),
        scene=_scene(field, center, None, None),
        source=f"{path.name}, all {len(field)} charges",
    )


def _from_structure(cfg: dict, base: Path) -> Preview:
    from smeltery.prep import PrepConfigError, PrepError, clean_receptor, obtain_structure, reference_ligand_centroid

    s = cfg["structure"]
    for need in ("spec", "pocket_cutoff"):
        if need not in s:
            raise PreviewRefused(f"[structure] needs {need}")
    if "center" not in s and "reference_ligand" not in s:
        raise PreviewRefused("[structure] needs a reference_ligand or a center")
    _need_pdb2pqr()
    try:
        result = obtain_structure(s["spec"], s.get("provider", "pdb"), base, bool(s.get("allow_network", False)))
        if s.get("center") is not None:
            center = _xyz(s["center"])
        else:
            center, _ = reference_ligand_centroid(result.text, s["reference_ligand"])
        pdb_text, _ = clean_receptor(result.text, s.get("chains"))
    except (PrepConfigError, PrepError) as e:
        raise PreviewRefused(str(e)) from e
    cutoff = float(s["pocket_cutoff"])
    with tempfile.TemporaryDirectory() as tmp:
        pdb = Path(tmp) / "receptor.pdb"
        pdb.write_text(pdb_text)
        try:
            field = load_pocket(pdb, cutoff_ang=cutoff, center_ang=center)
        except Pdb2PqrUnavailableError as e:
            # Found but not runnable (a broken shim): `_need_pdb2pqr` only checks that it exists.
            raise ToolMissing(str(e)) from e
        except ValueError as e:
            raise PreviewRefused(str(e)) from e
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
    return Preview(
        n_charges=len(field),
        net_charge=float(sum(pc.q for pc in field)),
        nearest_ang=_nearest(field, center),
        scene=_scene(field, center, cutoff, ligand),
        source=f"{len(field)} of {field.provenance['n_total']} charges within {cutoff:g} A of "
        f"{tuple(round(v, 2) for v in center)}",
    )
