#!/bin/zsh
# Install "Howlcoin Mine.command" on the Desktop for one-click mining.
set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$REPO/scripts/Howlcoin Mine.command"
DEST="$HOME/Desktop/Howlcoin Mine.command"

if [[ ! -f "$SRC" ]]; then
  echo "Missing launcher: $SRC"
  exit 1
fi

cp "$SRC" "$DEST"
chmod +x "$DEST"
# Clear quarantine so double-click works without Gatekeeper nag (best-effort)
xattr -d com.apple.quarantine "$DEST" 2>/dev/null || true

echo "Installed → $DEST"
echo "Double-click \"Howlcoin Mine\" on your Desktop."
echo "It will: stop old node → connect public seed → mine forever → open dashboard."
