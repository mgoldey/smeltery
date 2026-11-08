"""Export a finished run as enriched pose SDFs: the run's own pose records plus the cut, gate and decision results.

The run-time pose SDFs (`cli.write_pose_sdfs`) are sha256-pinned artefacts and are never touched; this reads them
(through `gui.results.pose_sdf`, which checks the hash) and writes a new text. Decisions are made after the run, so they
can only live here. `smeltery export-sdf OUT [--out FILE]` and the Bench route `/api/runs/{idx}/export.sdf` use it."""

from __future__ import annotations

import argparse
import io
import re
import sys
from pathlib import Path

from rdkit import Chem

from .decisions import DecisionSet
from .gui import results as R

#: per-pose quantities of the run that get a stable gate alias (the originals stay on the record too)
_PER_POSE_GATES = {"dock_score": "vina_score", "E_gfn2": "xtb_energy"}


def _line(value: object) -> str:
    """A property value on one line: a newline would end the SDF value (a blank line, the whole property)."""
    return re.sub(r"\s*\n\s*", " ", str(value)).strip()


def _num(x: float) -> str:
    return f"{x:.10g}"


def decisions_path(run: R.Run) -> Path:
    return Path(run.out_dir) / f"decisions-{run.record['input_digest'][:12]}.json"


def load_decisions(run: R.Run) -> DecisionSet | None:
    p = decisions_path(run)
    return DecisionSet.load(p) if p.is_file() else None


def candidate_props(run: R.Run, candidate: str, decisions: DecisionSet | None = None) -> list[dict[str, str]]:
    """The properties to add to each pose record of `candidate` (record i -> dict i), in the order written."""
    res = run.record["results"]
    n = res["poses"][candidate]["n_poses"]
    common: dict[str, str] = {"smeltery_units": "kcal/mol (energies)"}
    paired = (res.get("paired_ddE") or {}).get(candidate)
    if isinstance(paired, dict):
        common["smeltery_ddE"] = _num(paired["mean"])
        common["smeltery_ddE_sem"] = _num(paired["sem"])
    for rank, g in enumerate(run.groups, 1):
        if candidate in g:
            common["smeltery_group"] = str(rank)
    if run.unranked:
        common["smeltery_unranked"] = "1"
    elif run.survivors is not None:
        common["smeltery_survivor"] = "1" if candidate in run.survivors else "0"
    d = decisions.get(candidate) if decisions is not None else None
    if d is not None:
        for k in ("status", "reason", "note", "user", "time"):
            common[f"smeltery_decision_{k}"] = _line(getattr(d, k))
    gates = res.get("gates") or {}
    per_pose = (res.get("per_pose") or {}).get(candidate) or {}
    pb = ((gates.get("posebusters") or {}).get(candidate) or {}).get("poses")
    strain = (gates.get("strain_kcal") or {}).get(candidate)
    rows = []
    for i in range(n):
        p = dict(common)
        for q, alias in _PER_POSE_GATES.items():
            if q in per_pose and i < len(per_pose[q]):
                p[f"smeltery_gate_{alias}"] = _num(per_pose[q][i])
        if strain is not None and i < len(strain):
            p["smeltery_gate_strain_kcal"] = _num(strain[i])
        if pb is not None and i < len(pb):
            p["smeltery_gate_posebusters_pass"] = "1" if pb[i]["passed"] else "0"
            p["smeltery_gate_posebusters_failed_checks"] = _line(", ".join(pb[i].get("failed_checks") or []))
        rows.append(p)
    return rows


def enriched_sdf(run: R.Run, decisions: DecisionSet | None = None) -> str:
    """All candidates' pose records, run order, each with the run's own properties plus `candidate_props`."""
    if not run.has_pose_output:
        raise ValueError(f"{run.campaign}: this run record predates pose output; it has no poses to export")
    out = []
    for name in run.candidates:
        if name not in run.record["results"]["poses"]:
            continue
        extra = candidate_props(run, name, decisions)
        sup = Chem.SDMolSupplier()
        sup.SetData(R.pose_sdf(run, name), removeHs=False, sanitize=False)
        mols = list(sup)
        if any(m is None for m in mols) or len(mols) != len(extra):
            raise ValueError(f"{name}: pose SDF does not hold the {len(extra)} poses the record lists")
        out.append((mols, extra))
    buf = io.StringIO()
    with Chem.SDWriter(buf) as w:
        for mols, extra in out:
            for m, props in zip(mols, extra, strict=True):
                for k, v in props.items():
                    m.SetProp(k, v)
                w.write(m)
    return buf.getvalue()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="smeltery export-sdf",
        description="Write a run's poses as one SDF with ddE, tie group, survivor, gate results and decisions "
        "(from OUT's decisions file, if any) as properties. The run's own pose files are not modified.",
    )
    ap.add_argument("out", help="the run's OUT directory (or its run-*.json)")
    ap.add_argument("--out", dest="dest", help="write here instead of stdout")
    args = ap.parse_args(argv)
    try:
        run = R.load_run(args.out)
        text = enriched_sdf(run, load_decisions(run))
    except (ValueError, KeyError) as e:
        print(f"smeltery export-sdf: {e}", file=sys.stderr)
        return 2
    if args.dest:
        Path(args.dest).write_text(text)
    else:
        sys.stdout.write(text)
    return 0
