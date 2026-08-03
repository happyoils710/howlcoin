#!/usr/bin/env bash
# Install Howl Charts 24/7 sampler on a Howlscan VPS (run as root).
# Usage (on server):
#   bash /opt/howlcoin/scripts/install-howl-charts-sampler.sh
set -euo pipefail

INSTALL_DIR="${HOWL_DIR:-/opt/howlcoin}"
DATA_DIR="${HOWL_PUBLIC_DATA:-${HOWL_DATA:-/var/lib/howlcoin}}"

if [[ ! -d "$INSTALL_DIR" ]]; then
  echo "missing $INSTALL_DIR — clone howlcoin first" >&2
  exit 1
fi

mkdir -p "$DATA_DIR"
install -m 644 "$INSTALL_DIR/deploy/howl-charts-sampler.service" /etc/systemd/system/howl-charts-sampler.service
install -m 644 "$INSTALL_DIR/deploy/howl-charts-sampler.timer" /etc/systemd/system/howl-charts-sampler.timer

# Ensure data path is set even if unit file is older
mkdir -p /etc/systemd/system/howl-charts-sampler.service.d
cat >/etc/systemd/system/howl-charts-sampler.service.d/data.conf <<EOF
[Service]
Environment=HOWL_PUBLIC_DATA=${DATA_DIR}
EOF

systemctl daemon-reload
systemctl enable --now howl-charts-sampler.timer
# Fire once immediately so history starts now
systemctl start howl-charts-sampler.service || true

echo "Installed Howl Charts sampler"
echo "  timer:   systemctl status howl-charts-sampler.timer"
echo "  run now: systemctl start howl-charts-sampler.service"
echo "  logs:    journalctl -u howl-charts-sampler -n 50 --no-pager"
echo "  data:    ${DATA_DIR}/howl_charts_samples.json"
ls -la "${DATA_DIR}/howl_charts_samples.json" 2>/dev/null || echo "  (samples file appears after first successful run)"
