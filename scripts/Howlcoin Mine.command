#!/bin/zsh
# Howlcoin — double-click on macOS Desktop to connect + mine forever
# Opens local dashboard and joins the public seed (howlscan.org).

set -e
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:$PATH"

HOWL_DIR="${HOWL_DIR:-$HOME/Desktop/howlcoin}"
PUBLIC_SEED="${HOWL_SEED:-147.182.223.204:42069}"
DASH_URL="http://127.0.0.1:42070/"
LOG_DIR="${HOME}/.howlcoin"
LOG_FILE="${LOG_DIR}/desktop-mine.log"

mkdir -p "$LOG_DIR"
cd "$HOWL_DIR" 2>/dev/null || {
  osascript -e 'display alert "Howlcoin not found" message "Expected repo at ~/Desktop/howlcoin. Clone it there, or set HOWL_DIR." as critical'
  exit 1
}

# Prefer python3 with howl importable from this repo
if ! command -v python3 >/dev/null 2>&1; then
  osascript -e 'display alert "Python 3 missing" message "Install Python 3, then try again." as critical'
  exit 1
fi

# Stop previous local node (this user only)
pkill -f "python3 -m howl node" 2>/dev/null || true
pkill -f "python3 -m howl go" 2>/dev/null || true
pkill -f "Python -m howl node" 2>/dev/null || true
pkill -f "Python -m howl go" 2>/dev/null || true
pkill -f "python.*-m howl node" 2>/dev/null || true
pkill -f "python.*-m howl go" 2>/dev/null || true
sleep 0.8

# Optional soft update (ignore failures if offline)
git fetch origin --quiet 2>/dev/null || true
git merge --ff-only origin/main --quiet 2>/dev/null || true

# Install deps if needed (quiet)
python3 -c "import Crypto" 2>/dev/null || python3 -m pip install -q -r requirements.txt 2>/dev/null || true

clear 2>/dev/null || true
echo "========================================"
echo "  Howlcoin — Connect & Mine Forever"
echo "========================================"
echo "  Repo   : $HOWL_DIR"
echo "  Seed   : $PUBLIC_SEED"
echo "  Dash   : $DASH_URL"
echo "  Log    : $LOG_FILE"
echo "========================================"
echo ""
echo "This window stays open while you mine."
echo "Close it or press Ctrl+C to stop."
echo ""

# Notify + open browser shortly after start
(
  sleep 2
  open "$DASH_URL" 2>/dev/null || true
  osascript -e 'display notification "Connected to public seed · mining forever" with title "Howlcoin" sound name "Glass"' 2>/dev/null || true
) &

export HOWL_AUTO_MINE=1
export PYTHONUNBUFFERED=1

# Run in foreground so double-click Terminal window shows progress
exec python3 -m howl go --connect "$PUBLIC_SEED" 2>&1 | tee -a "$LOG_FILE"
