"""A proposal: a chemist's request for a candidate that does not exist yet, stored for review. It never launches."""

from __future__ import annotations

from smeltery.decisions import TIME_FORMAT
from smeltery.project import Proposal, make_entry_id


class ProposalRefused(ValueError):
    """The proposal was not stored: `Proposal.problems()` named what is wrong with it."""


class ProposeCandidate:
    def __init__(self, project_store, identity, clock):
        self.project_store = project_store
        self.identity = identity
        self.clock = clock

    def run(self, digest: str, smiles: str, reason: str, note: str = "", candidate: str = "") -> Proposal:
        """Build, validate and store a proposal on the run whose input digest is `digest`, by `Identity.current()`
        at `Clock.now()`. Its id is derived from the run, the SMILES and that time. This never touches the task
        queue: a comment must not start compute."""
        now = self.clock.now().strftime(TIME_FORMAT)
        p = Proposal(
            id=make_entry_id(digest, smiles, now),
            run=digest,
            smiles=smiles,
            reason=reason,
            note=note,
            candidate=candidate,
            author=self.identity.current(),
            created_at=now,
        )
        bad = p.problems()
        if bad:
            raise ProposalRefused("; ".join(bad))
        self.project_store.add_proposal(p)
        return p
