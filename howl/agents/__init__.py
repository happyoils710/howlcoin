"""Howl autonomous multi-agent system.

Agents monitor Howl L1 (+ optional external chains), coordinate consensus,
settle findings on-chain via oracle txs, and can request infrastructure
(node bootstrap) through DePIN / compute adapters.
"""

from .runtime import AgentRuntime, run_runtime
from .types import AgentRole, Finding, Proposal, Vote, ConsensusResult

__all__ = [
    "AgentRuntime",
    "run_runtime",
    "AgentRole",
    "Finding",
    "Proposal",
    "Vote",
    "ConsensusResult",
]
