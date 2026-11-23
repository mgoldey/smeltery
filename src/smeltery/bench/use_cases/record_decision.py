"""Recording keep / reject / hold decisions on a run: Bench's one home for them.

Every rule on a decision is `smeltery.decisions.DecisionSet`'s; this adds only that a decision names a candidate of
the run it is made on, and that the user is `Identity.current()`. The file is `OUT/decisions-<digest[:12]>.json`
(`smeltery.decisions.decisions_path`), beside the run record.
"""

from __future__ import annotations

from pathlib import Path

from smeltery.decisions import Decision, DecisionSet
from smeltery.decisions import decisions_path as _path


class DecisionRefused(ValueError):
    """The decision was not recorded: an unknown candidate, or one `DecisionSet` refuses. Nothing was written.

    A decisions file already on disk that does not validate is not this: it is `DecisionSet.load`'s plain ValueError,
    since the request did nothing wrong."""


def decisions_path(run) -> Path:
    """Where `run`'s decisions are kept: `run.out_dir/decisions-<input_digest[:12]>.json`."""
    return _path(run.out_dir, run.record["input_digest"])


def load(run) -> DecisionSet:
    """The decisions saved for `run`, else an empty set; either way knowing the run's candidates, so a candidate
    with no decision yet can be decided."""
    return DecisionSet.load_or_new(
        decisions_path(run), campaign=run.campaign, run=run.record["input_digest"], candidates=run.candidates
    )


class RecordDecision:
    def __init__(self, run_store, identity, clock):
        self.run_store = run_store
        self.identity = identity
        self.clock = clock

    def set(
        self, run, candidate: str, status: str, reason: str, note: str = "", *, smiles: str | None = None
    ) -> Decision:
        """Decide one candidate of `run` and save the file."""
        ds = load(run)
        if candidate not in run.candidates:
            raise DecisionRefused(f"no candidate {candidate!r} in this run")
        try:
            d = ds.set(candidate, status, reason, note, user=self.identity.current(), smiles=smiles)
            ds.dump(decisions_path(run))
        except ValueError as e:
            raise DecisionRefused(str(e)) from e
        return d

    def set_many(self, run, candidates: list[str], status: str, reason: str, note: str = "") -> list[Decision]:
        """One decision on many candidates, all or nothing: an unknown candidate or any invalid decision refuses the
        lot, naming every problem, and the file is written once."""
        unknown = [c for c in candidates if c not in run.candidates]
        if unknown:
            raise DecisionRefused(f"no candidate {', '.join(map(repr, unknown))} in this run")
        ds = load(run)
        try:
            done = ds.set_many(candidates, status, reason, note, user=self.identity.current())
            ds.dump(decisions_path(run))
        except ValueError as e:
            raise DecisionRefused(str(e)) from e
        return done

    def run(self, run_path, candidate, status, reason, note="", *, smiles=None) -> Decision:
        """`set` on the run at `run_path` (its directory or its run-*.json)."""
        return self.set(self.run_store.read_run(run_path), candidate, status, reason, note, smiles=smiles)

    def execute(self, *a, **kw):
        return self.run(*a, **kw)
