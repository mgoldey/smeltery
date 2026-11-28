"""Export finished runs as a columnar dataset (Parquet or Arrow IPC): one row per (ligand, target, tier).

`smeltery export parquet RUN... -o out.parquet` (or `arrow`) reads each run's `run-<digest12>.json` and writes the
rows below. Nothing is recomputed that the record already states: a score is the mean of the record's own per-pose
values (`Measurement.from_samples`, the funnel's estimator), the paired ddE and the decision come straight from
`results.paired_ddE` and `results.cut`, and provenance columns are copied from the record.

* **Grain.** A row exists for each candidate (parent included) and each tier that declares a per-pose quantity the
  candidate carries. A tier that writes only poses (`paired_poses`) has no score and so no row.
* **Units and floors come from the tiers' own declarations** (`Tier.produces()` and `Tier.systematic_floor()`),
  found by the recorded tier name in `tier_registry` and rebuilt from the recorded settings; no tier name is
  hard-coded here. A tier that cannot be rebuilt from its record (it needs a provider object) falls back to the
  class's `QUANTITY` and the record's own `results.units`, with the floor reported as unresolved, never as zero.
  A quantity declared by two tiers belongs to the later one, as in `planning.per_pose_results`.
* **Two floors, kept apart.** `score_floor` is what the TIER declares for `score_quantity` (null while unmeasured,
  and `score_floor_source` says which kind of null). `paired_ddE_floor` is the floor the CUT used, from
  `inputs.floor`; with an unmeasured tier floor that is the config's explicit value, and `paired_ddE_floor_source`
  says so.
* **Decision** is the candidate's outcome in the funnel, repeated on each of its rows: `pass` (a cut survivor),
  `drop`, `reference` (the parent is never cut) or `undecided` (the cut was unranked at the boundary). A decisions
  file beside the record (`decisions-<digest12>.json`) adds `user_decision`, null when there is none.
* **SMILES** are RDKit-canonical when RDKit imports and parses them, else as recorded; `smiles_form` says which, per
  row, and the file's metadata lists the forms present.
* **Provenance** columns: `run_id` and `family`, the run's identity as its CORE record (`OUT/attestation/run.json`,
  `smeltery.core_record`) states it -- a job run carries the submitter's run_id there, not `input_digest[:12]` -- and
  `core_record_sha256`, the sha256 of that run.json's bytes (what `attestation_schema.record_digest` returns and a
  dataset record's `source_runs[].record_sha256` names). A run with no core record has `core_record_sha256` null and
  falls back to what a core record would have said for a run given no name: `run_id` = the first 12 hex of
  `input_digest` (the digest in the legacy record's file name; that value stays derivable from `input_digest` and
  `record_file` for every row), `family` = the campaign. Then `input_digest`, `record_sha256` (the LEGACY record's
  self-hash, null in records written before it existed; not re-verified here: `smeltery verify-run` does that; no core
  reader resolves it), smeltery and ferric versions, ferric's build commit and dirty flag, and
  `ferric_provenance_level` (VERIFIED / INFERRED / UNKNOWN, the first word of the record's provenance label).
* **Ferric per row (`schema_version` 3).** A run whose SCF rows came from ONE ferric build (every run but a
  `--temporal` run finalized with `allow_mixed_builds`) states it in the record's `ferric` block, and its rows copy
  that (`ferric_build_basis` = `run_record`). A run whose `results.builds` lists more than one ferric build has no
  one ferric, so each row states the build of ITS OWN SCFs, read from the `build` stamp of the candidate's rows in
  the run's `calcs.jsonl` (`results.builds` itself carries only counts): one build -> its `git_sha` and `dirty`,
  VERIFIED, basis `scf_rows`; several -> sha and dirty null, UNKNOWN, basis `mixed_scf_rows`; a row with no stamp, or
  no calcs.jsonl beside the record -> null, UNKNOWN, basis `unrecorded`. Never the run-level stamp (it names the
  process that finalized the run, not the one that measured), and no `ferric_version` (a build stamp has none).

File-level key-value metadata (`smeltery.*`): `schema_version`, `units` (JSON quantity -> unit, a list when runs
disagree), `smiles_form`, `runs`, `grain`, `generator`, and `generation` only when a run had a generation step
(the generator's counts per closed rejection reason; docs/generators.md). pyarrow is the optional `dataset` extra;
`export_rows` needs nothing beyond the core install.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import re
import sys
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path

from . import run_view as R
from .decisions import DecisionSet
from .model import Measurement
from .record import digest, file_sha256

#: Bump when a column is added, renamed, removed or changes type. Stored in the file's metadata.
#: 2: `family` and `core_record_sha256` added; `run_id` is the core record's run_id (was always `input_digest[:12]`).
#: 3: the `ferric_*` columns state the build of THIS ROW's SCFs, not one build per run: `ferric_git_sha` can be null
#:    (build not recorded, or the row's SCFs came from several builds) and `ferric_build_basis` is added; a run whose
#:    rows all came from one build is unchanged apart from that column.
SCHEMA_VERSION = 3

#: `ferric_build_basis`: what a row's ferric columns were read from.
BASIS_RUN = "run_record"  # the run has one ferric build; the record's `ferric` block states it
BASIS_SCF = "scf_rows"  # a mixed-build run: every SCF row of this candidate carries one and the same build stamp
BASIS_MIXED = "mixed_scf_rows"  # a mixed-build run: this candidate's own SCF rows came from more than one build
BASIS_UNRECORDED = "unrecorded"  # a mixed-build run: a row of this candidate carries no build (or no calcs.jsonl)

#: Where a run directory keeps its core record (`attestation_schema.layout.RUN_JSON`), spelled here so the row builder
#: needs no attestation-schema: it reads the file as JSON and hashes its bytes, nothing more.
CORE_RECORD = Path("attestation") / "run.json"

#: The per-pose quantity the funnel cuts on (`smeltery.run_view.QUANTITY`).
CUT_QUANTITY = R.QUANTITY

GRAIN = "one row per (ligand, target, tier)"
FORMATS = ("parquet", "arrow")
_PROVENANCE_LEVEL = re.compile(r"^(VERIFIED|INFERRED|UNKNOWN)\b")
_NO_TIER_FLOOR = "tier declares no measured floor (systematic_floor() is None)"
_UNRESOLVED_FLOOR = "tier could not be rebuilt from the record: floor not resolved"
_UNATTRIBUTED = "unattributed"


class ExportError(ValueError):
    """The runs cannot be exported (unreadable record, the same run twice, pyarrow missing)."""


@dataclass(frozen=True)
class Declared:
    """One quantity a tier declares: its unit and its systematic floor (None while unmeasured)."""

    quantity: str
    unit: str
    floor: float | None
    floor_source: str


def tier_declarations(settings: dict, units: dict[str, str]) -> list[Declared]:
    """What the tier recorded as `settings` declares, in its own order, through the tier protocol.

    The class is found by the recorded name; it is built from the recorded settings that match its constructor,
    then bare. A tier no constructor call can build reports only its class's `QUANTITY` (if any), in the unit the
    record states, with its floor unresolved."""
    name = settings.get("name")
    try:
        from .tier_registry import registered_tiers

        cls = registered_tiers().get(name) if isinstance(name, str) else None
    except Exception:  # an optional backend a listed module needs is missing: the tier cannot be read, not the run
        cls = None
    if cls is None:
        return []
    inst, produced = None, {}
    try:
        params = set(inspect.signature(cls).parameters) - {"name"}
        attempts = [{k: v for k, v in settings.items() if k in params}, {}]
    except (TypeError, ValueError):
        attempts = [{}]
    for kwargs in attempts:
        try:
            inst = cls(**kwargs)
            produced = dict(inst.produces())
            break
        except Exception:  # needs inputs the record cannot supply (a provider object): try the next form
            inst = None
    if inst is None:
        q = getattr(cls, "QUANTITY", None)
        if isinstance(q, str) and q in units:
            return [Declared(q, units[q], None, _UNRESOLVED_FLOOR)]
        return []
    out = []
    for q, unit in produced.items():
        try:
            floor = inst.systematic_floor(q)
        except (KeyError, ValueError):
            floor = None
        out.append(Declared(q, unit, floor, f"tier {name} systematic_floor" if floor is not None else _NO_TIER_FLOOR))
    return out


def _canonical(smiles: str, canonicalize: bool) -> tuple[str, str]:
    """(smiles, form): RDKit's canonical SMILES when asked for and RDKit parses it, else the recorded text."""
    if canonicalize:
        try:
            from rdkit import Chem
        except ImportError:
            return smiles, "as-recorded"
        mol = Chem.MolFromSmiles(smiles)
        if mol is not None:
            return Chem.MolToSmiles(mol), "rdkit-canonical"
    return smiles, "as-recorded"


def _target(inputs: dict) -> tuple[str | None, str, str | None]:
    """(identifier, kind, sha256) of what the run was measured against, from what the record states.

    The structure the user named, else the pocket file, else the docking receptor; a run with none of them was
    measured in a bare point-charge field, identified by the digest of that field."""
    st = inputs.get("structure")
    if isinstance(st, dict) and st.get("spec"):
        return str(st["spec"]), "structure", (st.get("structure") or {}).get("sha256")
    pocket = inputs.get("pocket")
    if isinstance(pocket, dict) and pocket.get("input"):
        return str(pocket["input"]), "pocket", pocket.get("input_sha256")
    dock = inputs.get("docking")
    if isinstance(dock, dict) and dock.get("receptor"):
        return str(dock["receptor"]), "receptor", dock.get("receptor_sha256")
    field = inputs.get("field")
    if field is not None:
        sha = digest(field)
        return f"field:{sha[:12]}", "field", sha
    return None, "none", None


def _decisions(run: R.Run) -> dict[str, tuple[str, str]]:
    """{candidate: (decision, reason)} for every candidate of the run, from the cut and the recorded gates."""
    res = run.record["results"]
    survivors = run.survivors
    in_cut = {n for g in run.groups for n in g}
    pb = (res.get("gates") or {}).get("posebusters") or {}
    out = {}
    for name in run.candidates:
        if name == run.parent:
            out[name] = ("reference", "the parent is the reference; it is not cut")
        elif name not in in_cut:
            failed = (pb.get(name) or {}).get("all_passed") is False
            out[name] = (
                "drop",
                "failed the PoseBusters gate" if failed else "removed before the cut (see results.funnel)",
            )
        elif survivors is None:
            out[name] = ("undecided", "the cut was unranked at the boundary: no keep decision")
        elif name in survivors:
            out[name] = ("pass", f"cut survivor (keep={run.keep})")
        else:
            out[name] = ("drop", f"cut: ranked outside keep={run.keep}")
    return out


def _ferric_columns(ferric: dict, basis: str) -> dict:
    """The six `ferric_*` columns for the identity block `ferric` (the record's own, or a row-level one built by
    `_per_row_ferric`), and the `ferric_build_basis` that says which."""
    return {
        "ferric_version": ferric.get("version"),
        "ferric_git_sha": ferric.get("commit"),
        "ferric_dirty": ferric.get("dirty"),
        "ferric_provenance_level": _level(ferric.get("provenance")),
        "ferric_provenance": ferric.get("provenance"),
        "ferric_source": ferric.get("source"),
        "ferric_build_basis": basis,
    }


def _is_mixed(rec: dict) -> bool:
    """`results.builds` names more than one distinct ferric build (the rule `core_record._ferric` states ferric
    `unknown` by); rows with no build recorded do not count as one."""
    builds = [b["ferric"] for b in (rec["results"].get("builds") or []) if isinstance(b.get("ferric"), dict)]
    return len({json.dumps(b, sort_keys=True) for b in builds}) > 1


_NOT_STATED = {"version": None, "source": None}


def _per_row_ferric(record_path: Path, rec: dict, parent: str):
    """For a mixed-build run: candidate name -> (ferric identity block, basis), from the `build` stamps of the
    candidate's SCF rows in the run's calcs.jsonl (beside the record). `results.builds` has counts, not row ids, so
    this is the only place a row's build is recorded; a candidate with no rows, or any row without a stamp, is
    `unrecorded`: never the run-level `ferric` block."""
    calcs = (rec["results"].get("calcs") or {}).get("file") or "calcs.jsonl"
    path = record_path.parent / calcs
    digest = str(rec["input_digest"])[:12]
    stamps: dict[str, list[dict | None]] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue  # a torn line: finalize skips it too
            if not isinstance(r, dict) or "outcome" in r or r.get("digest", digest) != digest or "candidate" not in r:
                continue
            who = parent if r["candidate"] == "parent" else r["candidate"]
            b = r.get("build")
            stamps.setdefault(who, []).append(b.get("ferric") if isinstance(b, dict) else None)

    def of(name: str):
        got = stamps.get(name, [])
        if not got or any(not isinstance(g, dict) for g in got):
            if not got:
                return {
                    **_NOT_STATED,
                    "provenance": f"UNKNOWN: no SCF row of this candidate in {calcs}",
                }, BASIS_UNRECORDED
            named = sorted({str(g.get("git_sha") or "unstamped")[:12] for g in got if isinstance(g, dict)})
            n = sum(1 for g in got if not isinstance(g, dict))
            seen = f"; the other {len(got) - n} name {', '.join(named)}" if named else ""
            return {
                **_NOT_STATED,
                "provenance": f"UNKNOWN: {n} of {len(got)} SCF rows of this candidate carry no build stamp "
                f"in {calcs}{seen}",
            }, BASIS_UNRECORDED
        distinct = {json.dumps(g, sort_keys=True): g for g in got}
        if len(distinct) > 1:
            shas = ", ".join(sorted(str(g.get("git_sha") or "unstamped")[:12] for g in distinct.values()))
            return {
                **_NOT_STATED,
                "provenance": f"UNKNOWN: this candidate's SCF rows came from {len(distinct)} ferric builds ({shas})",
            }, BASIS_MIXED
        (g,) = distinct.values()
        if not g.get("git_sha"):
            return {
                **_NOT_STATED,
                "dirty": g.get("dirty"),
                "provenance": f"UNKNOWN: the build stamp in {calcs} names no git sha",
                "source": calcs,
            }, BASIS_SCF
        return {
            "version": None,
            "commit": g["git_sha"],
            "dirty": g.get("dirty"),
            "provenance": f"VERIFIED from the build stamp of every SCF row in {calcs} (ferric.__build__)",
            "source": calcs,
        }, BASIS_SCF

    return of


def _level(provenance: str | None) -> str:
    m = _PROVENANCE_LEVEL.match(provenance or "")
    return m.group(1) if m else "UNKNOWN"


def _num(x) -> float | None:
    """A finite float or None (a single-sample SEM is NaN in memory and null on disk)."""
    return None if x is None or x != x else float(x)


def _entry(quantity, unit, value, sem, n, floor, floor_source, kind) -> dict:
    return {
        "quantity": quantity,
        "unit": unit,
        "value": _num(value),
        "sem": _num(sem),
        "n": int(n),
        "systematic_floor": _num(floor),
        "floor_source": floor_source,
        "kind": kind,
    }


@dataclass(frozen=True)
class CoreRef:
    """A legacy record's core record: where it is, the sha256 of its bytes, and the identity it states."""

    path: Path
    sha256: str
    run_id: str
    family: str | None


def core_record_ref(legacy_path: str | Path) -> CoreRef | None:
    """The core record beside the legacy record at `legacy_path`, or None when there is none for it.

    A core record belongs to a legacy record when its `data[]` lists that file by name with role `output` (what
    `core_record.write_core` writes). A core record that lists the file with ANOTHER sha256 is stale -- the legacy
    record changed after it was written -- and is refused (`ExportError`), never silently paired. Standard library
    only: the strict read is the dataset writer's (`dataset_record`)."""
    legacy_path = Path(legacy_path)
    core = legacy_path.parent / CORE_RECORD
    if not core.is_file():
        return None
    raw = core.read_bytes()
    try:
        obj = json.loads(raw)
    except ValueError as e:
        raise ExportError(f"{core}: not JSON: {e}") from e
    schema = obj.get("schema") if isinstance(obj, dict) else None
    if not isinstance(obj, dict) or not str(schema).startswith("attestation.run/"):
        raise ExportError(f"{core}: not a core run record (schema {schema!r})")
    mine = [d for d in obj.get("data") or [] if d.get("name") == legacy_path.name and d.get("role") == "output"]
    if not mine:
        return None  # the core record of another legacy record in the same directory
    legacy_sha = file_sha256(legacy_path)
    if mine[0].get("sha256") != legacy_sha:
        raise ExportError(
            f"{core} lists {legacy_path.name} with sha256 {mine[0].get('sha256')}, but the file's is {legacy_sha}: "
            "the legacy record changed after its core record was written (`smeltery record core --force` rewrites it)"
        )
    return CoreRef(core, hashlib.sha256(raw).hexdigest(), str(obj["run_id"]), obj.get("family"))


def run_rows(path: str | Path, *, canonicalize: bool = True) -> list[dict]:
    """The rows of one run (`run-*.json`, or an OUT directory holding exactly one), in run order."""
    p = Path(path)
    run = R.load_run(p)
    if p.is_dir():
        p = next(iter(sorted(p.glob("run-*.json"))))
    rec = run.record
    res = rec["results"]
    units = dict(res.get("units") or {})
    per_pose = res.get("per_pose") or {}
    if not per_pose:
        raise ExportError(f"{p}: the record has no results.per_pose (it predates pose output); nothing to export")
    paired = res.get("paired_ddE") or {}
    inputs = rec["inputs"]
    target, target_kind, target_sha = _target(inputs)
    decisions = _decisions(run)
    user = _user_decisions(run)
    ferric, smeltery = rec.get("ferric") or {}, rec.get("smeltery") or {}
    row_builds = _per_row_ferric(p, rec, run.parent) if _is_mixed(rec) else None
    cut_floor = inputs["floor"]

    declared: dict[str, tuple[str, Declared]] = {}  # quantity -> (tier name, declaration); a later tier wins
    order: list[tuple[str, dict]] = []
    for t in rec.get("tiers") or []:
        tname = str(t.get("name"))
        order.append((tname, t))
        for d in tier_declarations(t, units):
            declared[d.quantity] = (tname, d)
    leftover = [q for q in units if q not in declared]
    if leftover:
        order.append((_UNATTRIBUTED, {"name": _UNATTRIBUTED}))
        for q in leftover:
            declared[q] = (_UNATTRIBUTED, Declared(q, units[q], None, "no tier of the record declares this quantity"))

    core = core_record_ref(p)
    base = {
        "run_id": core.run_id if core else str(rec["input_digest"])[:12],
        "input_digest": rec["input_digest"],
        "record_sha256": rec.get("record_sha256"),
        "core_record_sha256": core.sha256 if core else None,
        "record_file": p.name,
        "campaign": rec["campaign"],
        "family": core.family if core else rec["campaign"],
        "started_utc": rec.get("started_utc"),
        "target": target,
        "target_kind": target_kind,
        "target_sha256": target_sha,
        "smeltery_version": rec.get("smeltery_version"),
        "smeltery_commit": smeltery.get("commit"),
        "smeltery_dirty": smeltery.get("dirty"),
        **_ferric_columns(ferric, BASIS_RUN),
    }
    generated = next((r for r in res.get("funnel") or [] if r.get("kind") == "generate"), None)
    if generated is not None:  # only a run with a generation step: other runs' rows keep their exact keys
        base["generation"] = {k: generated[k] for k in GENERATION_SUMMARY_KEYS if k in generated}
    rows = []
    for name, recorded in run.candidates.items():
        values = per_pose.get(name)
        if not values:
            continue
        smiles, form = _canonical(recorded, canonicalize)
        ferric_cols = _ferric_columns(*row_builds(name)) if row_builds else {}
        dec, why = decisions[name]
        u_dec, u_why = user.get(name, (None, None))
        for tier_name, settings in order:
            mine = [(q, d) for q, (t, d) in declared.items() if t == tier_name and q in values]
            if not mine:
                continue
            scores = []
            for q, d in mine:
                m = Measurement.from_samples(values[q], d.unit)
                scores.append(_entry(q, d.unit, m.mean, m.sem, m.n, d.floor, d.floor_source, "per_pose_mean"))
            main = next((s for s in scores if s["quantity"] == CUT_QUANTITY), scores[0])
            flat = {
                "score_quantity": main["quantity"],
                "score": main["value"],
                "score_unit": main["unit"],
                "score_sem": main["sem"],
                "score_n": main["n"],
                "score_floor": main["systematic_floor"],
                "score_floor_source": main["floor_source"],
                "paired_ddE": None,
                "paired_ddE_sem": None,
                "paired_ddE_n": None,
                "paired_ddE_floor": None,
                "paired_ddE_floor_source": None,
            }
            pd = paired.get(name)
            if main["quantity"] == CUT_QUANTITY and isinstance(pd, dict):
                flat.update(
                    paired_ddE=_num(pd["mean"]),
                    paired_ddE_sem=_num(pd["sem"]),
                    paired_ddE_n=int(pd["n"]),
                    paired_ddE_floor=_num(cut_floor["value"]),
                    paired_ddE_floor_source=cut_floor["source"],
                )
                scores.append(
                    _entry(
                        "ddE",
                        main["unit"],
                        pd["mean"],
                        pd["sem"],
                        pd["n"],
                        cut_floor["value"],
                        cut_floor["source"],
                        "paired_vs_parent",
                    )
                )
            rows.append(
                {
                    **base,
                    **ferric_cols,
                    "ligand": name,
                    "is_parent": name == run.parent,
                    "smiles": smiles,
                    "smiles_form": form,
                    "smiles_recorded": recorded,
                    "tier": tier_name,
                    "tier_settings": json.dumps(settings, sort_keys=True, default=str),
                    **flat,
                    "scores": scores,
                    "decision": dec,
                    "decision_reason": why,
                    "user_decision": u_dec,
                    "user_decision_reason": u_why,
                }
            )
    return rows


def _user_decisions(run: R.Run) -> dict[str, tuple[str, str]]:
    from .sdf_export import load_decisions

    ds: DecisionSet | None = load_decisions(run)
    if ds is None:
        return {}
    return {d.candidate: (d.status, d.reason) for d in ds.decisions}


def find_records(path: str | Path) -> list[Path]:
    """The `run-*.json` files `path` names: itself, or every one in a directory (sorted)."""
    p = Path(path)
    if p.is_dir():
        found = sorted(p.glob("run-*.json"))
        if not found:
            raise ExportError(f"{p}: no run-*.json in this directory")
        return found
    if p.is_file():
        return [p]
    raise ExportError(f"{p}: no such run record or OUT directory")


def export_rows(paths: list[str | Path], *, canonicalize: bool = True) -> list[dict]:
    """Rows of every run under `paths`, in the order given. The same run (input digest) twice is refused."""
    seen: dict[str, Path] = {}
    rows: list[dict] = []
    for arg in paths:
        for rec in find_records(arg):
            try:
                got = run_rows(rec, canonicalize=canonicalize)
            except (ValueError, KeyError, OSError) as e:
                raise ExportError(f"{rec}: {e}") from e
            if not got:
                continue
            rid = got[0]["input_digest"]
            if rid in seen:
                raise ExportError(f"{rec}: the same run as {seen[rid]} (input digest {rid[:12]}); export it once")
            seen[rid] = rec
            rows.extend(got)
    return rows


def _pyarrow():
    try:
        import pyarrow as pa
    except ImportError as e:
        raise ExportError(
            "writing a dataset needs pyarrow, the optional `dataset` extra: pip install 'smeltery[dataset]'"
        ) from e
    return pa


def arrow_schema(metadata_: dict[str, str] | None = None):
    """The one schema every export has, whatever the runs: it is built here, never inferred from the data."""
    pa = _pyarrow()
    s, f, i, b = pa.string(), pa.float64(), pa.int32(), pa.bool_()
    score = pa.struct(
        [
            ("quantity", s),
            ("unit", s),
            ("value", f),
            ("sem", f),
            ("n", i),
            ("systematic_floor", f),
            ("floor_source", s),
            ("kind", s),
        ]
    )
    fields = [
        ("run_id", s),
        ("input_digest", s),
        ("record_sha256", s),
        ("core_record_sha256", s),
        ("record_file", s),
        ("campaign", s),
        ("family", s),
        ("started_utc", s),
        ("ligand", s),
        ("is_parent", b),
        ("smiles", s),
        ("smiles_form", s),
        ("smiles_recorded", s),
        ("target", s),
        ("target_kind", s),
        ("target_sha256", s),
        ("tier", s),
        ("tier_settings", s),
        ("score_quantity", s),
        ("score", f),
        ("score_unit", s),
        ("score_sem", f),
        ("score_n", i),
        ("score_floor", f),
        ("score_floor_source", s),
        ("paired_ddE", f),
        ("paired_ddE_sem", f),
        ("paired_ddE_n", i),
        ("paired_ddE_floor", f),
        ("paired_ddE_floor_source", s),
        ("scores", pa.list_(score)),
        ("decision", s),
        ("decision_reason", s),
        ("user_decision", s),
        ("user_decision_reason", s),
        ("smeltery_version", s),
        ("smeltery_commit", s),
        ("smeltery_dirty", b),
        ("ferric_version", s),
        ("ferric_git_sha", s),
        ("ferric_dirty", b),
        ("ferric_provenance_level", s),
        ("ferric_provenance", s),
        ("ferric_source", s),
        ("ferric_build_basis", s),
    ]
    meta = {k.encode(): v.encode() for k, v in (metadata_ or {}).items()}
    return pa.schema([pa.field(n, t) for n, t in fields], metadata=meta)


#: The generate row's counts a dataset carries (not the rejected SMILES: those stay in the run record).
GENERATION_SUMMARY_KEYS = ("generator", "candidates_in", "candidates_out", "rejected_by_reason", "rejected_total")


def file_metadata(rows: list[dict]) -> dict[str, str]:
    """The `smeltery.*` key-value metadata for `rows`. `smeltery.generation` (run_id -> the generator's raw count,
    returned count and rejections per closed reason) is present only when some run had a generation step."""
    units: dict[str, set[str]] = {}
    for r in rows:
        for s in r["scores"]:
            units.setdefault(s["quantity"], set()).add(s["unit"])
    try:
        version = metadata.version("smeltery")
    except metadata.PackageNotFoundError:
        version = "unknown"
    generation = {r["run_id"]: r["generation"] for r in reversed(rows) if "generation" in r}
    return {
        "smeltery.schema_version": str(SCHEMA_VERSION),
        "smeltery.units": json.dumps(
            {q: (next(iter(u)) if len(u) == 1 else sorted(u)) for q, u in sorted(units.items())}, sort_keys=True
        ),
        "smeltery.smiles_form": ",".join(sorted({r["smiles_form"] for r in rows})),
        "smeltery.runs": json.dumps(list(dict.fromkeys(r["run_id"] for r in rows))),
        "smeltery.grain": GRAIN,
        "smeltery.generator": f"smeltery {version}",
        **(
            {"smeltery.generation": json.dumps(dict(reversed(generation.items())), sort_keys=True)}
            if generation
            else {}
        ),
    }


def column_units(rows: list[dict]) -> dict[str, str | None]:
    """Column -> unit for the score and paired-ddE value columns (value, sem, floor): the unit when ONE holds for every
    row that has a value, else None (the row's own `score_unit` states it). Every other column has no unit. The one
    rule behind a dataset record's `columns[].unit` and the Hugging Face card / Croissant fields."""
    score_units = {r["score_unit"] for r in rows if r["score"] is not None}
    paired_units = {r["score_unit"] for r in rows if r["paired_ddE"] is not None}
    one = {
        "score": score_units,
        "score_sem": score_units,
        "score_floor": score_units,
        "paired_ddE": paired_units,
        "paired_ddE_sem": paired_units,
        "paired_ddE_floor": paired_units,
    }
    return {c: next(iter(u)) if len(u) == 1 else None for c, u in one.items()}


def to_table(rows: list[dict]):
    """A pyarrow Table of `rows` under `arrow_schema()`, with the file metadata attached."""
    pa = _pyarrow()
    schema = arrow_schema(file_metadata(rows))
    return pa.Table.from_pylist(rows, schema=schema)


def write_dataset(paths: list[str | Path], dest: str | Path, fmt: str = "parquet", *, canonicalize: bool = True) -> int:
    """Export the runs under `paths` to `dest`; returns the number of rows written. `fmt` is parquet or arrow.

    One file. A dataset DIRECTORY with its `attestation.dataset/1` record is `dataset_record.export_dataset_dir`."""
    if fmt not in FORMATS:
        raise ExportError(f"unknown format {fmt!r}; one of {', '.join(FORMATS)}")
    rows = export_rows(paths, canonicalize=canonicalize)
    if not rows:
        raise ExportError("no rows: none of the runs has a per-pose score to export")
    write_rows(rows, dest, fmt)
    return len(rows)


def write_rows(rows: list[dict], dest: str | Path, fmt: str = "parquet") -> None:
    """Write `rows` (from `export_rows`) to `dest` as Parquet or Arrow IPC, atomically."""
    table = to_table(rows)
    dest = Path(dest)
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        if fmt == "parquet":
            import pyarrow.parquet as pq

            pq.write_table(table, tmp)
        else:
            import pyarrow.ipc as ipc

            with ipc.new_file(str(tmp), table.schema) as w:
                w.write_table(table)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv[:1] == ["split"]:  # `smeltery export split ...`: versioned splits of an exported table
        from .dataset_splits import main as split_main

        return split_main(argv[1:])
    if argv[:1] == ["hf"]:  # `smeltery export hf ...`: a Hugging Face folder with its card and croissant.json
        from .hub_export import main as hf_main

        return hf_main(argv[1:])
    if argv[:1] == ["dataset"]:  # `smeltery export dataset ...`: a dataset directory with its attestation.dataset/1
        from .dataset_record import main as dataset_main

        return dataset_main(argv[1:])
    ap = argparse.ArgumentParser(
        prog="smeltery export",
        description="Write finished runs as one columnar dataset, one row per (ligand, target, tier), with units, "
        "uncertainty, decision and provenance columns. Needs the `dataset` extra (pyarrow).",
    )
    ap.add_argument(
        "format",
        choices=FORMATS,
        help="parquet, or arrow (the Arrow IPC file format); `split` is `export split --help`, a dataset directory "
        "with its record is `export dataset --help`, a Hugging Face folder is `export hf --help`",
    )
    ap.add_argument("runs", nargs="+", help="run OUT directories or run-*.json files; several are concatenated")
    ap.add_argument("-o", "--out", required=True, help="the file to write")
    ap.add_argument(
        "--no-canonical-smiles", action="store_true", help="keep SMILES as recorded (skip RDKit canonicalisation)"
    )
    args = ap.parse_args(argv)
    try:
        n = write_dataset(args.runs, args.out, args.format, canonicalize=not args.no_canonical_smiles)
    except ExportError as e:
        print(f"smeltery export: {e}", file=sys.stderr)
        return 2
    print(f"wrote {n} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
