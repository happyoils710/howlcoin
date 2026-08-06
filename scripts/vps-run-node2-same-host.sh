#!/usr/bin/env bash
# Howl secondary public node — run ON the primary VPS as root
# Endpoint: 147.182.223.204:42071
set -euo pipefail

ROOT=/opt/howlcoin
PY=/opt/howlcoin-venv/bin/python3
DATA=/var/lib/howlcoin-node2
P2P=42071
RPC=42072
SEED=147.182.223.204:42069
PUBLIC=147.182.223.204
SEEDS_FILE=/var/lib/howlcoin/public_seeds.json

if [[ ! -d "$ROOT/howl" ]]; then
  echo "Missing $ROOT — this must run on the Howl seed VPS"
  exit 1
fi
if [[ ! -x "$PY" ]]; then PY=python3; fi

echo "=== memory ==="
free -m
echo "=== primary listener ==="
ss -lntp | grep 42069 || echo "(42069 not shown)"

mkdir -p "$DATA" "$(dirname "$SEEDS_FILE")"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# init if empty
if [[ ! -f "$DATA/wallet.json" ]]; then
  "$PY" -m howl --data-dir "$DATA" init || true
fi

# clean agent dry-run fleet noise
mkdir -p /var/lib/howlcoin/agents/infra
echo '[]' > /var/lib/howlcoin/agents/infra/fleet.json

# systemd unit with memory caps
cat > /etc/systemd/system/howlcoin-node2.service <<EOF
[Unit]
Description=Howlcoin secondary public full node (node2)
After=network-online.target howlcoin.service
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
ExecStart=$PY -m howl node \\
  --data-dir $DATA \\
  --host 0.0.0.0 \\
  --port $P2P \\
  --rpc-host 127.0.0.1 \\
  --rpc-port $RPC \\
  --connect $SEED \\
  --public \\
  --no-mine
Restart=on-failure
RestartSec=25

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable howlcoin-node2
systemctl restart howlcoin-node2
sleep 4
systemctl --no-pager -l status howlcoin-node2 | head -22 || true

# firewall
if command -v ufw >/dev/null 2>&1; then
  ufw allow ${P2P}/tcp comment 'Howl node2 P2P' || true
fi

# register public seeds: primary + node2
"$PY" - <<'PY'
import json, time
from pathlib import Path

path = Path("/var/lib/howlcoin/public_seeds.json")
seeds = [
    {
        "id": "primary",
        "host": "147.182.223.204",
        "port": 42069,
        "endpoint": "147.182.223.204:42069",
        "role": "primary",
        "source": "static",
        "public": True,
        "notes": "Primary Howlscan seed",
    },
    {
        "id": "node2",
        "host": "147.182.223.204",
        "port": 42071,
        "endpoint": "147.182.223.204:42071",
        "role": "seed",
        "source": "operator",
        "public": True,
        "notes": "secondary public node (node2)",
        "meta": {"service": "howlcoin-node2"},
    },
]
path.write_text(json.dumps({"updated_at": time.time(), "seeds": seeds}, indent=2) + "\n")
print("wrote", path)
try:
    from howl.seeds import list_seeds
    print(json.dumps(list_seeds(probe=True), indent=2)[:2000])
except Exception as e:
    print("list_seeds skip:", e)
PY

systemctl restart howlcoin-explorer 2>/dev/null || true
systemctl restart howl-agents 2>/dev/null || true
sleep 2

echo "=== listeners ==="
ss -lntp | grep -E '42069|42071|42072' || true
echo "=== seeds API ==="
curl -sS -H 'User-Agent: HowlOps/1.0' http://127.0.0.1:42080/api/public/seeds | python3 -m json.tool | head -60
echo
echo "DONE — public endpoints:"
echo "  primary: 147.182.223.204:42069"
echo "  node2:   147.182.223.204:42071"
echo "logs: journalctl -u howlcoin-node2 -f"
