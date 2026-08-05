"""Protocol health: tip age, height growth, mempool, peer connectivity."""

from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Dict, List

from ..types import Finding, Severity
from .base import Monitor


def _get(url: str, timeout: int = 12) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; health)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class HealthMonitor(Monitor):
    role = "health"

    def __init__(self, agent_id: str, ctx: Dict[str, Any]):
        super().__init__(agent_id, ctx)
        self._last_height = None
        self._last_check = 0.0

    def tick(self) -> List[Finding]:
        api = self.ctx["api_base"].rstrip("/")
        out: List[Finding] = []
        try:
            s = _get(f"{api}/api/public/summary")
        except Exception as e:
            return [
                Finding(
                    agent_id=self.agent_id,
                    role=self.role,
                    kind="api_unreachable",
                    severity=Severity.CRITICAL.value,
                    summary=f"Howl API unreachable: {e}",
                    details={"error": str(e)},
                )
            ]
        height = s.get("height")
        tip_age = s.get("tip_age_seconds")
        mempool = s.get("mempool")
        online = s.get("online", True)
        details = {
            "height": height,
            "tip_age_seconds": tip_age,
            "mempool": mempool,
            "difficulty_label": s.get("difficulty_label"),
            "version": s.get("version"),
        }
        if not online:
            out.append(
                Finding(
                    self.agent_id, self.role, "network_offline", Severity.HIGH.value,
                    "Public chain marked offline", details,
                )
            )
        if tip_age is not None and float(tip_age) > 7200:
            out.append(
                Finding(
                    self.agent_id, self.role, "tip_stale", Severity.HIGH.value,
                    f"Tip age {tip_age}s exceeds 2h stall threshold", details,
                )
            )
        elif tip_age is not None and float(tip_age) > 600:
            out.append(
                Finding(
                    self.agent_id, self.role, "tip_slow", Severity.MEDIUM.value,
                    f"Tip age {tip_age}s — blocks slower than target", details,
                )
            )
        else:
            out.append(
                Finding(
                    self.agent_id, self.role, "health_ok", Severity.INFO.value,
                    f"Protocol healthy at height {height}", details,
                )
            )
        # height stall detection across ticks
        now = time.time()
        if self._last_height is not None and height is not None:
            if int(height) == int(self._last_height) and (now - self._last_check) > 300:
                out.append(
                    Finding(
                        self.agent_id, self.role, "height_stall", Severity.MEDIUM.value,
                        f"Height unchanged at {height} for >5m of agent observation",
                        details,
                    )
                )
        if height is not None:
            self._last_height = int(height)
        self._last_check = now
        # optional peer/status
        try:
            st = _get(f"{api}/api/public/status?window=12")
            details["status"] = {k: st.get(k) for k in ("avg_block_time", "status", "addresses") if k in st}
        except Exception:
            pass
        return out
