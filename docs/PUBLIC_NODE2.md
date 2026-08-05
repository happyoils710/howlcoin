# Secondary public node (node2)

One **extra** always-on full node so Howlcoin is not a single seed.

## Status of AI agent nodes

Agents may **plan** nodes in dry-run (templates only). Those are **not** public peers until you deliberately run live infra or this bootstrap.

Live directory: https://howlscan.org/api/public/seeds

## Recommended: second machine

1. Create a small VPS (1GB+ RAM) or use a home PC with a public IP / port forward.
2. Install Howlcoin (same as [SEEDS.md](../SEEDS.md)).
3. Run:

```bash
export HOWL_ROOT=/opt/howlcoin   # or your checkout
export HOWL_NODE2_PUBLIC_HOST=YOUR_PUBLIC_IP
export HOWL_NODE2_P2P_PORT=42071
bash scripts/howl-public-node2-bootstrap.sh
```

4. Open **TCP 42071** (or your port) on the firewall.
5. On the **primary** Howlscan VPS, register the endpoint so the API lists it:

```bash
cd /opt/howlcoin
PYTHONPATH=/opt/howlcoin /opt/howlcoin-venv/bin/python3 - <<'PY'
from pathlib import Path
from howl.seeds import register_public_seed
register_public_seed(
    "YOUR_PUBLIC_IP:42071",
    path=Path("/var/lib/howlcoin/public_seeds.json"),
    source="operator",
    notes="community / node2",
    public=True,
)
print("ok")
PY
systemctl restart howlcoin-explorer
curl -sS http://127.0.0.1:42080/api/public/seeds | python3 -m json.tool
```

## Same host as primary seed (discouraged)

The primary droplet is often **1GB RAM**. A second full node can OOM the seed.

Only if you accept the risk:

```bash
HOWL_ALLOW_SAME_HOST_NODE2=1 \
HOWL_NODE2_PUBLIC_HOST=147.182.223.204 \
bash /opt/howlcoin/scripts/howl-public-node2-bootstrap.sh
```

Uses ports **42071** (P2P) / **42072** (RPC local), MemoryMax **450M**, default **--no-mine**.

## Safety defaults

| Control | Default |
|---------|---------|
| Dry-run agents | Do not open public ports |
| Same-host node2 | Blocked unless `HOWL_ALLOW_SAME_HOST_NODE2=1` |
| Min free RAM | ~350MB or refuse |
| systemd MemoryMax | 450M |
| Auto-mine on node2 | Off (`HOWL_NODE2_AUTO_MINE=1` to enable) |

## Cleanup agent dry-run junk

```bash
bash /opt/howlcoin/scripts/howl-cleanup-agent-fleet.sh
```

## Verify

```bash
curl -sS https://howlscan.org/api/public/seeds | python3 -m json.tool
ss -lntp | grep -E '42069|42071'
systemctl status howlcoin howlcoin-node2 --no-pager
```
