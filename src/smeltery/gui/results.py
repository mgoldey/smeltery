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
    record: dict  # the parsed run-*.json, through `contract_record` (an imported record's derived values laid over)
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
        imported = run.record["results"].get("imported")
        if isinstance(imported, dict):
            raise ValueError(
                f"{run.campaign}: this run was imported from {imported.get('importer')}, and results.{key} "
                "was not recorded by the original run, so it cannot be shown"
            ) from None
        raise ValueError(
            f"{run.campaign}: the run record has no results.{key}; it predates pose output "
            "(docs/gui-contract.md section 1) and must be re-run with this smeltery to show it"
        ) from None


def _check_record(record: dict, where: Path) -> None:
    for key in ("campaign", "inputs", "results"):
        if key not in record:
            raise ValueError(f"{where}: not a smeltery run record (no {key!r})")
    for key in CONTRACT_INPUTS:
        if key not in record["inputs"]:
            raise ValueError(f"{where}: run record has no inputs.{key}")
    results = record["results"]
    for key in _RESULT_KEYS:
        if key not in results:
            raise ValueError(f"{where}: run record has no results.{key}")


#: `record.inputs` keys a viewer reads; `load_run` requires each.
CONTRACT_INPUTS = ("parent", "candidates", "cut", "floor")


def contract_record(record: dict) -> dict:
    """The record as the contract reads it. For an imported record (`results.imported`), each value an importer
    derived (`results.imported.derived["inputs.<key>"]` or `["results.<key>"]`, a dict with `value` and `from`) is
    laid over `inputs` or `results`; the record's own `inputs` stay the original's, which `input_digest` hashes. Any
    other record is returned unchanged (the same object)."""
    results = record.get("results")
    imported = results.get("imported") if isinstance(results, dict) else None
    derived = imported.get("derived") if isinstance(imported, dict) else None
    if not isinstance(derived, dict):
        return record
    out = {**record, "inputs": dict(record.get("inputs") or {}), "results": dict(results)}
    for key, d in derived.items():
        where, _, name = str(key).partition(".")
        if where in ("inputs", "results") and name and isinstance(d, dict) and "value" in d:
            out[where][name] = d["value"]
    return out


def load_run(path: str | Path) -> Run:
    """Read a run. `path` is a run-*.json, or an OUT directory holding exactly one; anything else is a
    ValueError that names what was found. An imported record is read through `contract_record`."""
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
    record = contract_record(record)
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


def pose_indices(run: Run, name: str, n: int) -> list[int]:
    """The original pose index of each of `name`'s `n` recorded poses (its per-pose values, its SDF records). After a
    pairwise pose drop (`smeltery run --temporal`) that is `results.pose_index[name]`; otherwise poses were never
    dropped and record `k` is pose `k`. ValueError when the record's list does not have `n` entries."""
    got = (run.record["results"].get("pose_index") or {}).get(name)
    if got is None:
        return list(range(n))
    if len(got) != n:
        raise ValueError(f"{name!r} has {n} recorded poses but results.pose_index lists {len(got)}")
    return [int(i) for i in got]


def paired_poses(run: Run, name: str, quantity: str = "dE_int") -> list[dict]:
    """One analogue's rows of `per_pose_table`. Analogue pose `i` pairs with parent pose `i` by ORIGINAL index:
    after a pairwise drop the two lists differ in length, and `results.pose_index` says which pose each value is."""
    per_pose = pose_output(run, "per_pose")
    present = sorted({q for qs in per_pose.values() for q in qs})
    for who in (run.parent, name):
        if quantity not in per_pose.get(who, {}):
            raise KeyError(f"{quantity!r} was not recorded for {who!r}; per-pose quantities present: {present}")
    parent, values = per_pose[run.parent][quantity], per_pose[name][quantity]
    if "pose_index" not in run.record["results"] and len(values) != len(parent):
        raise ValueError(f"{name!r} has {len(values)} poses of {quantity!r}, the parent {len(parent)}")
    at = {orig: k for k, orig in enumerate(pose_indices(run, run.parent, len(parent)))}
    rows = []
    for k, (orig, v) in enumerate(zip(pose_indices(run, name, len(values)), values, strict=True)):
        if orig not in at:
            raise ValueError(f"{name!r} pose {orig} was kept but the parent's pose {orig} was dropped")
        pv = parent[at[orig]]
        rows.append(
            {
                "candidate": name,
                "pose": orig,
                "position": k,
                "parent_position": at[orig],
                "value": v,
                "parent_value": pv,
                "paired": v - pv,
            }
        )
    return rows


def per_pose_table(run: Run, quantity: str = "dE_int") -> list[dict]:
    """One row per (analogue, pose) in cut order: candidate, pose (the ORIGINAL pose index, the pairing key), position
    (the pose's place in the candidate's per-pose values and SDF), parent_position (the same for the parent's pose),
    value, parent_value, paired (value - parent_value). Poses pair by original index (`pose_indices`): an analogue
    has no row for a pose dropped from it. KeyError naming the quantities present if `quantity` was not recorded."""
    return [row for name in _cut_order(run) for row in paired_poses(run, name, quantity)]


def dropped_poses(run: Run) -> list[dict]:
    """`results.dropped_poses`, unchanged: the poses a `--temporal` run dropped, one entry per failed leg. Empty when
    none was."""
    return [dict(d) for d in run.record["results"].get("dropped_poses") or []]


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
