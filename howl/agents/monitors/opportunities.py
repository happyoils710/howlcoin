"""Opportunity scanner: mempool pressure, wrap backlog, peer gaps, fee windows."""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Dict, List

from ..types import Finding, Severity
from .base import Monitor


def _get(url: str, timeout: int = 12) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; opportunity)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class OpportunityMonitor(Monitor):
    role = "opportunity"

    def tick(self) -> List[Finding]:
        api = self.ctx["api_base"].rstrip("/")
        out: List[Finding] = []

        # Mempool — pending inclusion = fee/priority opportunity for miners
        try:
            mp = _get(f"{api}/api/public/mempool")
            items = mp.get("mempool") or mp.get("txs") or mp.get("transactions") or []
            if isinstance(items, dict):
                items = list(items.values())
            n = len(items) if isinstance(items, list) else int(mp.get("count") or 0)
            if n >= 20:
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "mempool_congestion",
                        Severity.MEDIUM.value,
                        f"Mempool has {n} pending txs — mine / scale hashrate opportunity",
                        {"count": n, "action_hint": "bootstrap_node"},
                    )
                )
            elif n >= 5:
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "mempool_active",
                        Severity.LOW.value,
                        f"Mempool active ({n} txs)",
                        {"count": n},
                    )
                )
            else:
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "mempool_quiet",
                        Severity.INFO.value,
                        f"Mempool quiet ({n} txs)",
                        {"count": n},
                    )
                )
        except Exception as e:
            out.append(
                Finding(
                    self.agent_id,
                    self.role,
                    "mempool_error",
                    Severity.LOW.value,
                    f"mempool: {e}",
                    {"error": str(e)},
                )
            )

        # Wrap orders backlog (if API exposes)
        try:
            w = _get(f"{api}/api/public/wrap")
            pending = w.get("pending_orders") or w.get("pending") or w.get("queue_depth")
            if pending is not None and int(pending) > 0:
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "wrap_backlog",
                        Severity.MEDIUM.value,
                        f"Wrap queue depth {pending} — relayer / liquidity opportunity",
                        {"pending": int(pending), "enabled": w.get("enabled")},
                    )
                )
            elif w.get("enabled"):
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "wrap_idle",
                        Severity.INFO.value,
                        "Wrap enabled; no reported backlog",
                        {"mint": w.get("mint")},
                    )
                )
        except Exception:
            pass

        # Peer / network surface — low peer count → bootstrap more seeds
        try:
            s = _get(f"{api}/api/public/summary")
            peers = s.get("peers") or s.get("peer_count")
            height = s.get("height")
            tip_age = s.get("tip_age_seconds")
            if peers is not None and int(peers) < 2:
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "low_peer_count",
                        Severity.HIGH.value,
                        f"Only {peers} peer(s) — bootstrap nodes for decentralization",
                        {
                            "peers": peers,
                            "height": height,
                            "action_hint": "bootstrap_node",
                            "suggested_count": 2,
                        },
                    )
                )
            if tip_age is not None and float(tip_age) > 900:
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "slow_blocks_opportunity",
                        Severity.MEDIUM.value,
                        "Slow blocks — more miners / nodes can earn subsidy + fees",
                        {
                            "tip_age_seconds": tip_age,
                            "height": height,
                            "action_hint": "bootstrap_node",
                        },
                    )
                )
        except Exception:
            pass

        # Richlist concentration (decentralization signal)
        try:
            rl = _get(f"{api}/api/public/richlist?limit=5")
            rows = rl.get("richlist") or rl.get("addresses") or rl.get("top") or []
            if isinstance(rows, list) and rows:
                top = rows[0]
                bal = top.get("balance_howl") or top.get("balance") or top.get("howl")
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "richlist_snapshot",
                        Severity.INFO.value,
                        "Top holder snapshot for concentration watch",
                        {
                            "top_address": (top.get("address") or "")[:20],
                            "top_balance": bal,
                            "top_n": len(rows),
                        },
                    )
                )
        except Exception:
            pass

        if not out:
            out.append(
                Finding(
                    self.agent_id,
                    self.role,
                    "no_opportunity",
                    Severity.INFO.value,
                    "No actionable opportunities this tick",
                )
            )
        return out
