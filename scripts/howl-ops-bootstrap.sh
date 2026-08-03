#!/usr/bin/env bash
# Howlcoin VPS ops lock-in: deploy code, restart services, charts sampler, health cron, verify.
#
# On the VPS as root:
#   bash /opt/howlcoin/scripts/howl-ops-bootstrap.sh
#
# From your Mac (interactive SSH / passphrase OK):
#   ssh howl-vps 'bash /opt/howlcoin/scripts/howl-ops-bootstrap.sh'
#   # or pull first:
#   ssh howl-vps 'cd /opt/howlcoin && git fetch origin && git reset --hard origin/main && bash scripts/howl-ops-bootstrap.sh'

set -euo pipefail

INSTALL_DIR="${HOWL_DIR:-/opt/howlcoin}"
DATA_DIR="${HOWL_PUBLIC_DATA:-${HOWL_DATA:-/var/lib/howlcoin}}"
API="${HOWL_API:-https://howlscan.org}"
EXPECT_VER="${HOWL_EXPECT_VERSION:-0.6.4}"

echo "== Howlcoin ops bootstrap =="
echo "  dir=$INSTALL_DIR data=$DATA_DIR"

if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "FAIL: missing $INSTALL_DIR" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"

# 1) Latest code
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "-- git --"
  cd "$INSTALL_DIR"
  git fetch origin
  git reset --hard origin/main
  git log -1 --oneline
else
  echo "WARN: no git repo at $INSTALL_DIR — skipping pull"
fi

# 2) Restart core services if present
echo "-- services --"
for svc in howlcoin howlcoin-explorer; do
  if systemctl list-unit-files "$svc.service" &>/dev/null || systemctl cat "$svc" &>/dev/null; then
    systemctl restart "$svc" || true
    sleep 1
    systemctl is-active "$svc" || echo "WARN: $svc not active"
  else
    echo "WARN: $svc unit not found"
  fi
done

# 3) Auto-mine drop-in (optional, idempotent)
if systemctl cat howlcoin &>/dev/null; then
  if ! systemctl cat howlcoin 2>/dev/null | grep -q "HOWL_AUTO_MINE=1"; then
    echo "-- enabling HOWL_AUTO_MINE=1 drop-in --"
    mkdir -p /etc/systemd/system/howlcoin.service.d
    cat >/etc/systemd/system/howlcoin.service.d/automine.conf <<'EOF'
[Service]
Environment=HOWL_AUTO_MINE=1
EOF
    systemctl daemon-reload
    systemctl restart howlcoin || true
  else
    echo "OK HOWL_AUTO_MINE already configured"
  fi
fi

# 4) Howl Charts 24/7 sampler
echo "-- charts sampler --"
if [[ -f "$INSTALL_DIR/scripts/install-howl-charts-sampler.sh" ]]; then
  bash "$INSTALL_DIR/scripts/install-howl-charts-sampler.sh"
else
  echo "WARN: install-howl-charts-sampler.sh missing"
fi

# 5) Health cron (every 15 min)
echo "-- health cron --"
HEALTH="$INSTALL_DIR/scripts/howl-health-check.sh"
if [[ -f "$HEALTH" ]]; then
  chmod +x "$HEALTH"
  CRON_LINE="*/15 * * * * HOWL_API=${API} MAX_TIP_AGE=7200 ${HEALTH} >> /var/log/howl-health.log 2>&1"
  # install for root crontab if not present
  EXISTING="$(crontab -l 2>/dev/null || true)"
  if grep -q "howl-health-check.sh" <<<"$EXISTING"; then
    echo "OK health cron already installed"
  else
    (echo "$EXISTING"; echo "$CRON_LINE") | crontab -
    echo "OK installed health cron → /var/log/howl-health.log"
  fi
else
  echo "WARN: howl-health-check.sh missing"
fi

# 6) Verify
echo "-- verify --"
if [[ -f "$INSTALL_DIR/scripts/howl-deploy-verify.sh" ]]; then
  chmod +x "$INSTALL_DIR/scripts/howl-deploy-verify.sh"
  bash "$INSTALL_DIR/scripts/howl-deploy-verify.sh" --local --api "$API" --expect "$EXPECT_VER" --data "$DATA_DIR" --dir "$INSTALL_DIR" \
    || true
else
  echo "WARN: howl-deploy-verify.sh missing"
fi

echo
echo "== Bootstrap done =="
echo "  Sampler: systemctl status howl-charts-sampler.timer"
echo "  Health:  tail -f /var/log/howl-health.log"
echo "  Howl Swap stays offline until you set HOWL_BRIDGE_* + relayer (by design)"
echo "  Wallet soft-hides Howl Swap when bridge disabled"
