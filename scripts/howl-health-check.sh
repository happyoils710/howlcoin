#!/usr/bin/env bash
# Howlcoin public-chain health check for cron / monitoring.
# Exit 0 = OK, 1 = tip too old or API failure.
#
#   ./scripts/howl-health-check.sh
#   HOWL_API=https://howlscan.org MAX_TIP_AGE=7200 ./scripts/howl-health-check.sh
# Optional: TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID for alerts

set -euo pipefail

API="${HOWL_API:-https://howlscan.org}"
MAX_AGE="${MAX_TIP_AGE:-7200}"
ENDPOINT="${API%/}/api/public/health?window=20"

alert() {
  local msg="$1"
  echo "$msg"
  if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
    curl -fsS -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
      -d "chat_id=${TELEGRAM_CHAT_ID}" \
      --data-urlencode "text=$msg" >/dev/null 2>&1 || true
  fi
}

json="$(curl -fsS --max-time 20 "$ENDPOINT" 2>/dev/null || true)"
if [[ -z "$json" ]]; then
  alert "Howlcoin health: API unreachable ($ENDPOINT)"
  exit 1
fi

read -r HEIGHT TIP_AGE STATUS DIFF MEMPOOL < <(python3 -c "
import json,sys
d=json.loads(sys.stdin.read())
print(
  d.get('height','?'),
  d.get('tip_age_seconds', 999999),
  d.get('status','unknown'),
  str(d.get('difficulty_label') or '?').replace(' ','_'),
  d.get('mempool','?'),
)
" <<<"$json")

echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) height=$HEIGHT tip_age=${TIP_AGE}s status=$STATUS diff=$DIFF mempool=$MEMPOOL"

if ! [[ "$TIP_AGE" =~ ^[0-9]+$ ]]; then
  alert "Howlcoin health: bad tip_age from API"
  exit 1
fi

if [[ "$TIP_AGE" -gt "$MAX_AGE" ]]; then
  alert "Howlcoin ALERT: tip age ${TIP_AGE}s > ${MAX_AGE}s (height $HEIGHT, status $STATUS). Check seed mining / HOWL_AUTO_MINE."
  exit 1
fi

exit 0
