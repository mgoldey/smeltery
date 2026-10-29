"""A finished run, read back for viewers: the RunRecord of `smeltery run --out OUT` and the files beside it.

The shapes are `docs/gui-contract.md` section 2. Every table is `list[dict]`, so a viewer in any toolkit (or a
JSON dump for one not written in Python) can show it. No GUI library is imported.

This module reports what the run decided; it never decides more. `resolved_matrix` calls the funnel's own
`resolved` predicate with the run's z and floor, on Measurements rebuilt from the record, so a viewer that shades
"resolved" pairs agrees with the cut by construction.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from smeltery.funnel import resolved
from smeltery.model import Measurement

#: `record.results` keys the viewers read. The first group is as old as the cut and `load_run` requires it; the second
#: came with pose output, so a record that predates it still loads and only the views that need it refuse.
_RESULT_KEYS = ("paired_ddE", "unpaired_sem", "cut", "funnel")
_POSE_KEYS = ("per_pose", "units", "poses", "field", "receptor")


@dataclass
class Run:
    record: dict  # the parsed run-*.json
    out_dir: Path  # where `poses/...` paths resolve

    @property
    def campaign(self) -> str:
        return self.record["campaign"]

    @property
    def parent(self) -> str:
        return self.record["inputs"]["parent"]

    @property
    def candidates(self) -> dict[str, str]:
        """Name -> SMILES, parent included, in config order (`results.candidate_order`). The record is written
        with sort_keys, so a record without that list gives name order; rank order always comes from `groups`."""
        smiles = self.record["inputs"]["candidates"]
        order = self.record["results"].get("candidate_order") or sorted(smiles)
        return {n: smiles[n] for n in order}

    @property
    def analogues(self) -> list[str]:
        return [n for n in self.candidates if n != self.parent]

    @property
    def floor(self) -> float:
        return float(self.record["inputs"]["floor"]["value"])

    @property
    def floor_source(self) -> str:
        return self.record["inputs"]["floor"]["source"]

    @property
    def z(self) -> float:
        return float(self.record["inputs"]["cut"].get("z", 2.0))

    @property
    def keep(self) -> int:
        return int(self.record["inputs"]["cut"]["keep"])

    @property
    def groups(self) -> list[list[str]]:
        """Tie groups from the cut, best (lowest ddE) first. Within a group there is no order."""
        return [list(g) for g in self.record["results"]["cut"]["groups"]]

    @property
    def survivors(self) -> list[str] | None:
        s = self.record["results"]["cut"]["survivors"]
        return None if s is None else list(s)

    @property
    def unranked(self) -> bool:
        return bool(self.record["results"]["cut"]["unranked"])

    @property
    def has_pose_output(self) -> bool:
        """False for a record that predates pose output: its ranking can be shown, its poses cannot."""
        return all(k in self.record["results"] for k in _POSE_KEYS)


def pose_output(run: Run, key: str):
    """`record.results[key]` for a pose-output key, or a ValueError saying the record predates pose output."""
    try:
        return run.record["results"][key]
    except KeyError:
        raise ValueError(
            f"{run.campaign}: the run record has no results.{key}; it predates pose output "
            "(docs/gui-contract.md section 1) and must be re-run with this smeltery to show it"
        ) from None


def _check_record(record: dict, where: Path) -> None:
    for key in ("campaign", "inputs", "results"):
        if key not in record:
            raise ValueError(f"{where}: not a smeltery run record (no {key!r})")
    for key in ("parent", "candidates", "cut", "floor"):
        if key not in record["inputs"]:
            raise ValueError(f"{where}: run record has no inputs.{key}")
    results = record["results"]
    for key in _RESULT_KEYS:
        if key not in results:
            raise ValueError(f"{where}: run record has no results.{key}")


def load_run(path: str | Path) -> Run:
    """Read a run. `path` is a run-*.json, or an OUT directory holding exactly one; anything else is a
    ValueError that names what was found."""
    p = Path(path)
    if p.is_dir():
        found = sorted(p.glob("run-*.json"))
        if len(found) != 1:
            names = [f.name for f in found] or "nothing"
            raise ValueError(f"{p}: need exactly one run-*.json, found {names}; pass the one you mean")
        p = found[0]
    elif not p.is_file():
        raise ValueError(f"{p}: no such run record or OUT directory")
    try:
        record = json.loads(p.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{p}: cannot read run record: {e}") from e
    if not isinstance(record, dict):
        raise ValueError(f"{p}: not a smeltery run record (not a JSON object)")
    _check_record(record, p)
    return Run(record, p.parent)


def _measurement(run: Run, name: str) -> Measurement:
    r = run.record["results"]["paired_ddE"][name]
    return Measurement(float(r["mean"]), float(r["sem"]), int(r["n"]), tuple(float(v) for v in r["per_pose"]))


def _cut_order(run: Run) -> list[str]:
    return [n for g in run.groups for n in g]


def ddE_table(run: Run) -> list[dict]:
    """One row per analogue, in cut order (groups flattened): candidate, smiles, mean, sem, n, lo, hi
    (mean -/+ z*sem), unpaired_sem, group (index into run.groups), survivor (None when the cut was unranked)."""
    smiles = run.candidates
    unpaired = run.record["results"]["unpaired_sem"]
    survivors = run.survivors
    rows = []
    for gi, group in enumerate(run.groups):
        for name in group:
            m = _measurement(run, name)
            rows.append(
                {
                    "candidate": name,
                    "smiles": smiles[name],
                    "mean": m.mean,
                    "sem": m.sem,
                    "n": m.n,
                    "lo": m.mean - run.z * m.sem,
                    "hi": m.mean + run.z * m.sem,
                    "unpaired_sem": float(unpaired[name]) if name in unpaired else None,
                    "group": gi,
                    "survivor": None if run.unranked or survivors is None else name in survivors,
                }
            )
    return rows


def resolved_matrix(run: Run) -> tuple[list[str], list[list[bool]]]:
    """Names in ddE_table order, and [i][j] = smeltery.funnel.resolved(m_i, m_j, run.z, run.floor), False on the
    diagonal. The funnel's own predicate: the GUI never decides more than the cut did."""
    names = _cut_order(run)
    ms = [_measurement(run, n) for n in names]
    matrix = [[i != j and bool(resolved(a, b, run.z, run.floor)) for j, b in enumerate(ms)] for i, a in enumerate(ms)]
    return names, matrix


def per_pose_table(run: Run, quantity: str = "dE_int") -> list[dict]:
    """One row per (analogue, pose) in cut order: candidate, pose, value, parent_value, paired (value -
    parent_value). Poses pair by index. KeyError naming the quantities present if `quantity` was not recorded."""
    per_pose = pose_output(run, "per_pose")
    present = sorted({q for qs in per_pose.values() for q in qs})
    if quantity not in per_pose.get(run.parent, {}):
        raise KeyError(f"{quantity!r} was not recorded for the parent; per-pose quantities present: {present}")
    parent = per_pose[run.parent][quantity]
    rows = []
    for name in _cut_order(run):
        if quantity not in per_pose.get(name, {}):
            raise KeyError(f"{quantity!r} was not recorded for {name!r}; per-pose quantities present: {present}")
        values = per_pose[name][quantity]
        if len(values) != len(parent):
            raise ValueError(f"{name!r} has {len(values)} poses of {quantity!r}, the parent {len(parent)}")
        for i, (v, pv) in enumerate(zip(values, parent, strict=True)):
            rows.append({"candidate": name, "pose": i, "value": v, "parent_value": pv, "paired": v - pv})
    return rows


def funnel_rows(run: Run) -> list[dict]:
    """`results.funnel`, unchanged: one row per stage with candidates in and out."""
    return [dict(r) for r in run.record["results"]["funnel"]]


def _resolve(run: Run, file: str) -> Path:
    p = Path(file)
    return p if p.is_absolute() else run.out_dir / p


def _verified_bytes(run: Run, entry: dict, what: str) -> bytes:
    path = _resolve(run, entry["file"])
    try:
        data = path.read_bytes()
    except OSError as e:
        raise ValueError(f"{what}: cannot read {path}: {e}") from e
    got = hashlib.sha256(data).hexdigest()
    if got != entry["sha256"]:
        raise ValueError(f"{what}: {path} has sha256 {got}, the run recorded {entry['sha256']}; it was changed")
    return data


def pose_sdf(run: Run, candidate: str) -> str:
    """The candidate's pose SDF text (record i is pose i), after checking its sha256 against the record.
    ValueError naming the file if it is missing or changed; KeyError for a candidate with no poses."""
    poses = pose_output(run, "poses")
    if candidate not in poses:
        raise KeyError(f"no poses recorded for {candidate!r}; candidates with poses: {sorted(poses)}")
    return _verified_bytes(run, poses[candidate], f"poses of {candidate!r}").decode()


def receptor_pdb(run: Run) -> str | None:
    """The receptor PDB text, sha256-checked like `pose_sdf`, or None when the run has no receptor."""
    entry = pose_output(run, "receptor")
    if entry is None:
        return None
    return _verified_bytes(run, entry, "receptor").decode()
