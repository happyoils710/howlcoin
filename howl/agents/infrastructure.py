"""Closed-loop infrastructure: agents bootstrap and govern full nodes at scale."""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from .depin import best_quote, build_providers
from .depin.base import DeployJob
from .economy import AgentTreasury
from ..seeds import (
    ensure_primary_registered,
    is_public_endpoint,
    register_public_seed,
)


class InfraGovernor:
    """
    Agents create/govern their own infrastructure:
      - quote DePIN markets
      - deploy full Howl nodes (local / Akash / Nosana)
      - track fleet inventory for rebalancing
      - register healthy public seeds for /api/public/seeds
    """

    def __init__(
        self,
        work_dir: Path,
        *,
        seed: str = "147.182.223.204:42069",
        howl_root: Optional[Path] = None,
        prefer_providers: Optional[List[str]] = None,
        dry_run: bool = True,
        seeds_registry_path: Optional[Path] = None,
    ):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.dry_run = dry_run
        self.prefer = prefer_providers or ["local", "akash", "nosana"]
        self.providers = build_providers(self.work_dir / "depin", howl_root=howl_root)
        self.fleet_path = self.work_dir / "fleet.json"
        self.fleet: List[Dict[str, Any]] = self._load_fleet()
        # Prefer shared VPS registry so explorer can read agent-registered seeds
        if seeds_registry_path is not None:
            self.seeds_path = Path(seeds_registry_path)
        elif os.environ.get("HOWL_SEEDS_FILE"):
            self.seeds_path = Path(os.environ["HOWL_SEEDS_FILE"])
        elif Path("/var/lib/howlcoin").is_dir():
            self.seeds_path = Path("/var/lib/howlcoin/public_seeds.json")
        else:
            self.seeds_path = self.work_dir.parent / "public_seeds.json"

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
            "seeds_registry": str(self.seeds_path),
        }

    def _public_announce_endpoint(self, job_endpoint: Optional[str], p2p_port: int) -> Optional[str]:
        """
        Resolve a world-reachable host:port for the seed directory.
        HOWL_PUBLIC_NODE_HOST (or HOWL_PUBLIC_IP) + port overrides local endpoints.
        """
        pub_host = (
            os.environ.get("HOWL_PUBLIC_NODE_HOST")
            or os.environ.get("HOWL_PUBLIC_IP")
            or ""
        ).strip()
        if pub_host:
            return f"{pub_host}:{int(p2p_port)}"
        if job_endpoint and is_public_endpoint(job_endpoint):
            return job_endpoint
        return None

    def publish_seed_directory(self) -> Dict[str, Any]:
        """Ensure primary + *live* fleet public endpoints are in the shared seed registry.

        Dry-run bootstrap jobs must NOT appear as public seeds (ports are not open).
        """
        registered: List[Dict[str, Any]] = []
        try:
            registered.append(ensure_primary_registered(self.seeds_path))
        except Exception as e:
            return {"ok": False, "error": str(e), "registered": []}

        # Drop stale agent dry-run / non-running entries from registry
        self._prune_stale_agent_seeds()

        for entry in self.fleet:
            st = str(entry.get("status") or "")
            # Only advertise actually running / pending cloud nodes — never dry_run placeholders
            if st not in ("running", "pending"):
                continue
            meta = entry.get("meta") or {}
            p2p = int(meta.get("p2p_port") or 0)
            ep = entry.get("public_endpoint") or entry.get("endpoint")
            announce = None
            if entry.get("public_endpoint"):
                announce = str(entry["public_endpoint"])
            elif p2p:
                announce = self._public_announce_endpoint(ep, p2p)
            if not announce and ep and is_public_endpoint(str(ep)):
                announce = str(ep)
            if not announce:
                continue
            # Never list loopback as public
            if not is_public_endpoint(announce) and not (
                os.environ.get("HOWL_PUBLIC_NODE_HOST") or os.environ.get("HOWL_PUBLIC_IP")
            ):
                continue
            try:
                row = register_public_seed(
                    announce,
                    path=self.seeds_path,
                    role="seed",
                    source="agent",
                    notes=str(entry.get("reason") or "agent fleet node"),
                    public=True,
                    agent_job_id=str(entry.get("job_id") or ""),
                    meta={
                        "node_id": entry.get("node_id"),
                        "provider": entry.get("provider"),
                        "status": entry.get("status"),
                    },
                    status=str(entry.get("status") or "unknown"),
                )
                registered.append(row)
            except Exception:
                continue
        return {
            "ok": True,
            "path": str(self.seeds_path),
            "registered": registered,
            "count": len(registered),
        }

    def _prune_stale_agent_seeds(self) -> None:
        """Remove agent-registered seeds that are dry_run or not in the live fleet."""
        path = self.seeds_path
        if not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return
        seeds = list(raw.get("seeds") or []) if isinstance(raw, dict) else list(raw or [])
        live_job_ids = {
            str(e.get("job_id"))
            for e in self.fleet
            if str(e.get("status") or "") in ("running", "pending")
        }
        live_endpoints = {
            str(e.get("public_endpoint") or e.get("endpoint") or "")
            for e in self.fleet
            if str(e.get("status") or "") in ("running", "pending")
        }
        kept: List[Dict[str, Any]] = []
        for s in seeds:
            if not isinstance(s, dict):
                continue
            src = str(s.get("source") or "")
            role = str(s.get("role") or "")
            # always keep primary / operator / env / static
            if role == "primary" or src in ("static", "operator", "env", "file"):
                # but drop agent-polluted "primary" duplicates that aren't real primary endpoint
                kept.append(s)
                continue
            if src != "agent":
                kept.append(s)
                continue
            # agent entries: keep only if still a live fleet job
            jid = str(s.get("agent_job_id") or "")
            ep = str(s.get("endpoint") or "")
            meta = s.get("meta") or {}
            if str(meta.get("status") or "") == "dry_run":
                continue
            if jid and jid in live_job_ids:
                kept.append(s)
            elif ep and ep in live_endpoints:
                kept.append(s)
            # else drop
        payload = {"updated_at": time.time(), "seeds": kept}
        try:
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

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
            # Prefer public announce host when operator configured one
            announce = self._public_announce_endpoint(job.endpoint, p2p)
            if announce:
                entry["public_endpoint"] = announce
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

        seed_pub = self.publish_seed_directory()

        return {
            "ok": True,
            "jobs": jobs,
            "cost_howl": cost,
            "fleet_size": len(self.fleet),
            "dry_run": dry,
            "provider": pname,
            "public_seeds": seed_pub,
        }
