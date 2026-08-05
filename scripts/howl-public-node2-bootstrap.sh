#!/usr/bin/env bash
# Bootstrap ONE extra public Howl full node (safe defaults).
#
# Preferred: run on a SECOND machine (another VPS / home always-on PC).
# Same-host (primary VPS): requires HOWL_ALLOW_SAME_HOST_NODE2=1 and enough free RAM.
#
# Usage:
#   HOWL_NODE2_PUBLIC_HOST=YOUR_PUBLIC_IP bash scripts/howl-public-node2-bootstrap.sh
#   HOWL_ALLOW_SAME_HOST_NODE2=1 HOWL_NODE2_PUBLIC_HOST=147.182.223.204 bash ...
#
set -euo pipefail

ROOT="${HOWL_ROOT:-/opt/howlcoin}"
VENV_PY="${HOWL_PYTHON:-/opt/howlcoin-venv/bin/python3}"
DATA="${HOWL_NODE2_DATA:-/var/lib/howlcoin-node2}"
P2P_PORT="${HOWL_NODE2_P2P_PORT:-42071}"
RPC_PORT="${HOWL_NODE2_RPC_PORT:-42072}"
SEED="${HOWL_AGENTS_SEED:-147.182.223.204:42069}"
PUBLIC_HOST="${HOWL_NODE2_PUBLIC_HOST:-${HOWL_PUBLIC_NODE_HOST:-}}"
SEEDS_FILE="${HOWL_SEEDS_FILE:-/var/lib/howlcoin/public_seeds.json}"
ALLOW_SAME="${HOWL_ALLOW_SAME_HOST_NODE2:-0}"
AUTO_MINE="${HOWL_NODE2_AUTO_MINE:-0}"

if [[ ! -x "$VENV_PY" && -x "$(command -v python3)" ]]; then
  VENV_PY="$(command -v python3)"
fi
if [[ ! -d "$ROOT/howl" ]]; then
  echo "HOWL_ROOT not found at $ROOT — set HOWL_ROOT to the howlcoin checkout"
  exit 1
fi

# --- safety: RAM check ---
avail_kb=$(awk '/MemAvailable/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
avail_mb=$(( avail_kb / 1024 ))
echo "MemAvailable ≈ ${avail_mb} MB"

PRIMARY_IP="${SEED%%:*}"
THIS_IP="$(curl -4 -sS --max-time 4 ifconfig.me 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || true)"

if [[ -n "$THIS_IP" && "$THIS_IP" == "$PRIMARY_IP" && "$ALLOW_SAME" != "1" ]]; then
  cat <<EOF
REFUSING same-host node2 on the primary seed VPS without HOWL_ALLOW_SAME_HOST_NODE2=1

This droplet is the primary seed ($PRIMARY_IP). A second full node here can OOM a 1GB VPS.

Safer options:
  1) Spin a second DigitalOcean droplet (\$4–6), open TCP $P2P_PORT, run this script there
  2) Run on a home always-on PC with port forward $P2P_PORT
  3) Explicit override (at your risk):
       HOWL_ALLOW_SAME_HOST_NODE2=1 HOWL_NODE2_PUBLIC_HOST=$PRIMARY_IP \\
         bash scripts/howl-public-node2-bootstrap.sh

EOF
  exit 2
fi

if [[ "$avail_mb" -gt 0 && "$avail_mb" -lt 350 && "$ALLOW_SAME" != "1" ]]; then
  echo "REFUSING: less than 350MB free RAM (have ${avail_mb}MB). Free memory or use another host."
  exit 2
fi

if [[ -z "$PUBLIC_HOST" ]]; then
  PUBLIC_HOST="$THIS_IP"
fi
if [[ -z "$PUBLIC_HOST" ]]; then
  echo "Set HOWL_NODE2_PUBLIC_HOST to the public IP peers should dial"
  exit 1
fi

echo "node2 public endpoint will be: ${PUBLIC_HOST}:${P2P_PORT}"
echo "data dir: $DATA"
echo "seed: $SEED"

mkdir -p "$DATA"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# init wallet+genesis if empty
if [[ ! -f "$DATA/wallet.json" ]]; then
  "$VENV_PY" -m howl --data-dir "$DATA" init || true
fi

MINE_FLAG="--no-mine"
if [[ "$AUTO_MINE" == "1" ]]; then
  MINE_FLAG="--auto-mine"
fi

UNIT=/etc/systemd/system/howlcoin-node2.service
cat > "$UNIT" <<EOF
[Unit]
Description=Howlcoin secondary public full node (node2)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$ROOT
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONPATH=$ROOT
MemoryMax=450M
MemoryHigh=380M
CPUQuota=80%
ExecStart=$VENV_PY -m howl node \\
  --data-dir $DATA \\
  --host 0.0.0.0 \\
  --port $P2P_PORT \\
  --rpc-host 127.0.0.1 \\
  --rpc-port $RPC_PORT \\
  --connect $SEED \\
  --public \\
  $MINE_FLAG
Restart=on-failure
RestartSec=25

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable howlcoin-node2
systemctl restart howlcoin-node2
sleep 3
systemctl --no-pager -l status howlcoin-node2 | head -20

# firewall hint
if command -v ufw >/dev/null 2>&1; then
  ufw allow "${P2P_PORT}/tcp" comment 'Howl node2 P2P' || true
  echo "ufw: allowed ${P2P_PORT}/tcp"
fi

# register in seeds file (local host and/or primary VPS seeds file if shared)
ENDPOINT="${PUBLIC_HOST}:${P2P_PORT}"
export PYTHONPATH="$ROOT"
"$VENV_PY" - <<PY
from pathlib import Path
from howl.seeds import register_public_seed, list_seeds
import json
path = Path("$SEEDS_FILE")
row = register_public_seed(
    "$ENDPOINT",
    path=path,
    role="seed",
    source="operator",
    notes="secondary public node (node2)",
    public=True,
    status="unknown",
    meta={"service": "howlcoin-node2", "p2p": $P2P_PORT},
)
print("registered", row["endpoint"], "->", path)
print(json.dumps(list_seeds(probe=True), indent=2)[:1200])
PY

cat <<EOF

=== node2 bootstrap complete ===
P2P:  ${PUBLIC_HOST}:${P2P_PORT}
RPC:  127.0.0.1:${RPC_PORT} (local only)
Data: $DATA
Logs: journalctl -u howlcoin-node2 -f

Peers join with:
  python3 -m howl node --connect ${PUBLIC_HOST}:${P2P_PORT}

If this host is NOT the explorer VPS, copy the endpoint into the primary registry:
  On primary VPS:
    python3 -c "from pathlib import Path; from howl.seeds import register_public_seed; register_public_seed('${ENDPOINT}', path=Path('/var/lib/howlcoin/public_seeds.json'), source='operator', notes='node2', public=True)"
    systemctl restart howlcoin-explorer

Open cloud firewall / security group for TCP ${P2P_PORT}.
EOF
