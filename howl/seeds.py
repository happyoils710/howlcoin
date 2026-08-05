"""Public seed registry — static + operator + agent-registered peers.

Used by explorer API (/api/public/seeds), agents (register healthy public nodes),
and operators (HOWL_PUBLIC_SEEDS / public_seeds.json).
"""

from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import PUBLIC_SEED, PUBLIC_SEED_HOST, PUBLIC_SEED_PORT


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def parse_endpoint(endpoint: str) -> Optional[Tuple[str, int]]:
    ep = (endpoint or "").strip()
    if not ep or ":" not in ep:
        return None
    host, _, port_s = ep.rpartition(":")
    host = host.strip().strip("[]")
    try:
        port = int(port_s)
    except ValueError:
        return None
    if not host or port <= 0 or port > 65535:
        return None
    return host, port


def default_primary() -> Dict[str, Any]:
    return {
        "id": "primary",
        "host": PUBLIC_SEED_HOST,
        "port": int(PUBLIC_SEED_PORT),
        "endpoint": PUBLIC_SEED,
        "role": "primary",
        "source": "static",
        "public": True,
        "status": "unknown",
        "notes": "Howlscan DigitalOcean seed (24/7)",
    }


def registry_paths() -> List[Path]:
    """Ordered paths to merge (later files override same endpoint)."""
    paths: List[Path] = []
    for cand in (
        _env("HOWL_SEEDS_FILE"),
        "/var/lib/howlcoin/public_seeds.json",
        str(Path.home() / ".howlcoin" / "public_seeds.json"),
        _env("HOWL_AGENTS_STATE") and str(Path(_env("HOWL_AGENTS_STATE")) / "public_seeds.json"),
        str(Path.home() / ".howlcoin" / "agents" / "public_seeds.json"),
        "/var/lib/howlcoin/agents/public_seeds.json",
    ):
        if cand:
            paths.append(Path(cand))
    # de-dupe preserving order
    seen = set()
    out: List[Path] = []
    for p in paths:
        key = str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out


def _load_file(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(raw, dict):
        seeds = raw.get("seeds") or []
    elif isinstance(raw, list):
        seeds = raw
    else:
        return []
    out: List[Dict[str, Any]] = []
    for s in seeds:
        if not isinstance(s, dict):
            continue
        ep = s.get("endpoint") or ""
        if not ep and s.get("host") and s.get("port"):
            ep = f"{s['host']}:{s['port']}"
        parsed = parse_endpoint(str(ep))
        if not parsed:
            continue
        host, port = parsed
        row = dict(s)
        row["host"] = host
        row["port"] = port
        row["endpoint"] = f"{host}:{port}"
        row.setdefault("id", f"{host}-{port}")
        row.setdefault("source", "file")
        row.setdefault("public", True)
        row.setdefault("role", "seed")
        row.setdefault("status", "unknown")
        out.append(row)
    return out


def env_extra_seeds() -> List[Dict[str, Any]]:
    """HOWL_PUBLIC_SEEDS=host:port,host2:port2"""
    raw = _env("HOWL_PUBLIC_SEEDS")
    if not raw:
        return []
    out: List[Dict[str, Any]] = []
    for part in raw.split(","):
        parsed = parse_endpoint(part.strip())
        if not parsed:
            continue
        host, port = parsed
        out.append(
            {
                "id": f"env-{host}-{port}",
                "host": host,
                "port": port,
                "endpoint": f"{host}:{port}",
                "role": "seed",
                "source": "env",
                "public": True,
                "status": "unknown",
                "notes": "from HOWL_PUBLIC_SEEDS",
            }
        )
    return out


def merge_seeds(*groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    by_ep: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for group in groups:
        for s in group:
            ep = str(s.get("endpoint") or "")
            if not ep:
                continue
            if ep not in by_ep:
                order.append(ep)
                by_ep[ep] = dict(s)
            else:
                # later entries win on fields, keep earliest role if primary
                prev = by_ep[ep]
                merged = dict(prev)
                merged.update({k: v for k, v in s.items() if v is not None})
                if prev.get("role") == "primary":
                    merged["role"] = "primary"
                    merged["id"] = prev.get("id") or merged.get("id")
                by_ep[ep] = merged
    # primary first
    seeds = [by_ep[ep] for ep in order]
    seeds.sort(key=lambda s: (0 if s.get("role") == "primary" else 1, s.get("endpoint") or ""))
    return seeds


def _local_ipv4s() -> set:
    ips = {"127.0.0.1"}
    try:
        # hostname -I style discovery without shell
        import netifaces  # type: ignore

        for iface in netifaces.interfaces():
            for info in netifaces.ifaddresses(iface).get(netifaces.AF_INET, []):
                addr = info.get("addr")
                if addr:
                    ips.add(addr)
    except Exception:
        pass
    try:
        # UDP trick: discover primary outbound IP
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ips.add(s.getsockname()[0])
        s.close()
    except Exception:
        pass
    # common env hints on VPS
    for key in ("HOWL_PUBLIC_NODE_HOST", "HOWL_PUBLIC_IP"):
        v = (os.environ.get(key) or "").strip()
        if v and ":" not in v:
            ips.add(v)
    return ips


def tcp_probe(host: str, port: int, timeout: float = 2.5) -> str:
    """TCP check. If host is this machine's public IP, also try 127.0.0.1 (hairpin NAT)."""
    port = int(port)
    targets = [(host, port)]
    local_ips = _local_ipv4s()
    if host in local_ips or host == PUBLIC_SEED_HOST:
        targets.append(("127.0.0.1", port))
    for h, p in targets:
        try:
            with socket.create_connection((h, p), timeout=timeout):
                return "up"
        except OSError:
            continue
    return "down"


def list_seeds(
    *,
    probe: bool = True,
    include_private: bool = False,
    probe_timeout: float = 2.5,
) -> Dict[str, Any]:
    """
    Build public seed directory.
    probe: TCP check P2P port (best-effort).
    """
    file_seeds: List[Dict[str, Any]] = []
    for p in registry_paths():
        file_seeds.extend(_load_file(p))

    seeds = merge_seeds([default_primary()], env_extra_seeds(), file_seeds)

    # Hide agent dry-run placeholders that were never real listeners
    cleaned: List[Dict[str, Any]] = []
    for s in seeds:
        meta = s.get("meta") or {}
        if str(s.get("source") or "") == "agent" and str(meta.get("status") or "") == "dry_run":
            continue
        if str(s.get("status") or "") == "dry_run" and str(s.get("source") or "") == "agent":
            continue
        cleaned.append(s)
    seeds = cleaned

    if not include_private:
        seeds = [s for s in seeds if s.get("public", True)]

    checked_at = time.time()
    if probe:
        for s in seeds:
            host = str(s.get("host") or "")
            port = int(s.get("port") or 0)
            if not host or not port:
                s["status"] = "unknown"
                continue
            # skip probing obvious loopback as "public up"
            if host in ("127.0.0.1", "localhost", "0.0.0.0") and s.get("public"):
                s["status"] = "local"
                s["last_check"] = checked_at
                continue
            s["status"] = tcp_probe(host, port, timeout=probe_timeout)
            s["last_check"] = checked_at

    public = [s for s in seeds if s.get("public", True)]
    up = [s for s in public if s.get("status") == "up"]
    return {
        "network": "howlcoin",
        "p2p_port_default": PUBLIC_SEED_PORT,
        "primary": PUBLIC_SEED,
        "count": len(public),
        "up_count": len(up),
        "seeds": public,
        "connect_examples": [
            f"python3 -m howl node --connect {PUBLIC_SEED}",
            f"python3 -m howl node --public --auto-mine",
        ]
        + (
            [f"python3 -m howl node --connect {up[0]['endpoint']}"]
            if up and up[0].get("endpoint") != PUBLIC_SEED
            else []
        ),
        "docs": "https://github.com/happyoils710/howlcoin/blob/main/SEEDS.md",
        "updated_at": checked_at,
        "registry_files": [str(p) for p in registry_paths() if p.is_file()],
        "note": "Only live seeds should appear. Dry-run agent ports are filtered out.",
    }


def register_public_seed(
    endpoint: str,
    *,
    path: Optional[Path] = None,
    role: str = "seed",
    source: str = "agent",
    notes: str = "",
    public: bool = True,
    agent_job_id: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
    status: str = "unknown",
) -> Dict[str, Any]:
    """
    Persist a public seed into the operator/agent registry file.
    Default path: HOWL_SEEDS_FILE or /var/lib/howlcoin/public_seeds.json or ~/.howlcoin/public_seeds.json
    """
    parsed = parse_endpoint(endpoint)
    if not parsed:
        raise ValueError(f"invalid endpoint: {endpoint}")
    host, port = parsed

    if path is None:
        env_p = _env("HOWL_SEEDS_FILE")
        if env_p:
            path = Path(env_p)
        elif Path("/var/lib/howlcoin").is_dir():
            path = Path("/var/lib/howlcoin/public_seeds.json")
        else:
            path = Path.home() / ".howlcoin" / "public_seeds.json"

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_file(path)
    row = {
        "id": f"{source}-{host}-{port}",
        "host": host,
        "port": port,
        "endpoint": f"{host}:{port}",
        "role": role if f"{host}:{port}" != PUBLIC_SEED else "primary",
        "source": source,
        "public": bool(public),
        "status": status,
        "notes": notes,
        "agent_job_id": agent_job_id,
        "meta": meta or {},
        "registered_at": time.time(),
        "updated_at": time.time(),
    }
    # replace same endpoint
    others = [s for s in existing if s.get("endpoint") != row["endpoint"]]
    others.append(row)
    payload = {"updated_at": time.time(), "seeds": others}
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return row


def ensure_primary_registered(path: Optional[Path] = None) -> Dict[str, Any]:
    return register_public_seed(
        PUBLIC_SEED,
        path=path,
        role="primary",
        source="static",
        notes="Primary Howlscan seed",
        public=True,
        status="unknown",
    )


def is_public_endpoint(endpoint: str) -> bool:
    parsed = parse_endpoint(endpoint)
    if not parsed:
        return False
    host, _ = parsed
    if host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
        return False
    if host.startswith("10.") or host.startswith("192.168.") or host.startswith("172."):
        # treat RFC1918 as non-public for directory (operator can force public=True)
        return False
    return True
