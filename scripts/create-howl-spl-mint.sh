#!/usr/bin/env bash
# Create wrapped HOWL (wHOWL) SPL mint on Solana mainnet and wire HOWL_SPL_MINT.
#
# As root on Howlscan VPS:
#   bash /opt/howlcoin/scripts/create-howl-spl-mint.sh
#   bash /opt/howlcoin/scripts/create-howl-spl-mint.sh --decimals 8
#   bash /opt/howlcoin/scripts/create-howl-spl-mint.sh --dry-run
#
# Requires SOL in the treasury keypair for rent (~0.002–0.01 SOL).
# Uses: /var/lib/howlcoin/bridge-sol-treasury.json (mint authority + fee payer)
#
set -euo pipefail

DATA_DIR="${HOWL_PUBLIC_DATA:-${HOWL_BRIDGE_DATA:-/var/lib/howlcoin}}"
INSTALL_DIR="${HOWL_DIR:-/opt/howlcoin}"
KEYPAIR="${HOWL_WRAP_SOL_KEYPAIR:-$DATA_DIR/bridge-sol-treasury.json}"
DECIMALS=8
DRY=0
RPC="${SOLANA_RPC:-https://api.mainnet-beta.solana.com}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --data-dir) DATA_DIR="$2"; KEYPAIR="$DATA_DIR/bridge-sol-treasury.json"; shift 2 ;;
    --keypair) KEYPAIR="$2"; shift 2 ;;
    --decimals) DECIMALS="$2"; shift 2 ;;
    --rpc) RPC="$2"; shift 2 ;;
    --dry-run) DRY=1; shift ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *) echo "Unknown: $1" >&2; exit 1 ;;
  esac
done

if [[ "$(id -u)" -ne 0 ]]; then
  echo "WARN: not root — may not write systemd env; continuing" >&2
fi

mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR" 2>/dev/null || true

export PATH="${HOME}/.local/share/solana/install/active_release/bin:${PATH}:/root/.local/share/solana/install/active_release/bin"

ensure_solana() {
  if command -v solana >/dev/null 2>&1 && command -v spl-token >/dev/null 2>&1; then
    return 0
  fi
  echo "-- installing Solana CLI (Anza) --"
  sh -c "$(curl -sSfL https://release.anza.xyz/stable/install)"
  export PATH="${HOME}/.local/share/solana/install/active_release/bin:${PATH}:/root/.local/share/solana/install/active_release/bin"
  if ! command -v solana >/dev/null 2>&1; then
    echo "FAIL: solana CLI not on PATH after install" >&2
    exit 1
  fi
}

ensure_solana

if [[ ! -f "$KEYPAIR" ]]; then
  echo "FAIL: missing Solana keypair $KEYPAIR" >&2
  echo "  Run: bash $INSTALL_DIR/scripts/install-howl-bridge.sh --bootstrap-only" >&2
  exit 1
fi

solana config set --url "$RPC" >/dev/null
solana config set --keypair "$KEYPAIR" >/dev/null

ADDR=$(solana address --keypair "$KEYPAIR")
BAL=$(solana balance --keypair "$KEYPAIR" 2>/dev/null || echo "0 SOL")
echo "== Howl SPL mint =="
echo "  authority=$ADDR"
echo "  balance=$BAL"
echo "  decimals=$DECIMALS"
echo "  rpc=$RPC"

if [[ "$DRY" -eq 1 ]]; then
  echo "dry-run: would create mint with fee payer / authority $ADDR"
  exit 0
fi

# Need a little SOL for rent
if ! solana balance --keypair "$KEYPAIR" 2>/dev/null | grep -qE '^[1-9]|0\.[0-9]*[1-9]'; then
  echo "FAIL: treasury has ~0 SOL. Fund $ADDR with ~0.05 SOL for rent, then re-run." >&2
  exit 1
fi

MINT_OUT="$DATA_DIR/howl-spl-mint.json"
META="$DATA_DIR/howl-spl-mint.meta.json"

if [[ -f "$META" ]]; then
  EXISTING=$(python3 -c "import json;print(json.load(open('$META')).get('mint',''))" 2>/dev/null || true)
  if [[ -n "${EXISTING:-}" ]]; then
    echo "Mint already recorded: $EXISTING"
    echo "  meta=$META"
    echo "Set HOWL_SPL_MINT=$EXISTING (see below) if not already."
    MINT="$EXISTING"
  fi
fi

if [[ -z "${MINT:-}" ]]; then
  echo "-- creating SPL mint --"
  # Create mint; mint authority = fee payer (treasury)
  OUT=$(spl-token create-token --decimals "$DECIMALS" --fee-payer "$KEYPAIR" --mint-authority "$ADDR" 2>&1) || {
    echo "$OUT" >&2
    exit 1
  }
  echo "$OUT"
  MINT=$(echo "$OUT" | grep -Eo 'Creating token [1-9A-HJ-NP-Za-km-z]{32,44}' | awk '{print $3}' | head -1)
  if [[ -z "$MINT" ]]; then
    MINT=$(echo "$OUT" | grep -Eo '[1-9A-HJ-NP-Za-km-z]{32,44}' | head -1)
  fi
  if [[ -z "$MINT" ]]; then
    echo "FAIL: could not parse mint address from spl-token output" >&2
    exit 1
  fi
  # Optional: create ATA for treasury to hold inventory
  spl-token create-account "$MINT" --owner "$ADDR" --fee-payer "$KEYPAIR" 2>/dev/null || true

  python3 - <<PY
import json, time
meta = {
  "mint": "$MINT",
  "symbol": "wHOWL",
  "name": "Wrapped HOWL",
  "decimals": $DECIMALS,
  "mint_authority": "$ADDR",
  "freeze_authority": None,
  "created_at": int(time.time()),
  "explorer": f"https://solscan.io/token/$MINT",
  "note": "1 wHOWL ≈ 1 native HOWL via Howl Wrap (semi-custodial)",
}
open("$META","w").write(json.dumps(meta, indent=2)+"\n")
print("wrote", "$META")
PY
fi

echo "-- wiring env --"
ENV_FILE="$DATA_DIR/bridge.env"
touch "$ENV_FILE"
# strip old lines
grep -vE '^(HOWL_SPL_MINT|HOWL_WRAP_ENABLED|HOWL_WRAP_SOL_TREASURY|HOWL_WRAP_HOWL_DEPOSIT)=' "$ENV_FILE" > "${ENV_FILE}.tmp" || true
mv "${ENV_FILE}.tmp" "$ENV_FILE"

# Resolve HOWL deposit (hot wallet address)
HOWL_DEP=""
if [[ -f "$DATA_DIR/bridge-bootstrap.json" ]]; then
  HOWL_DEP=$(python3 -c "import json;print(json.load(open('$DATA_DIR/bridge-bootstrap.json')).get('howl_hot_wallet',{}).get('address',''))" 2>/dev/null || true)
fi
if [[ -z "$HOWL_DEP" && -f "$DATA_DIR/bridge-hot-wallet.json" ]]; then
  HOWL_DEP=$(python3 - <<'PY'
import json,sys
from pathlib import Path
sys.path.insert(0,"/opt/howlcoin")
try:
  from howl.wallet import Wallet
  w=Wallet.load(Path("/var/lib/howlcoin/bridge-hot-wallet.json"))
  print(w.address)
except Exception:
  d=json.load(open("/var/lib/howlcoin/bridge-hot-wallet.json"))
  print(d.get("address") or d.get("howl_address") or "")
PY
)
fi

{
  echo "HOWL_SPL_MINT=$MINT"
  echo "HOWL_WRAP_ENABLED=1"
  echo "HOWL_WRAP_SOL_TREASURY=$ADDR"
  if [[ -n "$HOWL_DEP" ]]; then
    echo "HOWL_WRAP_HOWL_DEPOSIT=$HOWL_DEP"
  fi
  echo "HOWL_WRAP_FEE_BPS=${HOWL_WRAP_FEE_BPS:-50}"
  echo "HOWL_WRAP_MIN_HOWL=${HOWL_WRAP_MIN_HOWL:-1}"
  echo "HOWL_WRAP_MAX_HOWL=${HOWL_WRAP_MAX_HOWL:-10000000}"
  echo "HOWL_WRAP_DATA=$DATA_DIR"
} >> "$ENV_FILE"

# Ensure explorer loads bridge.env
DROPIN_DIR=/etc/systemd/system/howlcoin-explorer.service.d
if [[ -d /etc/systemd/system ]]; then
  mkdir -p "$DROPIN_DIR"
  cat > "$DROPIN_DIR/bridge.conf" <<EOF
[Service]
EnvironmentFile=-$ENV_FILE
EOF
  systemctl daemon-reload || true
  systemctl restart howlcoin-explorer || true
fi

# Install wrap relayer unit if present
if [[ -f "$INSTALL_DIR/deploy/howl-wrap-relayer.service" ]]; then
  cp "$INSTALL_DIR/deploy/howl-wrap-relayer.service" /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable --now howl-wrap-relayer || true
fi

echo ""
echo "OK · wHOWL mint created"
echo "  mint:      $MINT"
echo "  authority: $ADDR"
echo "  explorer:  https://solscan.io/token/$MINT"
echo "  meta:      $META"
echo "  env:       $ENV_FILE"
echo ""
echo "Next:"
echo "  1) Fund wrap inventory: keep native HOWL on deposit address for unwraps"
echo "  2) curl -sS https://howlscan.org/api/public/wrap | python3 -m json.tool"
echo "  3) In wallet → Swap → Wrap HOWL"
echo "  4) Optional Metaplex token metadata via Solana Explorer / metaboss"
