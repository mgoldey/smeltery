"""Stage reuse: a resubmitted campaign reuses the stages an identical earlier run already completed.

**What is a stage, and which ones are reused.** A run is the funnel's stages in order: the ones before the
measurement (structure, docking, poses, pocket, parity, the gates), the measurement tier, the cut, then any analyses.

* Before the measurement: recomputed every run. Their output is geometry and files the run builds deterministically
  from its inputs; it is not cached, but it is digested (`plan_digest`: every candidate's poses, the per-pose values
  the gates recorded, the point charges and their provenance), and that digest is the measurement's upstream.
* The measurement tier (`FieldInteraction`, `FieldDftInteraction`: the SCFs, where the time goes): cached and reused.
  Its output is every candidate's per-pose values it set and its calc log (one line per SCF).
* The cut: recomputed (it is arithmetic on the measurement, cheaper than a lookup), digested for the record.
* An analysis: cached and reused, unless it builds fields of its own with an external program (`extra_fields`:
  `[charge_sensitivity]` runs pdb2pqr, whose build smeltery cannot verify), which is recomputed every run.

**The digest of a stage** (its key) is the sha256 of `{stage, kind, upstream, settings, identity}`:

    upstream   the measurement: `plan_digest`. An analysis: the digest of everything it can read (the record's
               inputs, tiers and results so far, timings and file paths dropped), which covers the measurement's
               output, the cut, and every earlier analysis. So a change upstream invalidates that stage and every
               stage after it, and nothing before it.
    settings   the tier's `settings()` (basis, convergence, remedies, field provenance ...) or the analysis's section.
    identity   the code that computes it (`identity`): a sha256 over smeltery's own source files, the Python and
               numpy versions, and ferric's build (its commit or index version).

**What counts as identical, and when nothing is reused.** Reuse is ON only when every part of the identity is
verified (`refusal`): ferric's identity must be VERIFIED (a `ferric.__build__` stamp, a git install, or an index
release) and NOT dirty. A dirty ferric build, or one only inferred from a checkout, is never reused and never stored:
its commit does not say what was built. smeltery's own identity is the sha256 of its source files, so an edited
(dirty) smeltery is reusable exactly when its source bytes are identical, and a one-byte change invalidates. A
different ferric build (another commit, another stamp) is another key: nothing is reused across builds.

**The store** (`StageStore`) is per workspace, content-addressed: `ROOT/.smeltery/stages/<k[:2]>/<k>.json`, one
file per stage key. It is written by a local campaign job (`smeltery.jobs`, under the job root) and by
`smeltery run --stage-cache DIR` (or `SMELTERY_STAGE_CACHE`); a plain run without either neither reads nor writes
one. Prior run directories are not searched: they live anywhere, can be moved, edited or deleted, and a run
directory holds no per-stage output in a reusable form. An entry holds the key's own document, the output, the
output's sha256 and where it came from (run_id, run directory, when). It is written whole to a temporary file,
fsync'd, then hard-linked into place: the link fails if the entry exists, so concurrent writers never interleave
and the first complete entry wins (every writer of one key computed the same output). A read re-derives the key
from the stored document and the sha256 from the stored output; an entry that fails either check is renamed aside
(`*.corrupt-<pid>`), reported, and recomputed: never trusted.

**Recorded honestly.** A run with a store writes `results.reuse` in its run record (mapped to `ext.smeltery.reuse`
in the core record, and per stage into `steps/NN-<stage>/stage.json`): for every stage its digest, upstream and
output digests, and `reused` with `reused_from` (the run, its directory, the entry's digest and time) when it was
reused. A reused stage's output is the stored output itself, so the run record, calcs.jsonl and the pose files are
byte-identical to the run that computed it, except this provenance and the run's timestamps and wall-clock times.

**Progress and cancellation.** OUT/stages.partial.jsonl gets one line per stage as it completes (fsync'd): what a
cancelled or failed job's record lists as completed (`ext["smeltery.job"].stages_completed`, `smeltery.jobs.settle`).
A stage is stored only once it has completed, so a cancel that lands mid-tier leaves every completed stage reusable
and the interrupted one not. The file is removed when the run record is written.

**With the per-SCF checkpoint** (`smeltery.checkpoint`, OUT/calcs.partial.jsonl): the two layers nest. The stage
store is consulted first, across runs, for a whole completed stage; on a miss the measurement runs with the
checkpoint, which resumes an interrupted measurement of the same OUT leg by leg; once it completes, the stage is
stored. So a crash mid-measurement costs only the unfinished SCFs on a rerun into the same OUT, and a resubmission
into a new OUT after the measurement finished costs none.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import hashlib
import json
import os
import platform
import sys
import uuid
from pathlib import Path

from .record import utc_now

SCHEMA = "smeltery.stage/1"
REUSE_SCHEMA = "smeltery.reuse/1"
#: Under a workspace root (the job backend's): the stage store.
STORE_DIR = Path(".smeltery") / "stages"
#: The stage store for `smeltery run`, when `--stage-cache` is not given.
ENV = "SMELTERY_STAGE_CACHE"
#: OUT/<this>: one line per completed stage, while the run is live.
PROGRESS = "stages.partial.jsonl"
#: Measurement tiers whose `run` sets only per-pose values and `calc_log` (and the field provenance build_plan already
#: set): their output can be stored and put back. Any other measure hook is recomputed.
CACHEABLE_TIERS = ("field_interaction", "field_dft_interaction")
WHY_PLAN = "recomputed every run: geometry and files built from the inputs; digested as the measurement's upstream"
WHY_CUT = "recomputed every run: arithmetic on the measurement, cheaper than a lookup"


def canonical(obj) -> bytes:
    """The bytes a digest is taken over: sorted keys, no whitespace; NaN spelled as JSON's NaN token."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=_default, allow_nan=True).encode()


def sha(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


now = utc_now  # `smeltery.record.utc_now`: TIME_FORMAT in UTC


# ---- identity --------------------------------------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def source_digest() -> str:
    """sha256 over smeltery's own source: every .py file of the package, by relative path and bytes."""
    root = Path(__file__).resolve().parent
    h = hashlib.sha256()
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in rel:
            continue
        h.update(rel.encode() + b"\0" + p.read_bytes() + b"\0")
    return h.hexdigest()


def identity() -> dict:
    """The code identity a stage's output depends on (see the module docstring)."""
    from .record import ferric_identity

    f = ferric_identity()
    try:
        import numpy

        np_version = numpy.__version__
    except ImportError:  # pragma: no cover - numpy is a core dependency
        np_version = None
    return {
        "smeltery_source_sha256": source_digest(),
        "python": platform.python_version(),
        "numpy": np_version,
        "ferric": {
            "version": f.get("version"),
            "commit": f.get("commit"),
            "dirty": f.get("dirty"),
            "provenance": f.get("provenance"),
        },
    }


def refusal(ident: dict) -> str | None:
    """Why stages of this identity may not be reused or stored; None when they may."""
    f = ident.get("ferric") or {}
    if f.get("dirty") is not False:
        state = "dirty" if f.get("dirty") else "of unknown cleanliness"
        return f"ferric's build is {state}: its commit does not say what was built, so no stage is reused or stored"
    if not str(f.get("provenance") or "").startswith("VERIFIED"):
        return f"ferric's build is not verified ({f.get('provenance')}), so no stage is reused or stored"
    if not (f.get("commit") or f.get("version")):
        return "ferric's build names neither a commit nor a version, so no stage is reused or stored"
    return None


# ---- digests of what a stage sees --------------------------------------------------------------------------------


def _default(o):
    """numpy scalars and arrays as the Python values they hold; anything else by its text."""
    for attr in ("tolist", "item"):
        if hasattr(o, attr):
            return getattr(o, attr)()
    return str(o)


def _plain(x):
    return json.loads(json.dumps(x, default=_default, allow_nan=True))


def plan_digest(plan) -> str:
    """The output of every stage before the measurement, as the measurement sees it: candidates, their poses (exact
    coordinates), the per-pose values recorded so far, flexible-receptor text, the pose gate's verdicts, the point
    charges and their provenance. No file paths: two run directories of one config give one digest."""
    import numpy as np

    def report(r):
        if r is None:
            return None
        failures = r.failures() if hasattr(r, "failures") else None
        return {"all_passed": bool(getattr(r, "all_passed", False)), "failures": _plain(failures)}

    doc = {
        "parent": plan.parent.name,
        "candidates": [
            {
                "name": c.name,
                "smiles": c.smiles,
                "poses": [
                    {"symbols": list(p.symbols), "coords": np.asarray(p.coords_ang, dtype=float).tolist()}
                    for p in c.poses
                ],
                "per_pose": _plain(c.per_pose),
                "receptor_flex": list(c.receptor_flex),
                "pose_report": report(c.pose_report),
            }
            for c in plan.candidates
        ],
        "field": [[float(q.q), *(float(v) for v in q.xyz_ang)] for q in plan.field],
        "field_provenance": _plain(getattr(plan.field, "provenance", None)),
    }
    return sha(doc)


_PATHLIKE = ("file", "copy", "path")


def _timeless(x):
    """A record's results as an analysis may read them, without wall-clock times or file paths (a path names the run
    directory; the sha256 beside it names the content)."""
    if isinstance(x, dict):
        return {
            k: (None if k in _PATHLIKE and isinstance(v, str) else _timeless(v))
            for k, v in x.items()
            if k not in ("seconds", "structure_fetch", "stages", "reuse")
        }
    if isinstance(x, list | tuple):
        return [_timeless(v) for v in x]
    return x


def visible_digest(rec) -> str:
    """What an analysis can read when it starts: the record's inputs, tiers and results so far."""
    return sha({"inputs": _plain(rec.inputs), "tiers": _plain(rec.tiers), "results": _timeless(_plain(rec.results))})


# ---- the store -------------------------------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class Entry:
    key: str
    stage: str
    output: dict
    output_digest: str
    origin: dict  # {"run_id", "run_dir", "created_at"}


class StageStore:
    """The content-addressed stage store (see the module docstring for its layout and its write and read rules)."""

    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()

    def path(self, key: str) -> Path:
        return self.root / key[:2] / f"{key}.json"

    def get(self, key: str) -> Entry | None:
        """The verified entry of `key`, or None. A corrupt entry is set aside, reported on stderr, and is a miss."""
        p = self.path(key)
        try:
            raw = p.read_bytes()
        except FileNotFoundError:
            return None
        except OSError as e:
            print(f"warning: stage store entry {p} cannot be read ({e}); recomputing", file=sys.stderr)
            return None
        why = None
        try:
            doc = json.loads(raw)
            if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
                why = "not a stage entry"
            elif doc.get("key") != key or sha(doc.get("key_doc")) != key:
                why = "its key does not match its key document"
            elif not isinstance(doc.get("output"), dict) or sha(doc["output"]) != doc.get("output_sha256"):
                why = "its output does not match its sha256"
        except ValueError as e:
            why = f"unreadable JSON: {e}"
        if why is not None:
            aside = p.with_name(f"{p.name}.corrupt-{os.getpid()}-{uuid.uuid4().hex[:6]}")
            with contextlib.suppress(OSError):
                os.rename(p, aside)
            print(f"warning: stage store entry {p} is corrupt ({why}); set aside, recomputing", file=sys.stderr)
            return None
        return Entry(key, str(doc.get("stage")), doc["output"], doc["output_sha256"], dict(doc.get("origin") or {}))

    def put(self, key: str, key_doc: dict, stage: str, output: dict, origin: dict) -> bool:
        """Store one completed stage; False when an entry of `key` already exists (the first complete one wins)."""
        if sha(key_doc) != key:
            raise ValueError("a stage key must be the sha256 of its key document")
        doc = {
            "schema": SCHEMA,
            "key": key,
            "key_doc": key_doc,
            "stage": stage,
            "output": output,
            "output_sha256": sha(output),
            "origin": origin,
        }
        final = self.path(key)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = final.with_name(f".{final.name}.{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            with open(tmp, "wb") as fh:
                # key order kept (not sorted): a reused calc line is then byte-identical to the one first logged
                fh.write(json.dumps(doc, indent=1, allow_nan=True).encode() + b"\n")
                fh.flush()
                os.fsync(fh.fileno())
            try:
                os.link(tmp, final)
            except FileExistsError:
                return False
            return True
        finally:
            with contextlib.suppress(OSError):
                tmp.unlink()


def store_for(flag: str | os.PathLike[str] | None, env=None) -> StageStore | None:
    """`--stage-cache DIR`, else $SMELTERY_STAGE_CACHE, else no store."""
    env = os.environ if env is None else env
    where = flag if flag is not None else (env.get(ENV) or None)
    return None if where is None else StageStore(where)


def workspace_store(root: str | os.PathLike[str]) -> StageStore:
    return StageStore(Path(root) / STORE_DIR)


# ---- one run's stages ------------------------------------------------------------------------------------------


class Miss(Exception):
    """`lookup_only`: the stage is not stored, so nothing after it can be predicted either."""


def append_line(path: Path, row: dict) -> None:
    """One JSON line, appended in a single write and fsync'd: a kill leaves at most a torn last line."""
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
    try:
        os.write(fd, (json.dumps(row, sort_keys=True, allow_nan=True) + "\n").encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def read_progress(run_dir: str | os.PathLike[str]) -> list[dict] | None:
    """The completed stages a live (or interrupted) run logged; None when it left no stage log. A torn last line, from
    a kill mid-write, is dropped."""
    try:
        text = (Path(run_dir) / PROGRESS).read_text()
    except OSError:
        return None
    rows = []
    for line in text.splitlines():
        with contextlib.suppress(ValueError):
            row = json.loads(line)
            if isinstance(row, dict) and "stage" in row:
                rows.append(row)
    return rows


def discard_progress(run_dir: str | os.PathLike[str]) -> None:
    with contextlib.suppress(FileNotFoundError):
        (Path(run_dir) / PROGRESS).unlink()


class RunStages:
    """One run's stages: digests them, reuses stored ones, stores completed ones, and logs each as it completes.

    `out_dir`: where OUT/stages.partial.jsonl goes (None: no log). `store`: the stage store (None: nothing reused or
    stored, and the record gets no `results.reuse`). `lookup_only`: predict, never run (`predict`)."""

    def __init__(self, out_dir=None, store: StageStore | None = None, *, lookup_only: bool = False) -> None:
        self.out_dir = None if out_dir is None else Path(out_dir)
        self.store = store
        self.lookup_only = lookup_only
        self.identity = identity() if store is not None else None
        self.why_not = None if store is None else refusal(self.identity or {})
        self.enabled = store is not None and self.why_not is None
        self.entries: list[dict] = []
        self.plan_digest: str | None = None
        self._prev_output: str | None = None
        self.run_id: str | None = None
        if self.out_dir is not None and not lookup_only:
            discard_progress(self.out_dir)  # a rerun into OUT logs its own stages from the start

    def _done(self, entry: dict) -> None:
        self.entries.append(entry)
        if self.out_dir is not None and not self.lookup_only:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            append_line(self.out_dir / PROGRESS, entry)

    def _key(self, stage: str, kind: str, upstream: str, settings) -> tuple[str, dict]:
        doc = {"stage": stage, "kind": kind, "upstream": upstream, "settings": _plain(settings)}
        doc["identity"] = self.identity
        return sha(doc), doc

    def _origin(self) -> dict:
        return {"run_id": self.run_id, "run_dir": None if self.out_dir is None else str(self.out_dir), "at": now()}

    def _lookup(self, key: str) -> Entry | None:
        if not self.enabled or self.store is None:
            return None
        return self.store.get(key)

    def _reused(self, entry: dict, hit: Entry) -> dict:
        entry.update(
            output_digest=hit.output_digest,
            reused=True,
            reused_from={"digest": hit.key, **hit.origin},
        )
        print(
            f"reused stage {entry['stage']} from run {hit.origin.get('run_id')} ({hit.origin.get('run_dir')}), "
            f"stage digest {hit.key[:12]}: nothing recomputed"
        )
        return entry

    def _store(self, key: str, doc: dict, stage: str, output: dict) -> None:
        if self.enabled and self.store is not None:
            try:
                self.store.put(key, doc, stage, output, self._origin())
            except OSError as e:  # a full disk must not cost the run its record: it only loses the reuse
                print(f"warning: stage {stage} not stored for reuse: {e}", file=sys.stderr)

    # -- before the measurement

    def plan_built(self, plan, rows: list[dict]) -> None:
        """The stages build_plan ran, completed: digest their output once (the measurement's upstream)."""
        self.plan_digest = plan_digest(plan)
        for r in rows:
            self._done(
                {
                    "stage": str(r["stage"]),
                    "kind": r.get("kind"),
                    "cacheable": False,
                    "why": WHY_PLAN,
                    "output_digest": self.plan_digest,
                    "reused": False,
                    "reused_from": None,
                }
            )

    # -- the measurement

    def measure(self, plan, run) -> None:
        """Reuse the stored measurement, or `run()` it (the tier, with its checkpoint) and store it."""
        tier = plan.tier
        upstream = self.plan_digest or plan_digest(plan)
        settings = tier.settings()
        key, doc = self._key(tier.name, "measure", upstream, settings)
        cacheable = tier.name in CACHEABLE_TIERS and type(tier).__module__ == "smeltery.tiers"
        entry = {
            "stage": tier.name,
            "kind": "measure",
            "cacheable": cacheable,
            "why": None if cacheable else "recomputed every run: a measurement hook may change more than it reports",
            "digest": key,
            "upstream": upstream,
            "reused": False,
            "reused_from": None,
        }
        hit = self._lookup(key) if cacheable else None
        if hit is not None:
            for c in plan.candidates:
                for q, values in (hit.output.get("per_pose") or {}).get(c.name, {}).items():
                    c.per_pose[q] = list(values)
            tier.calc_log = [dict(r) for r in hit.output.get("calc_log") or []]
            self._prev_output = hit.output_digest
            self._done(self._reused(entry, hit))
            return
        if self.lookup_only:
            raise Miss(tier.name)
        before = {c.name: _plain(c.per_pose) for c in plan.candidates}
        run()
        output = {
            "per_pose": {
                c.name: {q: _plain(v) for q, v in c.per_pose.items() if before[c.name].get(q) != _plain(v)}
                for c in plan.candidates
            },
            "calc_log": _plain(list(getattr(tier, "calc_log", None) or [])),
        }
        entry["output_digest"] = self._prev_output = sha(output)
        if cacheable and sha(tier.settings()) == sha(settings):  # settings that changed while running: not reusable
            self._store(key, doc, tier.name, output)
        elif cacheable:
            entry.update(cacheable=False, why="the tier's recorded settings changed while it ran")
        self._done(entry)

    # -- the cut

    def cut(self, plan, results: dict) -> None:
        settings = {"keep": plan.cfg["cut"]["keep"], "z": plan.cfg["cut"].get("z", 2.0), "floor": plan.floor}
        upstream = self._prev_output or ""
        key, _doc = self._key("cut", "rank", upstream, settings)
        out = {k: _plain(results.get(k)) for k in ("paired_ddE", "unpaired_sem", "cut")}
        self._prev_output = sha(out)
        self._done(
            {
                "stage": "cut",
                "kind": "rank",
                "cacheable": False,
                "why": WHY_CUT,
                "digest": key,
                "upstream": upstream,
                "output_digest": self._prev_output,
                "reused": False,
                "reused_from": None,
            }
        )

    # -- the analyses

    def analysis(self, spec, plan, rec, run) -> dict:
        """The analysis's dict: the stored one, or `run()`'s (stored when it can be)."""
        upstream = visible_digest(rec)
        settings = {"key": spec.key, "section": _plain(plan.cfg.get(spec.section))}
        key, doc = self._key(spec.key, "analysis", upstream, settings)
        cacheable = not hasattr(spec.hook, "extra_fields")
        entry = {
            "stage": spec.key,
            "kind": "analysis",
            "cacheable": cacheable,
            "why": None
            if cacheable
            else "recomputed every run: it builds its fields with an external program whose build smeltery cannot "
            "verify",
            "digest": key,
            "upstream": upstream,
            "reused": False,
            "reused_from": None,
        }
        hit = self._lookup(key) if cacheable else None
        if hit is not None:
            self._prev_output = hit.output_digest
            self._done(self._reused(entry, hit))
            return json.loads(json.dumps(hit.output["result"], allow_nan=True))
        if self.lookup_only:
            raise Miss(spec.key)
        got = run()
        try:
            if not isinstance(got, dict):
                raise TypeError("not a dict")
            output = {"result": json.loads(json.dumps(got, allow_nan=True))}
        except (TypeError, ValueError):
            output, cacheable = None, False
            entry.update(cacheable=False, why="its result is not plain JSON, so it cannot be stored")
        entry["output_digest"] = self._prev_output = sha(output if output is not None else repr(got))
        if cacheable and output is not None:
            self._store(key, doc, spec.key, output)
        self._done(entry)
        return got

    # -- the record

    def counts(self) -> tuple[int, int]:
        """(stages reused, stages that could have been)."""
        cacheable = [e for e in self.entries if e.get("cacheable")]
        return sum(1 for e in cacheable if e.get("reused")), len(cacheable)

    def summary(self) -> dict | None:
        """`results.reuse`; None without a store (the record then keeps its old shape)."""
        if self.store is None:
            return None
        n, m = self.counts()
        return {
            "schema": REUSE_SCHEMA,
            "enabled": self.enabled,
            "why_not": self.why_not,
            "identity": self.identity,
            "plan_digest": self.plan_digest,
            "reused": n,
            "reusable": m,
            "stages": [dict(e) for e in self.entries],
        }


def predict(plan, store: StageStore | None) -> tuple[int, int, str | None]:
    """(stages an identical earlier run already stored, stages that can be reused, why reuse is off or None), found
    by walking the run's stages against the store without computing any: the chain stops at the first miss. `plan` is
    used up (its candidates take the stored values)."""
    from .methods import active_hooks
    from .planning import run_plan

    stages = RunStages(None, store, lookup_only=True)
    cacheable = int(plan.tier.name in CACHEABLE_TIERS) + sum(
        1 for s in active_hooks("analysis", plan.cfg) if not hasattr(s.hook, "extra_fields")
    )
    if store is None or not stages.enabled:
        return 0, cacheable, stages.why_not or ("no stage store" if store is None else None)
    with open(os.devnull, "w") as quiet, contextlib.redirect_stdout(quiet):
        try:
            run_plan(plan, stages=stages)
        except Miss:
            pass
    return stages.counts()[0], cacheable, None


def run_stages(run_dir: str | os.PathLike[str]) -> list[dict] | None:
    """A run's stages as it recorded them: `results.reuse.stages` of its run record once it is written (None when the
    run had no stage store), else the stage log of a live or stopped run (None when it left none)."""
    run_dir = Path(run_dir)
    found = sorted(run_dir.glob("run-*.json"))
    if found:
        try:
            reuse = (json.loads(found[0].read_text()).get("results") or {}).get("reuse")
        except (OSError, ValueError):
            return None
        return list(reuse["stages"]) if isinstance(reuse, dict) and isinstance(reuse.get("stages"), list) else None
    return read_progress(run_dir)


def stage_lines(run_dir: str | os.PathLike[str]) -> list[str]:
    """`smeltery status`'s lines about stages: which completed (and which of those were reused), and the count."""
    rows = run_stages(run_dir)
    if not rows:
        return []
    names = ", ".join(f"{r['stage']}{' (reused)' if r.get('reused') else ''}" for r in rows)
    cacheable = [r for r in rows if r.get("cacheable")]
    n = sum(1 for r in cacheable if r.get("reused"))
    return [f"stages completed: {names}", f"stages reused: {n} of {len(cacheable)} reusable"]
