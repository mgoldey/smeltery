"""Active learning over the funnel: an external model proposes candidates, smeltery scores them, each loop
iteration recorded in attestation (roadmap 37, docs/active-learning.md, spec 2026-11-20-active-learning-design.md).

The loop is orchestration and recording only -- it computes no chemistry. An iteration is an ordinary campaign job
submitted through `smeltery.mcp.screening` (so the ceiling, the stage store and `cancel` all apply; there is no
second execution path), polled to completion, its paired-ΔΔE scores read back from the run's own record and fed to
the proposer for the next batch.

Recording (spec "family / arm choice"): the loop is one attestation family `al:<name>`, each iteration an arm
`iter-NNN`, so `attest runs compare al:<name>` ranks the iterations by `best_paired_ddE` -- the proposer's learning
curve. A finished core record is NEVER rewritten: its bytes are what other readers hash (the job's own `settle`, a
dataset's `source_runs[].record_sha256`, a ledger scan), and a record annotated afterwards would claim the producer
observed at run time something it did not. So everything the record says about the loop is handed to the run at
SUBMIT -- `JobSpec.arm` and `JobSpec.ext` (`jobs.check_spec`, `smeltery run --arm --ext-json`) -- and the record
states it the first and only time it is written. The arm metrics are the RUN's own
(`core_record.arm_metrics`, derived from its results); the loop's manifest keeps the cross-iteration summary.

Public surface: `Proposer`, `ScoredCandidate`, `PoolProposer`, `load_proposer`, `run_loop`, `LoopManifest`, and the
`smeltery al run` CLI (`al_main`). smeltery depends on `attestation_schema` only, never on `attestation`.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable

MANIFEST = "al-manifest.json"
MANIFEST_SCHEMA = "smeltery.al/1"
#: The core record's `ext` namespace the loop fills (`JobSpec.ext`, written at the record's first write).
EXT_NAMESPACE = "smeltery.al"
#: How long `run_loop` waits for one iteration's job before giving up (seconds). Fake-engine jobs finish in ~1 s.
DEFAULT_TIMEOUT_S = 1800.0


class AlError(Exception):
    """A loop could not proceed: a bad proposer, an unparseable reference, an iteration that failed."""


# ---- the scored history and the proposer boundary --------------------------------------------------------------


@dataclass(frozen=True)
class ScoredCandidate:
    """One molecule the funnel scored, as the proposer sees it. `score` is the paired ΔΔE mean against the reference
    (lower is better); `None` for the reference itself or a row the run did not score. `uncertainty` is its SEM."""

    smiles: str  # canonical (RDKit)
    name: str
    score: float | None
    uncertainty: float | None
    n_poses: int | None
    iteration: int


@runtime_checkable
class Proposer(Protocol):
    """The external model. `propose` returns at most `n` SMILES given the whole scored history; the loop owns
    validity and dedup, so re-suggesting a scored molecule is free. `fit` and `seeds` are optional."""

    def propose(self, history: list[ScoredCandidate], n: int) -> list[str]: ...


def _canonical(smiles: str) -> str | None:
    """RDKit canonical SMILES, or None when it does not parse."""
    from rdkit import Chem

    mol = Chem.MolFromSmiles(smiles)
    return None if mol is None else Chem.MolToSmiles(mol)


class PoolProposer:
    """A trivial, dependency-free proposer for tests and the demo: draw unscored SMILES from a fixed pool,
    deterministically shuffled by `Random(seed + len(history))`. Exhausts once every pool member has been scored."""

    def __init__(self, pool: Sequence[str], seed: int = 0) -> None:
        self.seed = int(seed)
        self.seeds = {"pool": self.seed}
        self._pool: list[str] = []
        for smi in pool:
            canon = _canonical(smi)
            if canon is not None and canon not in self._pool:
                self._pool.append(canon)

    def propose(self, history: list[ScoredCandidate], n: int) -> list[str]:
        scored = {c.smiles for c in history}
        remaining = [s for s in self._pool if s not in scored]
        random.Random(self.seed + len(history)).shuffle(remaining)
        return remaining[: max(0, int(n))]


def load_proposer(ref: str) -> Proposer:
    """Import `module:obj` and return the proposer. A class or a zero-arg factory is called; an instance is used."""
    module_name, _, attr = ref.partition(":")
    if not module_name or not attr:
        raise AlError(f"--proposer must be 'module:object', got {ref!r}")
    try:
        module = importlib.import_module(module_name)
    except ImportError as e:
        raise AlError(f"cannot import proposer module {module_name!r}: {e}") from e
    try:
        obj = getattr(module, attr)
    except AttributeError as e:
        raise AlError(f"proposer {attr!r} is not in module {module_name!r}") from e
    if isinstance(obj, type) or (not hasattr(obj, "propose") and callable(obj)):
        obj = obj()  # a class (its `propose` is unbound until instantiated) or a zero-arg factory
    if not hasattr(obj, "propose"):
        raise AlError(f"proposer {ref!r} has no propose(history, n) method")
    return obj


def proposer_identity(proposer: Proposer, ref: str | None) -> dict:
    """What is OBSERVABLE about the proposer, never guessed: its dotted ref, module, class, module version when it
    has one, and the sha256 of the module's source file when it is a real file."""
    module = sys.modules.get(type(proposer).__module__)
    source_sha256 = None
    module_file = getattr(module, "__file__", None)
    if module_file:
        try:
            source_sha256 = hashlib.sha256(Path(module_file).read_bytes()).hexdigest()
        except OSError:
            source_sha256 = None
    identity = {
        "ref": ref,
        "module": type(proposer).__module__,
        "class": type(proposer).__name__,
        "version": getattr(module, "__version__", None),
        "source_sha256": source_sha256,
    }
    # a proposer that exposes `params` (its non-seed settings, JSON scalars) has them recorded too; absent otherwise,
    # so every existing proposer's identity is unchanged
    params = getattr(proposer, "params", None)
    if isinstance(params, Mapping):
        identity["params"] = {str(k): v for k, v in params.items() if isinstance(v, str | int | float | bool | None)}
    return identity


def _proposer_seeds(proposer: Proposer) -> dict[str, int]:
    """The proposer's own integer seeds, where it exposes them as a `seeds` mapping; never invented."""
    raw = getattr(proposer, "seeds", None)
    if not isinstance(raw, Mapping):
        return {}
    return {str(k): int(v) for k, v in raw.items() if isinstance(v, int) and not isinstance(v, bool)}


# ---- the loop manifest -----------------------------------------------------------------------------------------


@dataclass
class IterationRecord:
    """One iteration in the loop manifest: which run, the exact core-record bytes, and the scores it produced."""

    iteration: int
    run_dir: str  # relative to the loop directory
    campaign: str
    family: str
    arm: str
    state: str
    candidates: list[dict]
    batch_best: float | None
    budget_spent_cpu_h: float
    core_record_sha256: str | None


@dataclass
class LoopManifest:
    """The audit trail of a loop (`<loop-dir>/al-manifest.json`), rewritten after each iteration so a stopped loop
    resumes from it."""

    loop: str
    reference: dict
    proposer: dict
    target: str
    budget_cpu_h: float | None
    iterations: list[IterationRecord] = field(default_factory=list)
    stopped: dict | None = None
    schema: str = MANIFEST_SCHEMA

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "loop": self.loop,
            "reference": self.reference,
            "proposer": self.proposer,
            "target": self.target,
            "budget_cpu_h": self.budget_cpu_h,
            "iterations": [asdict(it) for it in self.iterations],
            "stopped": self.stopped,
        }


def _canonical_bytes(obj: dict) -> bytes:
    try:
        from attestation_schema.canonical import canonicalize

        return canonicalize(obj)
    except Exception:  # noqa: BLE001 - the manifest is still written, just not JCS, without the schema package
        return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


def write_manifest(loop_dir: Path, manifest: LoopManifest) -> Path:
    """Write the manifest atomically (temp file, then replace)."""
    loop_dir.mkdir(parents=True, exist_ok=True)
    dest = loop_dir / MANIFEST
    tmp = dest.with_suffix(".json.tmp")
    tmp.write_bytes(_canonical_bytes(manifest.to_dict()))
    os.replace(tmp, dest)
    return dest


def read_manifest(loop_dir: Path) -> LoopManifest | None:
    """The manifest of an earlier loop in this directory, for resume; None when there is none."""
    path = Path(loop_dir) / MANIFEST
    try:
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    its = [IterationRecord(**it) for it in raw.get("iterations") or []]
    return LoopManifest(
        loop=raw.get("loop", ""),
        reference=raw.get("reference") or {},
        proposer=raw.get("proposer") or {},
        target=raw.get("target", ""),
        budget_cpu_h=raw.get("budget_cpu_h"),
        iterations=its,
        stopped=raw.get("stopped"),
        schema=raw.get("schema", MANIFEST_SCHEMA),
    )


def _history_from(iterations: list[IterationRecord]) -> list[ScoredCandidate]:
    """Re-seed the scored history from a manifest's recorded iterations (resume)."""
    out: list[ScoredCandidate] = []
    for it in iterations:
        for c in it.candidates:
            out.append(
                ScoredCandidate(
                    smiles=c["smiles"],
                    name=c["name"],
                    score=c.get("score"),
                    uncertainty=c.get("uncertainty"),
                    n_poses=c.get("n"),
                    iteration=it.iteration,
                )
            )
    return out


# ---- re-stamping the iteration's core record -------------------------------------------------------------------


def _finite(x: object) -> bool:
    return isinstance(x, int | float) and not isinstance(x, bool) and x == x and abs(float(x)) != float("inf")


def core_record_sha256(run_dir: Path) -> str | None:
    """The sha256 of the iteration's core record AS THE JOB WROTE IT, for the loop manifest. Read-only: the record is
    the artifact of record and is never rewritten -- other readers (the job's `settle`, a dataset's
    `source_runs[].record_sha256`, a ledger scan) may already have hashed those bytes, and a record re-stamped after
    the fact would claim the producer observed at run time something it did not. The arm, the `ext.smeltery.al`
    namespace and the arm metrics are therefore handed to the run at SUBMIT (`JobSpec.arm` / `JobSpec.ext` ->
    `smeltery run --arm --ext-json`), so the first and only write already states them. None when there is no record
    (no `attestation` extra, or a run that wrote none)."""
    path = Path(run_dir) / "attestation" / "run.json"
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


# ---- reading an iteration's scores ------------------------------------------------------------------------------


def _record_in(run_dir: Path) -> dict | None:
    found = sorted(Path(run_dir).glob("run-*.json"))
    if not found:
        return None
    try:
        return json.loads(found[0].read_text())
    except (OSError, ValueError):
        return None


def _scores_for(record: dict, names: list[str], iteration: int, smiles: dict[str, str]) -> list[ScoredCandidate]:
    results = record.get("results") or {}
    paired = results.get("paired_ddE") or {}
    out = []
    for name in names:
        row = paired.get(name) or {}
        out.append(
            ScoredCandidate(
                smiles=smiles[name],
                name=name,
                score=row.get("mean"),
                uncertainty=row.get("sem"),
                n_poses=row.get("n"),
                iteration=iteration,
            )
        )
    return out


# ---- the loop ---------------------------------------------------------------------------------------------------


def _dedup(raw: list[str], seen: set[str]) -> list[str]:
    """Canonicalize, drop the unparseable (a warning), and keep only molecules not already seen (scored, the
    reference, or earlier in this batch)."""
    kept: list[str] = []
    batch_seen = set(seen)
    for smi in raw:
        canon = _canonical(smi)
        if canon is None:
            print(f"smeltery al: dropping unparseable SMILES {smi!r}", file=sys.stderr)
            continue
        if canon in batch_seen:
            continue
        batch_seen.add(canon)
        kept.append(canon)
    return kept


def run_loop(
    *,
    loop: str,
    target: str,
    reference_smiles: str,
    reference_name: str = "reference",
    proposer: Proposer,
    proposer_ref: str | None = None,
    iterations: int,
    batch: int,
    settings,
    out_dir: str | os.PathLike[str],
    budget_cpu_h: float | None = None,
    target_class: str | None = None,
    keep: int | None = None,
    relax_unmapped: bool | None = None,
    confirm: bool = False,
    backend=None,
    root: str | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    poll_interval_s: float = 0.5,
) -> LoopManifest:
    """Run the active-learning loop, submitting each iteration through the campaign job backend and recording it.

    Resumes from `<out_dir>/al-manifest.json` when one is there: iterations already recorded complete are kept and
    their scores re-seed the proposer's history. Returns the final manifest (also written to disk)."""
    from .mcp import screening

    ref_canon = _canonical(reference_smiles)
    if ref_canon is None:
        raise AlError(f"the reference SMILES {reference_smiles!r} does not parse")
    loop_dir = Path(out_dir).expanduser().resolve()
    prior = read_manifest(loop_dir)
    if prior is not None and prior.iterations:
        manifest = prior
        done = {it.iteration for it in prior.iterations if it.state == "complete"}
        history = _history_from([it for it in prior.iterations if it.state == "complete"])
        start = max(done) + 1 if done else 0
    else:
        manifest = LoopManifest(
            loop=loop,
            reference={"name": reference_name, "smiles": ref_canon},
            proposer=proposer_identity(proposer, proposer_ref),
            target=str(target),
            budget_cpu_h=budget_cpu_h,
        )
        history = []
        start = 0
    spent = manifest.iterations[-1].budget_spent_cpu_h if manifest.iterations else 0.0
    seen = {ref_canon} | {c.smiles for c in history}
    pseeds = _proposer_seeds(proposer)

    def stop(reason: str, detail: str) -> LoopManifest:
        manifest.stopped = {"reason": reason, "detail": detail}
        write_manifest(loop_dir, manifest)
        return manifest

    for n in range(start, iterations):
        if hasattr(proposer, "fit"):
            proposer.fit(history)
        proposed = _dedup(proposer.propose(history, batch), seen)
        if len(proposed) < 2:
            return stop("exhausted", f"iteration {n}: fewer than two new candidates to score ({len(proposed)})")
        names = [f"c{n:03d}-{i}" for i in range(len(proposed))]
        smiles = dict(zip(names, proposed, strict=True))
        ligand_set = {reference_name: ref_canon, **smiles}
        iter_dir = loop_dir / f"iter-{n:03d}"
        arm = f"iter-{n:03d}"
        try:
            _cfg, built = screening.plan(
                target,
                ligand_set,
                reference_ligand=reference_name,
                target_class=target_class,
                campaign=f"al:{loop}",
                keep=keep,
                relax_unmapped=relax_unmapped,
            )
        except screening.Refusal as e:
            return stop("failed", f"iteration {n}: {e}")
        cost_h = built.cost.cpu_hours
        if budget_cpu_h is not None:
            if cost_h is None:
                return stop(
                    "over_budget",
                    f"iteration {n}: a budget of {budget_cpu_h:g} CPU-hours is set but this screen cannot be priced "
                    f"({built.cost.cpu_hours_basis}), so it cannot be shown to be under it",
                )
            if spent + cost_h > budget_cpu_h:
                return stop(
                    "over_budget",
                    f"iteration {n}: {spent:.3f}+{cost_h:.3f} CPU-hours would cross the budget of {budget_cpu_h:g}",
                )
        # everything the record says about the loop is known HERE, before the run: the loop, the iteration, the
        # proposer's identity and seeds, the batch it proposed, and what this screen was predicted to cost. The job
        # hands it to the run, which states it the first (and only) time it writes its core record.
        al_ext = {
            "loop": loop,
            "iteration": n,
            "proposer": manifest.proposer,
            "proposer_seeds": dict(pseeds),
            "batch": [{"name": nm, "smiles": smiles[nm]} for nm in names],
            "budget_cpu_h": budget_cpu_h,
            "budget_spent_before_cpu_h": spent,
            "predicted_cpu_h": cost_h,
        }
        try:
            scr = screening.screen(
                target,
                ligand_set,
                settings=settings,
                reference_ligand=reference_name,
                target_class=target_class,
                campaign=f"al:{loop}",
                keep=keep,
                relax_unmapped=relax_unmapped,
                out_dir=str(iter_dir),
                root=root,
                confirm=confirm,
                backend=backend,
                arm=arm,
                ext={EXT_NAMESPACE: al_ext},
            )
        except screening.Refusal as e:
            return stop("failed", f"iteration {n}: {e}")
        handle = scr.handle
        deadline = time.monotonic() + timeout_s
        while True:
            st = screening.status(handle, settings=settings, root=root, backend=backend)
            if st.finished:
                break
            if time.monotonic() > deadline:
                return stop("failed", f"iteration {n}: timed out after {timeout_s:g}s in state {st.state}")
            time.sleep(poll_interval_s)
        spent += cost_h or 0.0
        if st.state != "complete":
            manifest.iterations.append(
                IterationRecord(
                    iteration=n,
                    run_dir=os.path.relpath(iter_dir, loop_dir),
                    campaign=f"al:{loop}",
                    family=f"al:{loop}",
                    arm=arm,
                    state=st.state,
                    candidates=[{"name": nm, "smiles": smiles[nm]} for nm in names],
                    batch_best=None,
                    budget_spent_cpu_h=spent,
                    core_record_sha256=None,
                )
            )
            return stop("failed", f"iteration {n}: the screen ended {st.state} ({st.message})")
        record = _record_in(iter_dir)
        scored = _scores_for(record or {}, names, n, smiles) if record else []
        finite = [c.score for c in scored if c.score is not None and _finite(c.score)]
        batch_best = min(finite) if finite else None
        sha = core_record_sha256(iter_dir)  # read, never written: the job's record is the artifact of record
        manifest.iterations.append(
            IterationRecord(
                iteration=n,
                run_dir=os.path.relpath(iter_dir, loop_dir),
                campaign=f"al:{loop}",
                family=f"al:{loop}",
                arm=arm,
                state="complete",
                candidates=[
                    {"name": c.name, "smiles": c.smiles, "score": c.score, "uncertainty": c.uncertainty, "n": c.n_poses}
                    for c in scored
                ],
                batch_best=batch_best,
                budget_spent_cpu_h=spent,
                core_record_sha256=sha,
            )
        )
        write_manifest(loop_dir, manifest)
        history.extend(scored)
        for c in scored:
            seen.add(c.smiles)

    if manifest.stopped is None:
        manifest.stopped = {"reason": "iterations", "detail": f"ran the requested {iterations} iteration(s)"}
    write_manifest(loop_dir, manifest)
    return manifest


# ---- CLI --------------------------------------------------------------------------------------------------------


def _bool(text: str) -> bool:
    if text.lower() in ("true", "1", "yes"):
        return True
    if text.lower() in ("false", "0", "no"):
        return False
    raise argparse.ArgumentTypeError(f"expected true or false, got {text!r}")


def al_main(argv: list[str] | None = None, env: Mapping[str, str] | None = None) -> int:
    """`smeltery al run ...`: drive the active-learning loop. 0 ran, 2 a bad argument or a refusal."""
    from .settings import SettingsError, resolve

    ap = argparse.ArgumentParser(prog="smeltery al", description="Active-learning loop over the funnel.")
    sub = ap.add_subparsers(dest="what", required=True)
    run = sub.add_parser("run", help="run a loop: propose, score, record, repeat (docs/active-learning.md)")
    run.add_argument("--loop", required=True, help="loop name; the attestation family is al:<loop>")
    run.add_argument("--target", required=True, help="a .pdb/.pqr pocket or a .toml target definition")
    run.add_argument(
        "--reference-smiles", required=True, help="the reference molecule every analogue is scored against"
    )
    run.add_argument("--reference-name", default="reference")
    run.add_argument("--proposer", default=None, help="module:object of a Proposer (propose(history, n) -> [SMILES])")
    run.add_argument("--pool", default=None, help="a file of SMILES (one per line) for the built-in PoolProposer")
    run.add_argument("--seed", type=int, default=0, help="seed for the --pool proposer (PoolProposer or acquisition)")
    run.add_argument(
        "--acquisition",
        choices=("lcb", "ei", "random"),
        default=None,
        help="with --pool: choose by an uncertainty-aware GP (smeltery.al_proposers.UncertaintyProposer) using lower "
        "confidence bound or expected improvement; 'random' is that proposer without a model. Default: PoolProposer",
    )
    run.add_argument("--kappa", type=float, default=1.0, help="LCB exploration weight (with --acquisition lcb)")
    run.add_argument("--iterations", type=int, required=True)
    run.add_argument("--batch", type=int, required=True, help="candidates proposed per iteration")
    run.add_argument("--budget", type=float, default=None, help="total predicted CPU-hours for the whole loop")
    run.add_argument("--out", required=True, help="loop directory; holds al-manifest.json and iter-NNN/ run dirs")
    run.add_argument("--target-class", default=None, help="docs/mcp.md target class (default protein-pocket-rhf)")
    run.add_argument("--keep", type=int, default=None, help="cut survivors per screen (scoring is independent of it)")
    run.add_argument("--relax-unmapped", type=_bool, default=None)
    run.add_argument("--confirm", action="store_true", help="submit even over a configured SMELTERY_MAX_* ceiling")
    args = ap.parse_args(argv)
    env = os.environ if env is None else env

    if args.proposer and args.pool:
        print("smeltery al: pass either --proposer or --pool, not both", file=sys.stderr)
        return 2
    if args.acquisition and not args.pool:
        print("smeltery al: --acquisition needs --pool FILE (the candidates to choose among)", file=sys.stderr)
        return 2
    try:
        if args.proposer:
            proposer: Proposer = load_proposer(args.proposer)
            proposer_ref = args.proposer
        elif args.pool:
            pool = [line.strip() for line in Path(args.pool).read_text().splitlines() if line.strip()]
            if args.acquisition:
                from .al_proposers import UncertaintyProposer

                proposer = UncertaintyProposer(
                    pool,
                    seed=args.seed,
                    acquisition=args.acquisition,
                    kappa=args.kappa,
                    exclude=[args.reference_smiles],
                )
                proposer_ref = "smeltery.al_proposers:UncertaintyProposer"
            else:
                proposer = PoolProposer(pool, seed=args.seed)
                proposer_ref = None
        else:
            print("smeltery al: a proposer is required (--proposer module:obj or --pool FILE)", file=sys.stderr)
            return 2
        settings = resolve(env)
        manifest = run_loop(
            loop=args.loop,
            target=args.target,
            reference_smiles=args.reference_smiles,
            reference_name=args.reference_name,
            proposer=proposer,
            proposer_ref=proposer_ref,
            iterations=args.iterations,
            batch=args.batch,
            settings=settings,
            out_dir=args.out,
            budget_cpu_h=args.budget,
            target_class=args.target_class,
            keep=args.keep,
            relax_unmapped=args.relax_unmapped,
            confirm=args.confirm,
        )
    except (AlError, SettingsError, OSError) as e:
        print(f"smeltery al: {e}", file=sys.stderr)
        return 2
    done = [it for it in manifest.iterations if it.state == "complete"]
    print(f"loop {args.loop}: {len(done)} iteration(s) complete; manifest {Path(args.out) / MANIFEST}")
    if manifest.stopped:
        print(f"stopped: {manifest.stopped['reason']} -- {manifest.stopped['detail']}")
    for it in done:
        best = "n/a" if it.batch_best is None else f"{it.batch_best:.3g}"
        print(f"  {it.arm}: {len(it.candidates)} scored, batch best {best}")
    return 0


if __name__ == "__main__":
    sys.exit(al_main())
