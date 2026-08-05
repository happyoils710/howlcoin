#!/usr/bin/env bash
# Attach Metaplex Token Metadata so Solscan shows name/symbol wHOWL.
set -euo pipefail
MINT="${HOWL_SPL_MINT:-HYRKhV2Y9HEtKCCHSgH18Zfo4U9Ln9vAg2dCmBJSLWaG}"
KP="${HOWL_WRAP_SOL_KEYPAIR:-/var/lib/howlcoin/bridge-sol-treasury.json}"
URI="${WHOWL_METADATA_URI:-https://howlscan.org/assets/whowl-token.json}"
NAME="${WHOWL_NAME:-Wrapped HOWL}"
SYMBOL="${WHOWL_SYMBOL:-wHOWL}"
export PATH="${HOME}/.local/share/solana/install/active_release/bin:/usr/local/bin:$PATH"

if [[ ! -f "$KP" ]]; then
  echo "missing keypair $KP" >&2
  exit 1
fi

# Install metaboss if missing
if ! command -v metaboss >/dev/null 2>&1; then
  echo "Installing metaboss…"
  tmp=$(mktemp -d)
  # pin a known linux x86_64 release
  curl -fsSL -o "$tmp/metaboss.tar.gz" \
    "https://github.com/samuelvanderwaal/metaboss/releases/download/v0.43.1/metaboss-ubuntu-latest.tar.gz" \
    || curl -fsSL -o "$tmp/metaboss" \
    "https://github.com/samuelvanderwaal/metaboss/releases/download/v0.43.1/metaboss-ubuntu-latest"
  if [[ -f "$tmp/metaboss.tar.gz" ]]; then
    tar -xzf "$tmp/metaboss.tar.gz" -C "$tmp"
    install -m 755 "$tmp"/metaboss* /usr/local/bin/metaboss 2>/dev/null \
      || install -m 755 "$(find "$tmp" -type f -name 'metaboss' | head -1)" /usr/local/bin/metaboss
  else
    install -m 755 "$tmp/metaboss" /usr/local/bin/metaboss
  fi
fi

echo "Creating metadata for $MINT"
echo "  name=$NAME symbol=$SYMBOL uri=$URI"

# metaboss create-metadata (v0.4x)
set +e
metaboss create-metadata \
  -k "$KP" \
  -a "$MINT" \
  -n "$NAME" \
  -s "$SYMBOL" \
  -u "$URI" \
  --sfbp \
  --rpc "https://api.mainnet-beta.solana.com" 2>&1
rc=$?
if [[ $rc -ne 0 ]]; then
  # alternate subcommand names across versions
  metaboss create metadata \
    --keypair "$KP" \
    --mint "$MINT" \
    --name "$NAME" \
    --symbol "$SYMBOL" \
    --uri "$URI" \
    --seller-fee-basis-points 0 \
    --rpc "https://api.mainnet-beta.solana.com" 2>&1
  rc=$?
fi
set -e
if [[ $rc -ne 0 ]]; then
  echo "metaboss failed — try manually: https://solscan.io/token/$MINT" >&2
  exit $rc
fi
echo "OK · Solscan may take a few minutes to refresh metadata"
echo "https://solscan.io/token/$MINT"
