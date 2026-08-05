"""Security events: mint authority, wrap orphans, bridge errors, hot wallet signals."""

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
        headers={"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; security)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


class SecurityMonitor(Monitor):
    role = "security"

    def tick(self) -> List[Finding]:
        api = self.ctx["api_base"].rstrip("/")
        out: List[Finding] = []
        # Public security posture
        try:
            sec = _get(f"{api}/api/public/security")
            if sec.get("audit_status") == "none_published":
                out.append(
                    Finding(
                        self.agent_id, self.role, "no_external_audit",
                        Severity.INFO.value,
                        "No formal external audit published yet",
                        {"audit_status": sec.get("audit_status"), "semi_custodial": sec.get("semi_custodial")},
                    )
                )
            wh = sec.get("whowl") or {}
            if wh.get("freeze_authority") not in (None, "", "null"):
                out.append(
                    Finding(
                        self.agent_id, self.role, "freeze_authority_set",
                        Severity.HIGH.value,
                        "wHOWL freeze authority is set — unexpected for current policy",
                        wh,
                    )
                )
            else:
                out.append(
                    Finding(
                        self.agent_id, self.role, "freeze_authority_clear",
                        Severity.INFO.value,
                        "wHOWL freeze authority not set",
                        wh,
                    )
                )
        except Exception as e:
            out.append(
                Finding(
                    self.agent_id, self.role, "security_api_error",
                    Severity.LOW.value,
                    f"security API: {e}",
                    {"error": str(e)},
                )
            )
        # Wrap / bridge surface
        try:
            w = _get(f"{api}/api/public/wrap")
            if w.get("enabled") and not w.get("mint"):
                out.append(
                    Finding(
                        self.agent_id, self.role, "wrap_misconfig",
                        Severity.HIGH.value,
                        "Wrap enabled but mint missing",
                        w,
                    )
                )
        except Exception:
            pass
        try:
            b = _get(f"{api}/api/public/bridge")
            if b.get("enabled") and not (b.get("assets") or []):
                out.append(
                    Finding(
                        self.agent_id, self.role, "bridge_misconfig",
                        Severity.MEDIUM.value,
                        "Bridge enabled without assets",
                        {"enabled": b.get("enabled")},
                    )
                )
        except Exception:
            pass
        if not out:
            out.append(
                Finding(
                    self.agent_id, self.role, "security_scan_ok",
                    Severity.INFO.value,
                    "Security monitors completed without critical hits",
                )
            )
        return out
