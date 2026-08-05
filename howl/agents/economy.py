"""Economic autonomy for agents — budget, spend limits, ledger of actions."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class AgentTreasury:
    """Tracks HOWL budget the agent swarm may spend (fees, infra quotes)."""

    address: str = ""
    # soft budget in HOWL (not howlies) for planned spend
    budget_howl: float = 100.0
    spent_howl: float = 0.0
    max_tx_howl: float = 25.0
    min_reserve_howl: float = 5.0
    history: List[Dict[str, Any]] = field(default_factory=list)

    def can_spend(self, amount_howl: float) -> bool:
        if amount_howl <= 0:
            return False
        if amount_howl > self.max_tx_howl:
            return False
        remaining = self.budget_howl - self.spent_howl
        return remaining - amount_howl >= self.min_reserve_howl

    def record(self, amount_howl: float, kind: str, meta: Optional[Dict[str, Any]] = None) -> None:
        self.spent_howl += float(amount_howl)
        self.history.append(
            {
                "ts": time.time(),
                "amount_howl": amount_howl,
                "kind": kind,
                "meta": meta or {},
            }
        )
        if len(self.history) > 500:
            self.history = self.history[-500:]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "address": self.address,
            "budget_howl": self.budget_howl,
            "spent_howl": self.spent_howl,
            "max_tx_howl": self.max_tx_howl,
            "min_reserve_howl": self.min_reserve_howl,
            "remaining_howl": max(0.0, self.budget_howl - self.spent_howl),
            "history_tail": self.history[-20:],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "AgentTreasury":
        t = cls(
            address=str(d.get("address") or ""),
            budget_howl=float(d.get("budget_howl") or 100),
            spent_howl=float(d.get("spent_howl") or 0),
            max_tx_howl=float(d.get("max_tx_howl") or 25),
            min_reserve_howl=float(d.get("min_reserve_howl") or 5),
        )
        t.history = list(d.get("history") or [])[-500:]
        return t


def load_treasury(path: Path) -> AgentTreasury:
    if path.is_file():
        try:
            return AgentTreasury.from_dict(json.loads(path.read_text()))
        except Exception:
            pass
    return AgentTreasury()


def save_treasury(path: Path, treasury: AgentTreasury) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(treasury.to_dict(), indent=2))
