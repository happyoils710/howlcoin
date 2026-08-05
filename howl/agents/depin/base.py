"""DePIN / decentralized compute provider interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class ComputeQuote:
    provider: str
    region: str
    cpu: float
    memory_gb: float
    storage_gb: float
    cost_howl_per_day: float
    available: bool = True
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DeployJob:
    job_id: str
    provider: str
    status: str  # pending | running | failed | dry_run | stopped
    endpoint: Optional[str] = None
    cost_howl: float = 0.0
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ComputeProvider(ABC):
    name: str = "base"

    @abstractmethod
    def quote(self, *, cpu: float = 1.0, memory_gb: float = 2.0, storage_gb: float = 20.0) -> ComputeQuote:
        ...

    @abstractmethod
    def deploy_node(
        self,
        *,
        node_id: str,
        seed: str,
        p2p_port: int = 42069,
        rpc_port: int = 42070,
        auto_mine: bool = False,
        dry_run: bool = True,
    ) -> DeployJob:
        ...

    @abstractmethod
    def status(self, job_id: str) -> DeployJob:
        ...

    def list_jobs(self) -> List[DeployJob]:
        return []
