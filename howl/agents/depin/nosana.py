"""Nosana / decentralized GPU-compute adapter for agent workloads + light nodes.

Env:
  NOSANA_API_KEY (optional) — without it, jobs are dry_run manifests.
  NOSANA_MARKET — market address / slug
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List

from .base import ComputeProvider, ComputeQuote, DeployJob


class NosanaProvider(ComputeProvider):
    name = "nosana"

    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, DeployJob] = {}
        self._load()

    def _state_path(self) -> Path:
        return self.work_dir / "nosana_jobs.json"

    def _load(self) -> None:
        p = self._state_path()
        if p.is_file():
            try:
                raw = json.loads(p.read_text())
                for jid, d in (raw or {}).items():
                    self._jobs[jid] = DeployJob(**{**d, "meta": d.get("meta") or {}})
            except Exception:
                pass

    def _save(self) -> None:
        self._state_path().write_text(
            json.dumps({k: v.to_dict() for k, v in self._jobs.items()}, indent=2)
        )

    def _live(self) -> bool:
        return bool(os.environ.get("NOSANA_API_KEY"))

    def quote(self, *, cpu: float = 1.0, memory_gb: float = 2.0, storage_gb: float = 20.0) -> ComputeQuote:
        howl_day = float(os.environ.get("HOWL_NOSANA_HOWL_PER_DAY", "8") or 8)
        return ComputeQuote(
            provider=self.name,
            region=os.environ.get("NOSANA_REGION", "solana-depin"),
            cpu=cpu,
            memory_gb=memory_gb,
            storage_gb=storage_gb,
            cost_howl_per_day=howl_day,
            available=True,
            meta={
                "market": os.environ.get("NOSANA_MARKET", ""),
                "live_creds": self._live(),
                "note": "Ideal for AI monitor workers; full nodes prefer Akash/local",
            },
        )

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
        job_id = f"nosana-{node_id}-{uuid.uuid4().hex[:8]}"
        out_dir = self.work_dir / "nodes" / node_id
        out_dir.mkdir(parents=True, exist_ok=True)
        job_spec = {
            "version": "0.1",
            "ops": [
                {
                    "type": "container/run",
                    "id": f"howl-{node_id}",
                    "args": {
                        "image": "python:3.11-slim",
                        "cmd": [
                            "bash",
                            "-c",
                            (
                                "pip install -q git+https://github.com/happyoils710/howlcoin.git; "
                                f"python -m howl node --data-dir /data --host 0.0.0.0 --port {p2p_port} "
                                f"--rpc-host 0.0.0.0 --rpc-port {rpc_port} --connect {seed} --public "
                                f"{'--auto-mine' if auto_mine else '--no-mine'}"
                            ),
                        ],
                        "env": {"HOWL_SEED": seed},
                    },
                }
            ],
            "meta": {"project": "howlcoin", "role": "full-node", "node_id": node_id},
        }
        spec_path = out_dir / "job.json"
        spec_path.write_text(json.dumps(job_spec, indent=2))

        status = "dry_run"
        meta = {
            "node_id": node_id,
            "spec": str(spec_path),
            "seed": seed,
            "created_at": time.time(),
            "note": "Post job via Nosana CLI/SDK when NOSANA_API_KEY is set",
        }
        if not dry_run and self._live():
            status = "pending"
            meta["api_key_present"] = True

        q = self.quote()
        job = DeployJob(
            job_id=job_id,
            provider=self.name,
            status=status,
            endpoint=None,
            cost_howl=q.cost_howl_per_day,
            meta=meta,
        )
        self._jobs[job_id] = job
        self._save()
        return job

    def status(self, job_id: str) -> DeployJob:
        return self._jobs.get(job_id) or DeployJob(
            job_id=job_id, provider=self.name, status="unknown"
        )

    def list_jobs(self) -> List[DeployJob]:
        return list(self._jobs.values())
