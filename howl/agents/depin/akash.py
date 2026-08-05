"""Akash Network adapter — SDL manifests for decentralized node hosting.

Credentials (optional):
  AKASH_KEY_NAME, AKASH_ACCOUNT_ADDRESS, AKASH_NET, AKASH_CHAIN_ID
Without credentials, deploy produces SDL + dry_run job (governance-ready).
"""

from __future__ import annotations

import json
import os
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .base import ComputeProvider, ComputeQuote, DeployJob


class AkashProvider(ComputeProvider):
    name = "akash"

    def __init__(self, work_dir: Path):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._jobs: Dict[str, DeployJob] = {}
        self._load()

    def _state_path(self) -> Path:
        return self.work_dir / "akash_jobs.json"

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
        return bool(os.environ.get("AKASH_ACCOUNT_ADDRESS") and os.environ.get("AKASH_KEY_NAME"))

    def quote(self, *, cpu: float = 1.0, memory_gb: float = 2.0, storage_gb: float = 20.0) -> ComputeQuote:
        # Rough uakt→HOWL soft estimate for treasury planning (operator-tunable)
        uakt_day = max(100, int(cpu * 50 + memory_gb * 30 + storage_gb * 2))
        howl_day = float(os.environ.get("HOWL_AKASH_HOWL_PER_DAY", "5") or 5)
        return ComputeQuote(
            provider=self.name,
            region=os.environ.get("AKASH_REGION", "global"),
            cpu=cpu,
            memory_gb=memory_gb,
            storage_gb=storage_gb,
            cost_howl_per_day=howl_day,
            available=True,
            meta={"uakt_estimate_day": uakt_day, "live_creds": self._live()},
        )

    def _sdl(self, node_id: str, seed: str, p2p_port: int, rpc_port: int, auto_mine: bool) -> str:
        mine = "true" if auto_mine else "false"
        return f"""# Akash SDL — Howl full node {node_id} (agent-generated)
version: "2.0"
services:
  howl:
    image: python:3.11-slim
    env:
      - HOWL_SEED={seed}
      - HOWL_AUTO_MINE={mine}
    expose:
      - port: {p2p_port}
        as: {p2p_port}
        to:
          - global: true
      - port: {rpc_port}
        as: {rpc_port}
        to:
          - global: true
    command:
      - bash
      - -c
      - |
        pip install -q git+https://github.com/happyoils710/howlcoin.git || true
        python -m howl node --data-dir /data --host 0.0.0.0 --port {p2p_port} \\
          --rpc-host 0.0.0.0 --rpc-port {rpc_port} --connect {seed} --public \\
          {"--auto-mine" if auto_mine else "--no-mine"}
profiles:
  compute:
    howl:
      resources:
        cpu:
          units: 1.0
        memory:
          size: 2Gi
        storage:
          size: 20Gi
  placement:
    dcloud:
      pricing:
        howl:
          denom: uakt
          amount: 1000
deployment:
  howl:
    dcloud:
      profile: howl
      count: 1
"""

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
        job_id = f"akash-{node_id}-{uuid.uuid4().hex[:8]}"
        out_dir = self.work_dir / "nodes" / node_id
        out_dir.mkdir(parents=True, exist_ok=True)
        sdl_path = out_dir / "deploy.yml"
        sdl = self._sdl(node_id, seed, p2p_port, rpc_port, auto_mine)
        sdl_path.write_text(sdl)

        status = "dry_run"
        meta: Dict = {
            "node_id": node_id,
            "sdl": str(sdl_path),
            "seed": seed,
            "created_at": time.time(),
            "note": "Submit with akash tx deployment create deploy.yml — when live_creds set",
        }
        # Live submission is intentionally opt-in; agents stage manifests by default
        if not dry_run and self._live():
            status = "pending"
            meta["submit"] = "manual_or_cli"
            meta["hint"] = "akash tx deployment create " + str(sdl_path)

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
