#!/usr/bin/env bash
# Howlcoin public seed — run ON a fresh Ubuntu/Debian VPS as root
# Usage: curl -sSL ... | bash   OR   bash vps-install.sh
set -euo pipefail

REPO="${HOWL_REPO:-https://github.com/happyoils710/howlcoin.git}"
INSTALL_DIR="${HOWL_DIR:-/opt/howlcoin}"
DATA_DIR="${HOWL_DATA:-/var/lib/howlcoin}"
P2P_PORT="${HOWL_P2P_PORT:-42069}"
RPC_PORT="${HOWL_RPC_PORT:-42070}"

export DEBIAN_FRONTEND=noninteractive

echo "==> Howlcoin VPS seed installer"
echo "    repo:  $REPO"
echo "    dir:   $INSTALL_DIR"
echo "    data:  $DATA_DIR"
echo "    p2p:   $P2P_PORT"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root (sudo -i)"
  exit 1
fi

apt-get update -y
apt-get install -y python3 python3-pip python3-venv git ufw curl ca-certificates

if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" pull --ff-only || true
fi

cd "$INSTALL_DIR"
python3 -m venv /opt/howlcoin-venv
# shellcheck disable=SC1091
source /opt/howlcoin-venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/chain.json" ]; then
  python3 -m howl --data-dir "$DATA_DIR" init
fi

# Firewall: SSH + P2P only (dashboard stays localhost)
ufw allow OpenSSH
ufw allow "${P2P_PORT}/tcp"
ufw --force enable

cat >/etc/systemd/system/howlcoin.service <<EOF
[Unit]
Description=Howlcoin Scrypt P2P seed (HOWL)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
Environment=PATH=/opt/howlcoin-venv/bin:/usr/bin
ExecStart=/opt/howlcoin-venv/bin/python3 -m howl --data-dir ${DATA_DIR} node --host 0.0.0.0 --port ${P2P_PORT} --rpc-host 127.0.0.1 --rpc-port ${RPC_PORT}
Restart=always
RestartSec=5
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now howlcoin.service
sleep 2
systemctl --no-pager --full status howlcoin.service || true

PUBLIC_IP="$(curl -4 -s --max-time 5 ifconfig.me || curl -4 -s --max-time 5 icanhazip.com || echo YOUR_VPS_IP)"
echo
echo "=============================================="
echo " Howlcoin seed is LIVE"
echo " Miners connect with:"
echo
echo "   python3 -m howl node --connect ${PUBLIC_IP}:${P2P_PORT}"
echo
echo " Service:  systemctl status howlcoin"
echo " Logs:     journalctl -u howlcoin -f"
echo " Data:     ${DATA_DIR}"
echo "=============================================="
