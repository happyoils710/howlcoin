#!/usr/bin/env bash
# Clean agent dry-run fleet noise; keep only the primary public seed.
set -euo pipefail

AGENTS_STATE="${HOWL_AGENTS_STATE:-/var/lib/howlcoin/agents}"
SEEDS_FILE="${HOWL_SEEDS_FILE:-/var/lib/howlcoin/public_seeds.json}"
PRIMARY="${HOWL_PUBLIC_SEEDS:-147.182.223.204:42069}"
PRIMARY_HOST="${PRIMARY%%:*}"
PRIMARY_PORT="${PRIMARY##*:}"

mkdir -p "$AGENTS_STATE/infra" "$(dirname "$SEEDS_FILE")"

python3 - <<PY
import json, time
from pathlib import Path

fleet = Path("$AGENTS_STATE") / "infra" / "fleet.json"
fleet.parent.mkdir(parents=True, exist_ok=True)
fleet.write_text("[]\n")
print("cleared", fleet)

# Drop dry-run node dirs metadata only (leave launch templates optional)
depin = Path("$AGENTS_STATE") / "infra" / "depin" / "local" / "nodes"
if depin.is_dir():
    n = sum(1 for _ in depin.iterdir())
    print("local node template dirs present:", n, "(left on disk; not running)")

seeds = {
    "updated_at": time.time(),
    "seeds": [{
        "id": "primary",
        "host": "$PRIMARY_HOST",
        "port": int("$PRIMARY_PORT"),
        "endpoint": "$PRIMARY",
        "role": "primary",
        "source": "static",
        "public": True,
        "status": "unknown",
        "notes": "Primary Howlscan seed",
    }],
}
Path("$SEEDS_FILE").write_text(json.dumps(seeds, indent=2) + "\n")
print("reset seeds ->", "$SEEDS_FILE")
PY

if systemctl is-active --quiet howl-agents 2>/dev/null; then
  systemctl restart howl-agents
  echo "restarted howl-agents"
fi
if systemctl is-active --quiet howlcoin-explorer 2>/dev/null; then
  systemctl restart howlcoin-explorer
  echo "restarted howlcoin-explorer"
fi

sleep 2
echo "--- seeds ---"
curl -sS -H 'User-Agent: HowlOps/1.0' http://127.0.0.1:42080/api/public/seeds 2>/dev/null \
  | python3 -m json.tool 2>/dev/null | head -40 || true
echo "DONE cleanup"
