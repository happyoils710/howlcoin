#!/usr/bin/env bash
# Install + enable Howl Swap (Phase A) on a Howlscan VPS.
#
# As root:
#   bash /opt/howlcoin/scripts/install-howl-bridge.sh
#   bash /opt/howlcoin/scripts/install-howl-bridge.sh --howl-per-usdc 1
#   bash /opt/howlcoin/scripts/install-howl-bridge.sh --sol-treasury <base58>
#   bash /opt/howlcoin/scripts/install-howl-bridge.sh --bootstrap-only
#   bash /opt/howlcoin/scripts/install-howl-bridge.sh --disable
#
set -euo pipefail

INSTALL_DIR="${HOWL_DIR:-/opt/howlcoin}"
DATA_DIR="${HOWL_PUBLIC_DATA:-${HOWL_BRIDGE_DATA:-${HOWL_DATA:-/var/lib/howlcoin}}}"
BOOTSTRAP_ONLY=0
DISABLE=0
EXTRA_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bootstrap-only) BOOTSTRAP_ONLY=1; shift ;;
    --disable) DISABLE=1; EXTRA_ARGS+=(--disable); shift ;;
    --data-dir) DATA_DIR="$2"; EXTRA_ARGS+=(--data-dir "$2"); shift 2 ;;
    --sol-treasury) EXTRA_ARGS+=(--sol-treasury "$2"); shift 2 ;;
    --howl-per-usdc) EXTRA_ARGS+=(--howl-per-usdc "$2"); shift 2 ;;
    --howl-per-sol) EXTRA_ARGS+=(--howl-per-sol "$2"); shift 2 ;;
    --force) EXTRA_ARGS+=(--force); shift ;;
    --max-sol) EXTRA_ARGS+=(--max-sol "$2"); shift 2 ;;
    --min-sol) EXTRA_ARGS+=(--min-sol "$2"); shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "FAIL: missing $INSTALL_DIR" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "FAIL: run as root (systemd + secret files)" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" 2>/dev/null || true

# Prefer venv python (base58 + pycryptodome)
if [[ -x /opt/howlcoin-venv/bin/python3 ]]; then
  PY=/opt/howlcoin-venv/bin/python3
elif [[ -x "$INSTALL_DIR/.venv/bin/python3" ]]; then
  PY="$INSTALL_DIR/.venv/bin/python3"
else
  PY=python3
fi

echo "== Howl Swap install =="
echo "  dir=$INSTALL_DIR data=$DATA_DIR py=$PY"

# 1) Bootstrap wallets + bridge.env
echo "-- bootstrap --"
"$PY" "$INSTALL_DIR/scripts/howl-bridge-bootstrap.py" \
  --data-dir "$DATA_DIR" \
  --systemd-dropin-dir /etc/systemd/system \
  "${EXTRA_ARGS[@]}"

ENV_FILE="$DATA_DIR/bridge.env"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "FAIL: bootstrap did not write $ENV_FILE" >&2
  exit 1
fi

if [[ "$BOOTSTRAP_ONLY" -eq 1 ]]; then
  echo "Bootstrap-only done. Run without --bootstrap-only to install systemd units."
  exit 0
fi

# 2) Install relayer unit
echo "-- systemd units --"
install -m 644 "$INSTALL_DIR/deploy/howl-bridge-relayer.service" \
  /etc/systemd/system/howl-bridge-relayer.service

# Ensure explorer + relayer both load bridge.env
for unit in howlcoin-explorer howl-bridge-relayer; do
  mkdir -p "/etc/systemd/system/${unit}.service.d"
  cat >"/etc/systemd/system/${unit}.service.d/bridge.conf" <<EOF
[Service]
EnvironmentFile=-${ENV_FILE}
EOF
done

systemctl daemon-reload

# 3) Restart explorer so /api/public/bridge goes live
if systemctl cat howlcoin-explorer &>/dev/null; then
  systemctl restart howlcoin-explorer
  sleep 1
  systemctl is-active howlcoin-explorer || echo "WARN: howlcoin-explorer not active"
else
  echo "WARN: howlcoin-explorer unit not found"
fi

# 4) Enable relayer (unless disabled)
if [[ "$DISABLE" -eq 1 ]]; then
  systemctl disable --now howl-bridge-relayer 2>/dev/null || true
  echo "Bridge disabled (relayer stopped)."
else
  systemctl enable --now howl-bridge-relayer
  sleep 1
  systemctl is-active howl-bridge-relayer || echo "WARN: howl-bridge-relayer not active"
fi

# 5) Verify
echo "-- verify --"
API="${HOWL_API:-https://howlscan.org}"
if command -v curl >/dev/null; then
  echo "  GET $API/api/public/bridge"
  curl -sS --max-time 12 "$API/api/public/bridge" | "$PY" -c '
import sys, json
try:
  j=json.load(sys.stdin)
except Exception as e:
  print("  FAIL parse:", e); sys.exit(0)
print("  enabled:", j.get("enabled"))
print("  note:", (j.get("note") or "")[:100])
for a in j.get("assets") or []:
  if a.get("id")=="sol":
    print("  SOL deposit:", a.get("deposit_address") or "(empty)")
    print("  SOL min/max:", a.get("min"), "/", a.get("max"), " rate", a.get("howl_per_unit"), "HOWL/SOL")
' || echo "  WARN: could not reach API (DNS/firewall? try localhost)"
  # local fallback
  curl -sS --max-time 5 http://127.0.0.1:42080/api/public/bridge 2>/dev/null | "$PY" -c '
import sys,json
try:
  j=json.load(sys.stdin)
  print("  local enabled:", j.get("enabled"), "sol:", next((a.get("deposit_address") for a in j.get("assets")or[] if a.get("id")=="sol"),""))
except Exception:
  pass
' || true
fi

# Hot wallet balance hint via public API if possible
HOW_ADDR=$("$PY" -c "
import json
from pathlib import Path
p=Path('$DATA_DIR')/'bridge-hot-wallet.json'
if p.exists():
  d=json.loads(p.read_text())
  keys=d.get('keys') or []
  if keys: print(keys[0].get('address') or keys[0].get('addr') or '')
" 2>/dev/null || true)

echo
echo "== Howl Swap install done =="
echo "  env:      $ENV_FILE"
echo "  relayer:  systemctl status howl-bridge-relayer"
echo "  logs:     journalctl -u howl-bridge-relayer -n 50 --no-pager"
echo "  status:   cat $DATA_DIR/bridge-bootstrap.json"
if [[ -n "${HOW_ADDR:-}" ]]; then
  echo "  FUND HOWL → $HOW_ADDR"
  echo "    (inventory for payouts; min swap ~990 HOWL at default rates)"
fi
SOL_ADDR=$(grep -E '^HOWL_BRIDGE_SOL_TREASURY=' "$ENV_FILE" | cut -d= -f2- || true)
if [[ -n "${SOL_ADDR:-}" ]]; then
  echo "  USERS SEND SOL/USDC → $SOL_ADDR"
fi
echo "  Wallet UI shows Howl Swap when enabled=true"
echo
if [[ "$DISABLE" -eq 0 ]]; then
  echo "Bridge is configured. It will complete swaps only after:"
  echo "  1) HOWL hot wallet is funded"
  echo "  2) relayer is active (check journalctl)"
  echo "  3) seed/miner is producing blocks"
fi
