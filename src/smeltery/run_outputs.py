"""What a run leaves under OUT: the pose SDFs, the calc log, the run record and the `--plan --out` preview
(docs/gui-contract.md section 1), and the file names of a partial run. No argparse and no printing; `smeltery.cli`
re-exports every name here (the same objects).
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING

from rdkit import Chem

from .calc_log import _tag, primary_model
from .config import StageError
from .gates import AtomOrderError, mol_for_poses
from .methods import active_hooks
from .model import Candidate
from .planning import memory_estimate
from .pocket.loader import file_digest
from .record import RunRecord, write_text_atomic
from .run_inputs import poses_key

if TYPE_CHECKING:
    from .planning import Plan

POSES_DIR = "poses"


def pose_file_name(candidate: str) -> str:
    """The SDF file name for a candidate: every character outside [A-Za-z0-9._-] becomes '_'."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", candidate) + ".sdf"


def _out_path(path: str | Path, out_dir: Path) -> str:
    """A path as the record stores it: relative to OUT when inside it, else absolute."""
    p = Path(path).resolve()
    try:
        return p.relative_to(out_dir.resolve()).as_posix()
    except ValueError:
        return str(p)


def _gates_in_out(gates: dict, out_dir: Path) -> dict:
    """A copy of the per-pose gate results with every `receptor` path made relative to OUT when inside it (the
    absolute path of a run directory that is later renamed or moved points at nothing)."""

    def walk(x):
        if isinstance(x, dict):
            return {
                k: _out_path(v, out_dir) if k == "receptor" and isinstance(v, str) and v else walk(v)
                for k, v in x.items()
            }
        if isinstance(x, list):
            return [walk(v) for v in x]
        return x

    return walk(gates)


def pose_mol(c: Candidate) -> Chem.Mol:
    """The candidate as an RDKit mol with one conformer per pose: atoms and bonds from its SMILES with explicit
    H (`Chem.AddHs(Chem.MolFromSmiles(smiles))`), coordinates from `Pose.coords_ang`. A pose whose elements are
    not that atom order is a StageError: writing coordinates onto the wrong atoms would be silently wrong."""
    try:
        return mol_for_poses(c.smiles, c.poses, set_3d=True)
    except AtomOrderError as e:
        raise StageError(
            f"stage 'outputs' failed: {c.name} pose {e.index} atoms {''.join(e.pose_symbols)} are not its "
            f"SMILES-with-H order {''.join(e.smiles_symbols)}; refusing to write coordinates onto the wrong atoms",
            stage="outputs",
        ) from None


def write_pose_sdfs(plan: Plan, results: dict, out_dir: Path) -> dict:
    """One SDF per candidate under OUT/poses/ (docs/gui-contract.md section 1); returns `results.poses`.

    Record i is pose i. Properties: smeltery_candidate, smeltery_pose_index, smeltery_parent ("1"/"0"), every
    `results.per_pose` quantity of the candidate and, on analogues, paired_ddE, all numbers as %.10g. A file-name
    collision after sanitising is a StageError, raised before anything is written.
    """
    names = {}
    for c in plan.candidates:
        f = pose_file_name(c.name)
        if f in names:
            raise StageError(
                f"stage 'outputs' failed: candidates {names[f]!r} and {c.name!r} both sanitise to pose file {f!r}; "
                "rename one",
                stage="outputs",
            )
        names[f] = c.name
    mols = {c.name: pose_mol(c) for c in plan.candidates}  # all checked before the first write
    pose_dir = out_dir / POSES_DIR
    pose_dir.mkdir(parents=True, exist_ok=True)
    poses = {}
    for c in plan.candidates:
        path = pose_dir / pose_file_name(c.name)
        values = results["per_pose"][c.name]
        paired = None
        if c is not plan.parent:
            paired_ddE = results.get("paired_ddE", {})
            if c.name in paired_ddE and isinstance(paired_ddE[c.name], dict):
                paired = paired_ddE[c.name].get("per_pose")
        mol = mols[c.name]
        index = (results.get("pose_index") or {}).get(c.name)  # original indices after a pairwise drop
        with Chem.SDWriter(str(path)) as w:
            for i, conf in enumerate(mol.GetConformers()):
                m = Chem.Mol(mol)
                m.RemoveAllConformers()
                m.AddConformer(Chem.Conformer(conf), assignId=True)
                m.SetProp("_Name", f"{c.name} pose {i}")
                m.SetProp("smeltery_candidate", c.name)
                m.SetProp("smeltery_pose_index", str(i if index is None else index[i]))
                m.SetProp("smeltery_parent", "1" if c is plan.parent else "0")
                for q, v in values.items():
                    m.SetProp(q, f"{v[i]:.10g}")
                if paired is not None:
                    m.SetProp("paired_ddE", f"{paired[i]:.10g}")
                w.write(m)
        poses[c.name] = {"file": f"{POSES_DIR}/{path.name}", "sha256": file_digest(path), "n_poses": len(c.poses)}
    return poses


def calc_file_name(candidate: str) -> str:
    """The calc log of a `--only-candidate` partial run: calcs-<candidate>.jsonl, sanitised like the pose SDFs."""
    return "calcs-" + pose_file_name(candidate)[: -len(".sdf")] + ".jsonl"


def partial_file_name(candidate: str) -> str:
    """The record of a `--only-candidate` partial run: partial-<candidate>.json, the name as given (unlike
    `calc_file_name`, not sanitised: `partial_candidate` reads the candidate back from it)."""
    return f"partial-{candidate}.json"


def partial_candidate(path: str | Path) -> str:
    """The candidate a partial record's file name names (`partial_file_name`, inverted)."""
    return Path(path).name[len("partial-") : -len(".json")]


def merged_partial_calcs(out_dir: Path, partials: list[Path]) -> list[dict]:
    """Every SCF line of the partial runs being combined, read from the calc logs their records name."""
    rows: list[dict] = []
    for p in partials:
        calcs = (json.loads(p.read_text()).get("results") or {}).get("calcs")
        f = Path(out_dir) / calcs["file"] if calcs else None
        if f is not None and f.is_file():
            rows += [json.loads(x) for x in f.read_text().splitlines() if x.strip()]
    return rows


def write_outputs(
    plan: Plan,
    rec: RunRecord,
    out_dir: Path,
    calc_file: str = "calcs.jsonl",
    calc_rows: list[dict] | None = None,
) -> Path:
    """Write everything a run leaves under OUT and return the record's path: the pose SDFs (`results.poses`), the
    receptor and decisions paths made relative to OUT when inside it, then OUT/run-<digest12>.json."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    results = rec.results
    results["poses"] = write_pose_sdfs(plan, results, out_dir)
    if results.get("receptor") is not None:
        results["receptor"]["file"] = _out_path(results["receptor"]["file"], out_dir)
    if "decisions_in" in results:
        results["decisions_in"]["file"] = _out_path(results["decisions_in"]["file"], out_dir)
    if plan.gate_results:  # only the gates that ran
        results["gates"] = _gates_in_out(plan.gate_results, out_dir)
    if calc_rows is not None and not calc_rows:
        results["calcs"] = None  # a combine of partials with no calc logs: no SCF count is known, never "0"
    else:
        base = list(calc_rows) if calc_rows is not None else list(getattr(plan.tier, "calc_log", None) or [])
        rows = write_calc_log(plan, out_dir, calc_file, base + _analysis_calcs(plan, results))
        results["calcs"] = {
            "file": calc_file,
            "n": rows,
            "primary": primary_model(plan.field),
            "by_model": _count_models(out_dir / calc_file),
        }
    path = out_dir / f"run-{rec.input_digest[:12]}.json"
    write_text_atomic(path, rec.to_json())
    return path


def _analysis_calcs(plan: Plan, results: dict) -> list[dict]:
    """Move every active analysis's per-model SCF lines (`results[key][model]["calcs"]`) out of the record, which
    then keeps `{"n": count}`: calcs.jsonl is the one per-SCF log."""
    rows: list[dict] = []
    for spec in active_hooks("analysis", plan.cfg):
        val = results.get(spec.key)
        if not isinstance(val, dict):
            continue
        for model, entry in val.items():
            if isinstance(entry, dict) and isinstance(entry.get("calcs"), list):
                rows += [_tag(r, str(model)) for r in entry["calcs"]]
                entry["calcs"] = {"n": len(entry["calcs"])}
    return rows


def _count_models(path: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for x in path.read_text().splitlines():
        if x.strip():
            r = json.loads(x)
            if "outcome" not in r:  # a --temporal vacuum leg has no model: it is shared by every model
                k = "shared_vacuum" if r.get("field_model") is None else str(r["field_model"])
                out[k] = out.get(k, 0) + 1
    return out


def write_calc_log(plan: Plan, out_dir: Path, name: str = "calcs.jsonl", rows: list[dict] | None = None) -> int:
    """Write OUT/<name> (one line per SCF: `rows`, else the ones the tier ran) and return how many lines it has.
    A line without a field model is the primary field's."""
    primary = primary_model(plan.field)
    rows = list(rows) if rows is not None else list(getattr(plan.tier, "calc_log", None) or [])
    calc_log = [_tag(r, primary) for r in rows]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # written whole, then renamed: a crash never leaves a half-written log
    write_text_atomic(out_dir / name, "".join(json.dumps(e) + "\n" for e in calc_log))
    return len(calc_log)


def write_plan_preview(plan: Plan, digest: str, out_dir: Path, config: Path) -> None:
    """`--plan --out OUT`: OUT/plan.json (`digest`, `seconds` = the measurement tier's `estimate_cost`, `memory`,
    `poses_key`, `poses`) and the pose SDFs under OUT/poses/. No SCF ran; Bench's impact reads these."""
    poses = write_pose_sdfs(plan, {"per_pose": {c.name: {} for c in plan.candidates}}, out_dir)
    raw = tomllib.loads(Path(config).read_text())  # the config as written: before decisions drop candidates
    rec = {
        "digest": digest,
        "seconds": plan.tier.estimate_cost(plan.candidates),
        "memory": memory_estimate(plan),
        "poses_key": poses_key(raw, Path(config).resolve().parent),
        "poses": poses,
    }
    rec = {"status": "ok", **rec}
    write_text_atomic(out_dir / "plan.json", json.dumps(rec, indent=2, sort_keys=True, default=str) + "\n")
