#!/usr/bin/env bash
# Start Howlcoin seed + free public tunnel (bore.pub)
set -euo pipefail
export PATH="${HOME}/.cargo/bin:/usr/local/bin:${PATH}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

mkdir -p /tmp
echo "Starting Howlcoin seed on 0.0.0.0:42069 …"
if ! lsof -nP -iTCP:42069 -sTCP:LISTEN >/dev/null 2>&1; then
  nohup python3 -m howl node --host 0.0.0.0 --port 42069 \
    --rpc-host 127.0.0.1 --rpc-port 42070 \
    > /tmp/howl-seed.log 2>&1 &
  echo $! > /tmp/howl-seed.pid
  sleep 2
else
  echo "  (already listening on 42069)"
fi

if ! command -v bore >/dev/null 2>&1; then
  echo "Installing bore-cli …"
  cargo install bore-cli --locked
fi

# stop old bore
if [ -f /tmp/howl-bore.pid ]; then
  kill "$(cat /tmp/howl-bore.pid)" 2>/dev/null || true
fi

echo "Opening public tunnel bore.pub → localhost:42069 …"
nohup bore local 42069 --to bore.pub > /tmp/howl-bore.log 2>&1 &
echo $! > /tmp/howl-bore.pid
sleep 3
echo
grep -E "listening at|remote_port" /tmp/howl-bore.log || cat /tmp/howl-bore.log
echo
echo "Dashboard: http://127.0.0.1:42070/"
echo "Share:     python3 -m howl node --connect bore.pub:<PORT from above>"
echo "Logs:      /tmp/howl-seed.log  /tmp/howl-bore.log"
