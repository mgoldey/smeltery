from __future__ import annotations

from smeltery.project import Proposal, utc_now


class ProposeCandidate:
    def __init__(self, project_store, identity, clock):
        self.project_store = project_store
        self.identity = identity
        self.clock = clock

    def run(self, *, campaign_id, run, candidate, smiles, reason, note=""):
        from smeltery.project import make_entry_id

        p = Proposal(
            id=make_entry_id(campaign_id, candidate, utc_now()),
            run=run,
            candidate=candidate,
            smiles=smiles,
            reason=reason,
            note=note,
            user=self.identity.current(),
            time=utc_now(),
            status="pending",
        )
        p.validate()
        # store as a notebook entry referencing the run digest if possible
        return p

    def execute(self, *a, **kw):
        return self.run(*a, **kw)

    __call__ = run
