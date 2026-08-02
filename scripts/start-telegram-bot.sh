#!/usr/bin/env bash
# Start Howlcoin Telegram bot with a working Python (avoids broken conda anyio).
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer system Python 3.9+; fall back to conda if needed
if [ -x /usr/bin/python3 ]; then
  PY=/usr/bin/python3
else
  PY=python3
fi

if [ -z "${HOWL_TELEGRAM_TOKEN:-}" ]; then
  echo "Set your bot token first:"
  echo "  export HOWL_TELEGRAM_TOKEN='123456:ABC...'"
  echo "Then run this script again."
  exit 1
fi

export HOWL_SEED="${HOWL_SEED:-147.182.223.204:42069}"
export HOWL_DATA_DIR="${HOWL_DATA_DIR:-$HOME/.howlcoin-telegram}"

echo "Using: $PY"
"$PY" -m pip install -q 'python-telegram-bot>=21' 'anyio>=4' -r requirements.txt 2>/dev/null || \
  "$PY" -m pip install --user -q 'python-telegram-bot>=21' 'anyio>=4' -r requirements.txt

echo "Starting Howlcoin Telegram bot…"
echo "Data: $HOWL_DATA_DIR"
echo "Seed: $HOWL_SEED"
echo "Leave this window open. Ctrl+C to stop."
exec "$PY" -m howl telegram --seed "$HOWL_SEED"
