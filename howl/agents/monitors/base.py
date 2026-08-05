from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from ..types import Finding


class Monitor(ABC):
    role: str = "base"

    def __init__(self, agent_id: str, ctx: Dict[str, Any]):
        self.agent_id = agent_id
        self.ctx = ctx

    @abstractmethod
    def tick(self) -> List[Finding]:
        ...
