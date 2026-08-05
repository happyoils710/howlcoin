"""Local DePIN adapter — bootstrap full Howl nodes as subprocesses / unit files."""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from .base import ComputeProvider, ComputeQuote, DeployJob


class LocalProvider(ComputeProvider):
    """Spawns or stages Howl full nodes under agents/infra/nodes/."""

    name = "local"

    def __init__(self, work_dir: Path, howl_root: Optional[Path] = None):
        self.work_dir = Path(work_dir)
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self.howl_root = Path(howl_root) if howl_root else Path(__file__).resolve().parents[3]
        self._jobs: Dict[str, DeployJob] = {}
        self._load()

    def _state_path(self) -> Path:
        return self.work_dir / "local_jobs.json"

    def _load(self) -> None:
        p = self._state_path()
        if p.is_file():
            try:
                raw = json.loads(p.read_text())
                for jid, d in (raw or {}).items():
                    self._jobs[jid] = DeployJob(
                        job_id=d["job_id"],
                        provider=d.get("provider") or self.name,
                        status=d.get("status") or "unknown",
                        endpoint=d.get("endpoint"),
                        cost_howl=float(d.get("cost_howl") or 0),
                        meta=dict(d.get("meta") or {}),
                    )
            except Exception:
                pass

    def _save(self) -> None:
        self._state_path().write_text(
            json.dumps({k: v.to_dict() for k, v in self._jobs.items()}, indent=2)
        )

    def quote(self, *, cpu: float = 1.0, memory_gb: float = 2.0, storage_gb: float = 20.0) -> ComputeQuote:
        return ComputeQuote(
            provider=self.name,
            region="local",
            cpu=cpu,
            memory_gb=memory_gb,
            storage_gb=storage_gb,
            cost_howl_per_day=0.0,
            available=True,
            meta={"note": "Local machine — electricity only"},
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
        job_id = f"local-{node_id}-{uuid.uuid4().hex[:8]}"
        node_dir = self.work_dir / "nodes" / node_id
        node_dir.mkdir(parents=True, exist_ok=True)
        data_dir = node_dir / "data"
        data_dir.mkdir(exist_ok=True)

        # Launch script (closed-loop agents can re-run this)
        launch = node_dir / "launch.sh"
        py = os.environ.get("HOWL_PYTHON", "python3")
        root = str(self.howl_root)
        mine_flag = "--auto-mine" if auto_mine else "--no-mine"
        script = f"""#!/usr/bin/env bash
set -euo pipefail
export PYTHONPATH="{root}${{PYTHONPATH:+:$PYTHONPATH}}"
exec {py} -m howl node \\
  --data-dir "{data_dir}" \\
  --host 0.0.0.0 --port {int(p2p_port)} \\
  --rpc-host 127.0.0.1 --rpc-port {int(rpc_port)} \\
  --connect "{seed}" \\
  --public \\
  {mine_flag}
"""
        launch.write_text(script)
        launch.chmod(0o755)

        # systemd unit template (for DePIN / VPS fleet governance)
        unit = node_dir / f"howl-node-{node_id}.service"
        unit.write_text(
            f"""[Unit]
Description=Howl full node {node_id} (agent-bootstrapped)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory={root}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH={root}
ExecStart={launch}
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
"""
        )

        # docker-compose fragment for portable DePIN
        compose = node_dir / "docker-compose.yml"
        compose.write_text(
            f"""# Agent-generated Howl full node — {node_id}
services:
  howl-{node_id}:
    image: python:3.11-slim
    working_dir: /app
    volumes:
      - {root}:/app:ro
      - {data_dir}:/data
    command: >
      bash -c "pip install -q -r requirements.txt &&
      python -m howl node --data-dir /data --host 0.0.0.0 --port {p2p_port}
      --rpc-host 0.0.0.0 --rpc-port {rpc_port} --connect {seed} --public
      {'--auto-mine' if auto_mine else '--no-mine'}"
    ports:
      - "{p2p_port}:{p2p_port}"
      - "{rpc_port}:{rpc_port}"
    restart: unless-stopped
"""
        )

        meta = {
            "node_id": node_id,
            "node_dir": str(node_dir),
            "launch": str(launch),
            "unit": str(unit),
            "compose": str(compose),
            "seed": seed,
            "p2p_port": p2p_port,
            "rpc_port": rpc_port,
            "auto_mine": auto_mine,
            "created_at": time.time(),
        }

        status = "dry_run"
        endpoint = f"127.0.0.1:{p2p_port}"
        pid = None
        if not dry_run:
            log = open(node_dir / "node.log", "a")  # noqa: SIM115 — long-lived process
            proc = subprocess.Popen(
                ["bash", str(launch)],
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            pid = proc.pid
            meta["pid"] = pid
            meta["log"] = str(node_dir / "node.log")
            status = "running"

        job = DeployJob(
            job_id=job_id,
            provider=self.name,
            status=status,
            endpoint=endpoint,
            cost_howl=0.0,
            meta=meta,
        )
        self._jobs[job_id] = job
        self._save()
        return job

    def status(self, job_id: str) -> DeployJob:
        job = self._jobs.get(job_id)
        if not job:
            return DeployJob(job_id=job_id, provider=self.name, status="unknown")
        pid = (job.meta or {}).get("pid")
        if pid and job.status == "running":
            try:
                os.kill(int(pid), 0)
            except OSError:
                job.status = "stopped"
                self._save()
        return job

    def list_jobs(self) -> List[DeployJob]:
        return list(self._jobs.values())
