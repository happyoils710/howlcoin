"""Oracle data integrity: feed freshness, agent settlements, price consistency."""

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
        headers={"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; oracle)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class OracleMonitor(Monitor):
    role = "oracle"

    def __init__(self, agent_id: str, ctx: Dict[str, Any]):
        super().__init__(agent_id, ctx)
        self._last_keys: Dict[str, str] = {}

    def tick(self) -> List[Finding]:
        api = self.ctx["api_base"].rstrip("/")
        out: List[Finding] = []
        feed: List[Dict[str, Any]] = []
        try:
            j = _get(f"{api}/api/public/oracle?limit=80")
            feed = list(j.get("feed") or [])
        except Exception as e:
            # fallback: recent txs filtered client-side
            try:
                txs = _get(f"{api}/api/public/txs?limit=50")
                for t in txs.get("txs") or txs.get("transactions") or []:
                    if (t.get("type") or "") == "oracle":
                        feed.append(
                            {
                                "key": t.get("oracle_key"),
                                "value": t.get("oracle_value"),
                                "txid": t.get("txid"),
                                "height": t.get("height"),
                                "reporter": t.get("from"),
                            }
                        )
            except Exception as e2:
                return [
                    Finding(
                        self.agent_id,
                        self.role,
                        "oracle_api_error",
                        Severity.MEDIUM.value,
                        f"Oracle feed unreachable: {e}; txs fallback: {e2}",
                        {"error": str(e)},
                    )
                ]

        if not feed:
            out.append(
                Finding(
                    self.agent_id,
                    self.role,
                    "oracle_empty",
                    Severity.LOW.value,
                    "No oracle entries on chain yet",
                )
            )
            return out

        agent_settlements = 0
        stale_agent = 0
        now = time.time()
        for row in feed:
            key = str(row.get("key") or row.get("oracle_key") or "")
            val = str(row.get("value") or row.get("oracle_value") or "")
            if key.startswith("howl.agent."):
                agent_settlements += 1
            # detect flip-flops on same key within short window (simple hash of value)
            prev = self._last_keys.get(key)
            if prev is not None and prev != val and key.startswith("howl.price."):
                out.append(
                    Finding(
                        self.agent_id,
                        self.role,
                        "oracle_price_flip",
                        Severity.MEDIUM.value,
                        f"Price oracle key changed: {key[:60]}",
                        {"key": key, "prev_len": len(prev), "new_len": len(val)},
                    )
                )
            if key:
                self._last_keys[key] = val

            # age heuristic when timestamp present
            ts = row.get("timestamp") or row.get("observed_at") or row.get("ts")
            if ts is not None and key.startswith("howl.agent."):
                try:
                    age = now - float(ts)
                    if age > 86400 * 7:
                        stale_agent += 1
                except (TypeError, ValueError):
                    pass

        out.append(
            Finding(
                self.agent_id,
                self.role,
                "oracle_feed_ok",
                Severity.INFO.value,
                f"Oracle feed has {len(feed)} keys ({agent_settlements} agent settlements)",
                {
                    "count": len(feed),
                    "agent_settlements": agent_settlements,
                    "sample_keys": [
                        str(r.get("key") or r.get("oracle_key") or "")[:80]
                        for r in feed[:8]
                    ],
                },
            )
        )
        if stale_agent:
            out.append(
                Finding(
                    self.agent_id,
                    self.role,
                    "oracle_agent_stale",
                    Severity.LOW.value,
                    f"{stale_agent} agent oracle rows older than 7d",
                    {"stale_count": stale_agent},
                )
            )

        # Cross-check markets/prices API when present
        try:
            prices = _get(f"{api}/api/public/prices")
            howl = None
            if isinstance(prices, dict):
                howl = (
                    prices.get("howl")
                    or prices.get("HOWL")
                    or (prices.get("prices") or {}).get("howlcoin")
                )
            if howl is not None:
                try:
                    px = float(howl if not isinstance(howl, dict) else howl.get("usd") or howl.get("price") or 0)
                    if px < 0:
                        out.append(
                            Finding(
                                self.agent_id,
                                self.role,
                                "price_negative",
                                Severity.HIGH.value,
                                "Negative HOWL price from public API",
                                {"price": px},
                            )
                        )
                    elif px == 0:
                        out.append(
                            Finding(
                                self.agent_id,
                                self.role,
                                "price_zero",
                                Severity.LOW.value,
                                "HOWL price reports as zero",
                                {"price": px},
                            )
                        )
                except (TypeError, ValueError):
                    pass
        except Exception:
            pass

        return out
