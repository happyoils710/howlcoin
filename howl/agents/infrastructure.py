"""Closed-loop infrastructure: agents bootstrap and govern full nodes at scale."""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .depin import best_quote, build_providers
from .depin.base import DeployJob
from .economy import AgentTreasury


class InfraGovernor:
    """
    Agents create/govern their own infrastructure:
      - quote DePIN markets
      - deploy full Howl nodes (local / Akash / Nosana)
      - track fleet inventory for rebalancing
    """

    def __init__(
        self,
        work_dir: Path,
        *,
        seed: str = "147.182.223.204:42069",
        howl_root: Optional[Path] = None,
        prefer_providers: Optional[List[str]] = None,
        dry_run: bool = True,
    ):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.dry_run = dry_run
        self.prefer = prefer_providers or ["local", "akash", "nosana"]
        self.providers = build_providers(self.work_dir / "depin", howl_root=howl_root)
        self.fleet_path = self.work_dir / "fleet.json"
        self.fleet: List[Dict[str, Any]] = self._load_fleet()

    def _load_fleet(self) -> List[Dict[str, Any]]:
        if self.fleet_path.is_file():
            try:
                return list(json.loads(self.fleet_path.read_text()) or [])
            except Exception:
                pass
        return []

    def _save_fleet(self) -> None:
        self.fleet_path.write_text(json.dumps(self.fleet, indent=2))

    def inventory(self) -> Dict[str, Any]:
        by_provider: Dict[str, int] = {}
        for n in self.fleet:
            p = n.get("provider") or "unknown"
            by_provider[p] = by_provider.get(p, 0) + 1
        quotes = {}
        for name, prov in self.providers.items():
            try:
                quotes[name] = prov.quote().to_dict()
            except Exception as e:
                quotes[name] = {"error": str(e)}
        return {
            "nodes": len(self.fleet),
            "by_provider": by_provider,
            "fleet_tail": self.fleet[-20:],
            "quotes": quotes,
            "dry_run_default": self.dry_run,
            "seed": self.seed,
        }

    def plan_bootstrap(
        self,
        *,
        count: int = 1,
        reason: str = "",
        auto_mine: bool = False,
    ) -> Dict[str, Any]:
        count = max(1, min(10, int(count)))
        q = best_quote(self.providers, prefer=self.prefer)
        return {
            "action": "bootstrap_node",
            "count": count,
            "reason": reason,
            "quote": q.to_dict(),
            "estimated_cost_howl_day": q.cost_howl_per_day * count,
            "provider": q.provider,
            "auto_mine": auto_mine,
        }

    def execute_bootstrap(
        self,
        *,
        treasury: AgentTreasury,
        count: int = 1,
        reason: str = "",
        auto_mine: bool = False,
        provider_name: Optional[str] = None,
        dry_run: Optional[bool] = None,
        base_p2p_port: int = 42100,
    ) -> Dict[str, Any]:
        """
        Bootstrap `count` full nodes. Charges treasury soft budget for non-local quotes.
        Returns job list + fleet updates.
        """
        count = max(1, min(10, int(count)))
        dry = self.dry_run if dry_run is None else dry_run
        q = best_quote(self.providers, prefer=self.prefer)
        pname = provider_name or q.provider
        prov = self.providers.get(pname) or self.providers.get("local")
        if not prov:
            return {"ok": False, "error": "no compute provider"}

        cost = float(q.cost_howl_per_day) * count if pname != "local" else 0.0
        if cost > 0 and not treasury.can_spend(cost):
            return {
                "ok": False,
                "error": "treasury cannot fund infra",
                "cost_howl": cost,
                "treasury": treasury.to_dict(),
            }

        jobs: List[Dict[str, Any]] = []
        for i in range(count):
            node_id = f"n-{uuid.uuid4().hex[:8]}"
            p2p = base_p2p_port + len(self.fleet) + i
            rpc = p2p + 1
            job: DeployJob = prov.deploy_node(
                node_id=node_id,
                seed=self.seed,
                p2p_port=p2p,
                rpc_port=rpc,
                auto_mine=auto_mine,
                dry_run=dry,
            )
            entry = {
                "node_id": node_id,
                "job_id": job.job_id,
                "provider": job.provider,
                "status": job.status,
                "endpoint": job.endpoint,
                "reason": reason,
                "created_at": time.time(),
                "meta": job.meta,
            }
            self.fleet.append(entry)
            jobs.append(job.to_dict())

        if cost > 0:
            treasury.record(cost, "infra_bootstrap", {"count": count, "provider": pname, "reason": reason})

        self._save_fleet()
        # Write governance snapshot (agents "own" inventory)
        gov = {
            "updated_at": time.time(),
            "policy": {
                "max_nodes_per_tick": 10,
                "prefer_providers": self.prefer,
                "seed": self.seed,
                "dry_run": dry,
            },
            "inventory": self.inventory(),
        }
        (self.work_dir / "governance.json").write_text(json.dumps(gov, indent=2))

        return {
            "ok": True,
            "jobs": jobs,
            "cost_howl": cost,
            "fleet_size": len(self.fleet),
            "dry_run": dry,
            "provider": pname,
        }
