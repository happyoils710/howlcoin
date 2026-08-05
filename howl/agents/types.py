from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class AgentRole(str, Enum):
    HEALTH = "health"
    SECURITY = "security"
    ORACLE = "oracle"
    OPPORTUNITY = "opportunity"
    INFRA = "infra"
    COORDINATOR = "coordinator"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class Finding:
    agent_id: str
    role: str
    kind: str
    severity: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    finding_id: str = field(default_factory=lambda: "f-" + uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    """Proposal for multi-agent consensus (action or report)."""
    proposer: str
    action: str  # report | alert | bootstrap_node | scale_nodes | spend | settle
    payload: Dict[str, Any]
    finding_ids: List[str] = field(default_factory=list)
    required_votes: int = 2
    ts: float = field(default_factory=time.time)
    proposal_id: str = field(default_factory=lambda: "p-" + uuid.uuid4().hex[:12])

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class Vote:
    proposal_id: str
    voter: str
    approve: bool
    reason: str = ""
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ConsensusResult:
    proposal_id: str
    approved: bool
    yes: int
    no: int
    votes: List[Dict[str, Any]]
    settled_txid: Optional[str] = None
    infra_job_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
