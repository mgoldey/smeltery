from __future__ import annotations

from pathlib import Path

from smeltery.decisions import DecisionSet


class RecordDecision:
    def __init__(self, run_store, identity, clock):
        self.run_store = run_store
        self.identity = identity
        self.clock = clock

    def run(self, run_path, candidate, status, reason, note="", *, smiles=None, decisions_path=None):
        run = self.run_store.read_run(run_path)
        # load or create
        dp = (
            Path(decisions_path)
            if decisions_path
            else Path(run_path).parent / f"decisions-{run.record['input_digest'][:12]}.json"
        )
        if dp.exists():
            ds = DecisionSet.load(dp)
        else:
            ds = DecisionSet(campaign=run.campaign, run=run.record["input_digest"], candidates=dict(run.candidates))
        d = ds.set(candidate, status, reason, note, user=self.identity.current(), smiles=smiles)
        # stamp time already in set via utc_now; ensure user
        ds.dump(dp)
        return d

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
