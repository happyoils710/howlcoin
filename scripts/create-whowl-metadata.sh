#!/usr/bin/env bash
# Attach Metaplex Token Metadata so Solscan shows name/symbol wHOWL.
set -euo pipefail
MINT="${HOWL_SPL_MINT:-HYRKhV2Y9HEtKCCHSgH18Zfo4U9Ln9vAg2dCmBJSLWaG}"
KP="${HOWL_WRAP_SOL_KEYPAIR:-/var/lib/howlcoin/bridge-sol-treasury.json}"
URI="${WHOWL_METADATA_URI:-https://howlscan.org/assets/whowl-token.json}"
NAME="${WHOWL_NAME:-Wrapped HOWL}"
SYMBOL="${WHOWL_SYMBOL:-wHOWL}"
RPC="${SOLANA_RPC:-https://api.mainnet-beta.solana.com}"
export PATH="${HOME}/.local/share/solana/install/active_release/bin:/usr/local/bin:$PATH"

if [[ ! -f "$KP" ]]; then
  echo "missing keypair $KP" >&2
  exit 1
fi

if ! command -v metaboss >/dev/null 2>&1; then
  echo "Installing metaboss 0.49…"
  curl -fsSL -o /usr/local/bin/metaboss \
    "https://github.com/samuelvanderwaal/metaboss/releases/download/v0.49.0/metaboss-ubuntu-latest"
  chmod +x /usr/local/bin/metaboss
fi

tmp=$(mktemp)
cat > "$tmp" <<JSON
{
  "name": "$NAME",
  "symbol": "$SYMBOL",
  "uri": "$URI",
  "seller_fee_basis_points": 0,
  "creators": null
}
JSON

echo "Creating metadata for $MINT"
metaboss create metadata -k "$KP" -a "$MINT" -m "$tmp" -r "$RPC" -P medium
rm -f "$tmp"
echo "OK · Solscan may take a few minutes: https://solscan.io/token/$MINT"
