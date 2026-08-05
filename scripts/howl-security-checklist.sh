#!/usr/bin/env bash
# Operator security checklist for howlscan.org / wrap / bridge
set -euo pipefail
DATA="${HOWL_PUBLIC_DATA:-/var/lib/howlcoin}"
echo "== Howl security checklist =="
echo "data: $DATA"
fail=0
check() {
  if "$@"; then echo "OK  $*"; else echo "FAIL $*"; fail=1; fi
}
# key file permissions
for f in bridge-hot-wallet.json bridge-sol-treasury.json wallet.json; do
  p="$DATA/$f"
  if [[ -f "$p" ]]; then
    mode=$(stat -c '%a' "$p" 2>/dev/null || stat -f '%Lp' "$p")
    if [[ "$mode" == "600" || "$mode" == "400" ]]; then
      echo "OK  $f mode $mode"
    else
      echo "WARN $f mode $mode (prefer 600)"
      chmod 600 "$p" 2>/dev/null || true
    fi
  fi
done
# world-readable order files with possible PII — tighten
for f in wrap_orders.json bridge_orders.json howl_accounts.json howl_sessions.json; do
  p="$DATA/$f"
  if [[ -f "$p" ]]; then
    chmod 600 "$p" 2>/dev/null || true
    echo "OK  tightened $f"
  fi
done
# mint freeze authority should be empty
if command -v spl-token >/dev/null 2>&1 && [[ -n "${HOWL_SPL_MINT:-}" ]]; then
  if spl-token display "$HOWL_SPL_MINT" 2>/dev/null | grep -qi 'Freeze authority: (not set)'; then
    echo "OK  wHOWL freeze authority not set"
  else
    echo "WARN check freeze authority on mint"
  fi
fi
# services
for s in howlcoin howlcoin-explorer howl-bridge-relayer howl-wrap-relayer; do
  if systemctl is-active --quiet "$s" 2>/dev/null; then echo "OK  $s active"; else echo "INFO $s not active"; fi
done
echo "== done (fail=$fail) =="
exit $fail
