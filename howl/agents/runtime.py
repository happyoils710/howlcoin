"""Multi-agent runtime: monitor → propose → vote → settle → act."""

from __future__ import annotations

import json
import os
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from .consensus import Council
from .economy import AgentTreasury, load_treasury, save_treasury
from .infrastructure import InfraGovernor
from .monitors import (
    HealthMonitor,
    OpportunityMonitor,
    OracleMonitor,
    SecurityMonitor,
)
from .settlement import settle_consensus, settle_finding
from .types import Finding, Proposal, Severity, Vote


SEVERITY_RANK = {
    Severity.INFO.value: 0,
    Severity.LOW.value: 1,
    Severity.MEDIUM.value: 2,
    Severity.HIGH.value: 3,
    Severity.CRITICAL.value: 4,
}


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


class AgentRuntime:
    """
    Closed-loop multi-agent system:

      1. Role agents tick monitors (health, security, oracle, opportunity)
      2. Coordinator turns notable findings into proposals
      3. Council votes to consensus
      4. Settlement posts results on-chain as oracle txs
      5. Infra governor bootstraps nodes via DePIN when approved
      6. Treasury enforces economic autonomy limits
    """

    def __init__(
        self,
        *,
        api_base: str = "https://howlscan.org",
        state_dir: Optional[Path] = None,
        wallet_path: Optional[Path] = None,
        data_dir: Optional[Path] = None,
        seed: str = "147.182.223.204:42069",
        settle: bool = False,
        dry_run_infra: bool = True,
        required_votes: int = 2,
        settle_min_severity: str = "high",
        interval: float = 60.0,
        howl_root: Optional[Path] = None,
    ):
        self.api_base = api_base.rstrip("/")
        self.state_dir = Path(state_dir or (Path.home() / ".howlcoin" / "agents"))
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.wallet_path = Path(wallet_path) if wallet_path else None
        self.data_dir = Path(data_dir) if data_dir else None
        self.settle = settle and self.wallet_path is not None
        self.required_votes = max(1, int(required_votes))
        self.settle_min_severity = settle_min_severity
        self.interval = float(interval)
        self.council = Council()
        self.treasury = load_treasury(self.state_dir / "treasury.json")
        seeds_path = None
        if Path("/var/lib/howlcoin").is_dir():
            seeds_path = Path("/var/lib/howlcoin/public_seeds.json")
        elif _env("HOWL_SEEDS_FILE"):
            seeds_path = Path(_env("HOWL_SEEDS_FILE"))
        else:
            seeds_path = self.state_dir / "public_seeds.json"
        self.infra = InfraGovernor(
            self.state_dir / "infra",
            seed=seed,
            howl_root=howl_root,
            dry_run=dry_run_infra,
            seeds_registry_path=seeds_path,
        )
        ctx = {"api_base": self.api_base, "state_dir": str(self.state_dir)}
        self.agents = {
            "health-1": HealthMonitor("health-1", ctx),
            "security-1": SecurityMonitor("security-1", ctx),
            "oracle-1": OracleMonitor("oracle-1", ctx),
            "opportunity-1": OpportunityMonitor("opportunity-1", ctx),
        }
        self.findings: List[Dict[str, Any]] = []
        self.results: List[Dict[str, Any]] = []
        self.tick_count = 0
        self._load_history()
        # Always keep primary seed in the public directory
        try:
            self.infra.publish_seed_directory()
        except Exception:
            pass

    def _load_history(self) -> None:
        p = self.state_dir / "history.json"
        if p.is_file():
            try:
                h = json.loads(p.read_text())
                self.findings = list(h.get("findings") or [])[-200:]
                self.results = list(h.get("results") or [])[-100:]
                self.tick_count = int(h.get("tick_count") or 0)
            except Exception:
                pass

    def _save_history(self) -> None:
        (self.state_dir / "history.json").write_text(
            json.dumps(
                {
                    "tick_count": self.tick_count,
                    "findings": self.findings[-200:],
                    "results": self.results[-100:],
                    "updated_at": time.time(),
                },
                indent=2,
            )
        )
        save_treasury(self.state_dir / "treasury.json", self.treasury)
        # Public-ish status snapshot for explorer / ops
        (self.state_dir / "status.json").write_text(
            json.dumps(self.status(), indent=2)
        )

    def status(self) -> Dict[str, Any]:
        seeds_summary: Dict[str, Any] = {}
        try:
            from ..seeds import list_seeds

            sd = list_seeds(probe=False)
            seeds_summary = {
                "primary": sd.get("primary"),
                "count": sd.get("count"),
                "endpoints": [s.get("endpoint") for s in (sd.get("seeds") or [])],
                "registry_files": sd.get("registry_files"),
            }
        except Exception as e:
            seeds_summary = {"error": str(e)}
        return {
            "system": "howl-agents/v1",
            "api_base": self.api_base,
            "tick_count": self.tick_count,
            "agents": list(self.agents.keys()),
            "settle_enabled": self.settle,
            "wallet": str(self.wallet_path) if self.wallet_path else None,
            "treasury": self.treasury.to_dict(),
            "infra": self.infra.inventory(),
            "public_seeds": seeds_summary,
            "recent_findings": self.findings[-15:],
            "recent_results": self.results[-10:],
            "open_proposals": len(self.council.proposals),
        }

    def _rank(self, sev: str) -> int:
        return SEVERITY_RANK.get(str(sev).lower(), 0)

    def _should_propose(self, f: Finding) -> bool:
        return self._rank(f.severity) >= self._rank(Severity.MEDIUM.value)

    def _action_for(self, f: Finding) -> str:
        hint = (f.details or {}).get("action_hint") or ""
        if hint == "bootstrap_node" or f.kind in (
            "low_peer_count",
            "mempool_congestion",
            "tip_stale",
            "height_stall",
            "slow_blocks_opportunity",
            "network_offline",
        ):
            return "bootstrap_node"
        if self._rank(f.severity) >= self._rank(Severity.HIGH.value):
            return "alert"
        return "report"

    def _auto_votes(self, proposal: Proposal, findings: List[Finding]) -> List[Vote]:
        """Role agents vote based on related findings and severity."""
        votes: List[Vote] = []
        related_roles = set()
        for fid in proposal.finding_ids:
            for f in findings:
                if f.finding_id == fid:
                    related_roles.add(f.role)

        action = proposal.action
        # Proposer always yes
        votes.append(
            Vote(
                proposal_id=proposal.proposal_id,
                voter="coordinator",
                approve=True,
                reason="proposer",
            )
        )
        for aid, mon in self.agents.items():
            role = mon.role
            approve = False
            reason = "no_affinity"
            if role in related_roles:
                approve = True
                reason = f"produced_related_finding:{role}"
            elif action == "bootstrap_node" and role in ("health", "opportunity", "infra"):
                approve = True
                reason = "infra_affinity"
            elif action == "alert" and role in ("security", "health", "oracle"):
                approve = True
                reason = "alert_affinity"
            elif action == "report" and role == "oracle":
                approve = True
                reason = "oracle_record"
            elif action == "settle":
                approve = True
                reason = "settle_affinity"
            votes.append(
                Vote(
                    proposal_id=proposal.proposal_id,
                    voter=aid,
                    approve=approve,
                    reason=reason,
                )
            )
        return votes

    def _execute_approved(
        self,
        proposal: Proposal,
        result_dict: Dict[str, Any],
        findings: List[Finding],
    ) -> Dict[str, Any]:
        out: Dict[str, Any] = {"proposal_id": proposal.proposal_id, "action": proposal.action}
        settled_txid = None
        infra_job = None

        if proposal.action == "bootstrap_node":
            count = int((proposal.payload or {}).get("count") or 1)
            reason = str((proposal.payload or {}).get("reason") or proposal.action)
            res = self.infra.execute_bootstrap(
                treasury=self.treasury,
                count=count,
                reason=reason,
                auto_mine=bool((proposal.payload or {}).get("auto_mine")),
            )
            out["infra"] = res
            if res.get("jobs"):
                infra_job = res["jobs"][0].get("job_id")
            out["infra_job_id"] = infra_job

        # On-chain settlement for medium+ when enabled
        if self.settle and self.wallet_path:
            try:
                # Fee is 1 HOWL min — only settle meaningful proposals
                sev = str((proposal.payload or {}).get("severity") or "medium")
                if self._rank(sev) >= self._rank(self.settle_min_severity):
                    fee_howl = 1.0
                    if self.treasury.can_spend(fee_howl):
                        key, txid = settle_consensus(
                            wallet_path=self.wallet_path,
                            api_base=self.api_base,
                            proposal=proposal.to_dict(),
                            result=result_dict,
                            data_dir=self.data_dir,
                        )
                        settled_txid = txid
                        self.treasury.record(
                            fee_howl,
                            "settle_consensus",
                            {"oracle_key": key, "txid": txid, "proposal_id": proposal.proposal_id},
                        )
                        out["settled"] = {"oracle_key": key, "txid": txid}
                    else:
                        out["settled"] = {"skipped": "treasury"}
                else:
                    out["settled"] = {"skipped": "severity_below_threshold"}
            except Exception as e:
                out["settled"] = {"error": str(e)}

        # Critical findings can also settle individually
        if self.settle and self.wallet_path:
            for f in findings:
                if f.finding_id not in proposal.finding_ids:
                    continue
                if self._rank(f.severity) < self._rank(Severity.CRITICAL.value):
                    continue
                try:
                    if self.treasury.can_spend(1.0):
                        key, txid = settle_finding(
                            wallet_path=self.wallet_path,
                            api_base=self.api_base,
                            finding=f.to_dict(),
                            data_dir=self.data_dir,
                        )
                        self.treasury.record(1.0, "settle_finding", {"txid": txid, "finding_id": f.finding_id})
                        out.setdefault("finding_settlements", []).append(
                            {"finding_id": f.finding_id, "txid": txid, "key": key}
                        )
                except Exception as e:
                    out.setdefault("finding_settlements", []).append(
                        {"finding_id": f.finding_id, "error": str(e)}
                    )

        out["settled_txid"] = settled_txid
        out["infra_job_id"] = infra_job
        return out

    def tick(self) -> Dict[str, Any]:
        self.tick_count += 1
        all_findings: List[Finding] = []
        errors: List[str] = []

        # Keep public seed directory fresh (primary + fleet)
        try:
            self.infra.publish_seed_directory()
        except Exception as e:
            errors.append(f"seed_registry: {e}")

        for aid, mon in self.agents.items():
            try:
                all_findings.extend(mon.tick())
            except Exception as e:
                errors.append(f"{aid}: {e}")

        for f in all_findings:
            self.findings.append(f.to_dict())
        self.findings = self.findings[-200:]

        proposals_run: List[Dict[str, Any]] = []
        # One proposal per notable finding (cap per tick)
        notable = [f for f in all_findings if self._should_propose(f)]
        # Prefer higher severity
        notable.sort(key=lambda f: -self._rank(f.severity))
        for f in notable[:5]:
            action = self._action_for(f)
            payload: Dict[str, Any] = {
                "finding": f.to_dict(),
                "severity": f.severity,
                "kind": f.kind,
                "reason": f.summary,
            }
            if action == "bootstrap_node":
                count = int((f.details or {}).get("suggested_count") or 1)
                payload.update(self.infra.plan_bootstrap(count=count, reason=f.summary))
            prop = Proposal(
                proposer="coordinator",
                action=action,
                payload=payload,
                finding_ids=[f.finding_id],
                required_votes=self.required_votes,
            )
            self.council.submit(prop)
            final = None
            for v in self._auto_votes(prop, all_findings):
                final = self.council.vote(v)
            if not final:
                continue
            exec_out: Dict[str, Any] = {}
            if final.approved:
                exec_out = self._execute_approved(prop, final.to_dict(), all_findings)
                final.settled_txid = exec_out.get("settled_txid")
                final.infra_job_id = exec_out.get("infra_job_id")
            row = {
                "proposal": prop.to_dict(),
                "consensus": final.to_dict(),
                "execution": exec_out,
                "ts": time.time(),
            }
            proposals_run.append(row)
            self.results.append(row)

        self.results = self.results[-100:]
        self._save_history()

        return {
            "tick": self.tick_count,
            "findings": [f.to_dict() for f in all_findings],
            "proposals": proposals_run,
            "errors": errors,
            "status": self.status(),
        }

    def run_forever(self) -> None:
        print(
            json.dumps(
                {
                    "event": "agents_start",
                    "api_base": self.api_base,
                    "interval": self.interval,
                    "settle": self.settle,
                    "state_dir": str(self.state_dir),
                }
            ),
            flush=True,
        )
        while True:
            try:
                out = self.tick()
                summary = {
                    "event": "tick",
                    "n": out["tick"],
                    "findings": len(out["findings"]),
                    "proposals": len(out["proposals"]),
                    "errors": out["errors"],
                    "fleet": out["status"]["infra"]["nodes"],
                    "treasury_remaining": out["status"]["treasury"]["remaining_howl"],
                }
                # Highlight approved actions
                for p in out["proposals"]:
                    c = p.get("consensus") or {}
                    if c.get("approved"):
                        summary.setdefault("actions", []).append(
                            {
                                "action": (p.get("proposal") or {}).get("action"),
                                "txid": (p.get("execution") or {}).get("settled_txid"),
                                "infra": (p.get("execution") or {}).get("infra_job_id"),
                            }
                        )
                print(json.dumps(summary), flush=True)
            except Exception as e:
                print(
                    json.dumps(
                        {
                            "event": "tick_error",
                            "error": str(e),
                            "trace": traceback.format_exc()[-500:],
                        }
                    ),
                    flush=True,
                )
            time.sleep(self.interval)


def run_runtime_from_env() -> AgentRuntime:
    api = _env("HOWL_AGENTS_API", _env("HOWL_EXPLORER_URL", "https://howlscan.org"))
    state = Path(_env("HOWL_AGENTS_STATE", str(Path.home() / ".howlcoin" / "agents")))
    wallet = _env("HOWL_AGENTS_WALLET") or _env("HOWL_BRIDGE_HOT_WALLET")
    data = _env("HOWL_AGENTS_DATA_DIR") or _env("HOWL_PUBLIC_DATA")
    seed = _env("HOWL_AGENTS_SEED", "147.182.223.204:42069")
    settle = _env("HOWL_AGENTS_SETTLE", "0") in ("1", "true", "yes")
    dry = _env("HOWL_AGENTS_DRY_RUN", "1") not in ("0", "false", "no")
    interval = float(_env("HOWL_AGENTS_INTERVAL", "60") or 60)
    votes = int(_env("HOWL_AGENTS_QUORUM", "2") or 2)
    min_sev = _env("HOWL_AGENTS_SETTLE_SEVERITY", "high") or "high"
    root = _env("HOWL_ROOT")
    return AgentRuntime(
        api_base=api,
        state_dir=state,
        wallet_path=Path(wallet) if wallet else None,
        data_dir=Path(data) if data else None,
        seed=seed,
        settle=settle,
        dry_run_infra=dry,
        required_votes=votes,
        settle_min_severity=min_sev,
        interval=interval,
        howl_root=Path(root) if root else None,
    )


def run_runtime() -> None:
    rt = run_runtime_from_env()
    rt.run_forever()
