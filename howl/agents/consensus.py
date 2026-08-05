"""Lightweight multi-agent consensus (quorum voting)."""

from __future__ import annotations

from typing import Dict, List, Optional

from .types import ConsensusResult, Proposal, Vote


class Council:
    """In-memory council; state also persisted by runtime for restarts."""

    def __init__(self) -> None:
        self.proposals: Dict[str, Proposal] = {}
        self.votes: Dict[str, List[Vote]] = {}

    def submit(self, proposal: Proposal) -> Proposal:
        self.proposals[proposal.proposal_id] = proposal
        self.votes.setdefault(proposal.proposal_id, [])
        return proposal

    def vote(self, vote: Vote) -> Optional[ConsensusResult]:
        if vote.proposal_id not in self.proposals:
            return None
        bucket = self.votes.setdefault(vote.proposal_id, [])
        # one vote per voter
        bucket[:] = [v for v in bucket if v.voter != vote.voter]
        bucket.append(vote)
        return self.tally(vote.proposal_id)

    def tally(self, proposal_id: str) -> Optional[ConsensusResult]:
        prop = self.proposals.get(proposal_id)
        if not prop:
            return None
        votes = self.votes.get(proposal_id) or []
        yes = sum(1 for v in votes if v.approve)
        no = sum(1 for v in votes if not v.approve)
        approved = yes >= int(prop.required_votes) and yes > no
        return ConsensusResult(
            proposal_id=proposal_id,
            approved=approved,
            yes=yes,
            no=no,
            votes=[v.to_dict() for v in votes],
        )
