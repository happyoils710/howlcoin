# Howlcoin public seed setup

Make your chain reachable so GitHub miners can:

```bash
python3 -m howl node --connect YOUR_HOST:42069
```

---

## Option A — Home router port-forward (free, fiddly)

### Your numbers (fill from your Mac)

| Item | Typical value on this network |
|------|-------------------------------|
| Seed machine LAN IP | `192.168.1.96` |
| P2P port | `42069` TCP |
| Public IP | check https://ifconfig.me |
| Router admin | often `http://192.168.1.1` |

### 1. Run the seed on the Mac

```bash
cd ~/Desktop/howlcoin
python3 -m howl node --host 0.0.0.0 --port 42069 --rpc-host 127.0.0.1 --rpc-port 42070
```

- Dashboard stays **local only** (`127.0.0.1:42070`) — safer.
- P2P listens on **all interfaces** port **42069**.

Optional: give the Mac a **DHCP reservation** / static LAN IP `192.168.1.96` in the router so the forward doesn’t break after reboot.

### 2. macOS firewall

**System Settings → Network → Firewall**

- If Firewall is **Off**, inbound usually works.
- If **On**: allow **Python** / incoming for the terminal app, or add a rule for port 42069.

CLI check:

```bash
/usr/libexec/ApplicationFirewall/socketfilterfw --getglobalstate
```

### 3. Router port forward

1. Open router admin (try `http://192.168.1.1` or `http://192.168.0.1`).
2. Log in (often on the router sticker).
3. Find **Port Forwarding** / **Virtual Server** / **NAT**.
4. Add rule:

| Field | Value |
|-------|--------|
| Name | Howlcoin |
| Protocol | **TCP** |
| External port | **42069** |
| Internal port | **42069** |
| Internal IP | **192.168.1.96** (your Mac) |
| Enable | Yes |

5. Save / reboot router if required.

### 4. Test from outside

From a phone on **cellular** (not Wi‑Fi), or a friend:

```bash
# friend with howlcoin cloned:
python3 -m howl node --connect YOUR_PUBLIC_IP:42069
```

Online port check: https://www.yougetsignal.com/tools/open-ports/  
Host = your public IP, port = `42069`.

### 5. Common failures

| Problem | Fix |
|---------|-----|
| CGNAT / no real public IP | Home forward **won’t work** → use VPS (Option B) |
| ISP blocks inbound | VPS |
| Mac sleeps | System Settings → Energy → prevent sleep when on power |
| IP changed | Recheck ifconfig.me; update SEEDS.md / friends |
| Wrong LAN IP | `ipconfig getifaddr en0` |

---

## Option B — VPS seed (recommended for “public”)

A small Linux VPS (~$4–6/mo) with a static IP is the reliable way.

### 1. Create a droplet / instance

- Provider: DigitalOcean, Linode, Vultr, Hetzner, etc.
- Image: **Ubuntu 24.04**
- Size: 1 vCPU / 1 GB RAM is enough
- Note the **public IPv4**

### 2. SSH in and install

```bash
ssh root@YOUR_VPS_IP

apt update && apt install -y python3 python3-pip git
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install -r requirements.txt --break-system-packages 2>/dev/null || python3 -m pip install -r requirements.txt

# own data dir on the server
python3 -m howl --data-dir /var/lib/howlcoin init
```

### 3. Open firewall on the VPS

```bash
ufw allow 22/tcp
ufw allow 42069/tcp
ufw --force enable
```

### 4. Run as a service (stays up after reboot)

```bash
cat >/etc/systemd/system/howlcoin.service <<'EOF'
[Unit]
Description=Howlcoin P2P seed
After=network.target

[Service]
Type=simple
WorkingDirectory=/root/howlcoin
ExecStart=/usr/bin/python3 -m howl --data-dir /var/lib/howlcoin node --host 0.0.0.0 --port 42069 --rpc-host 127.0.0.1 --rpc-port 42070
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now howlcoin
systemctl status howlcoin --no-pager
```

### 5. Point miners + GitHub docs

```bash
python3 -m howl node --connect YOUR_VPS_IP:42069
```

Update `SEEDS.md` in the repo:

```markdown
| YOUR_VPS_IP | 42069 | Primary public seed |
```

Then:

```bash
cd ~/Desktop/howlcoin
# edit SEEDS.md
git add SEEDS.md README.md
git commit -m "Add public seed node"
git push
```

### 6. Optional: sync your home chain to the VPS

If the VPS started at genesis and your Mac already has height 7+, either:

- **Mine only on the VPS** going forward (simplest public network), or  
- On the VPS, connect **to your Mac** once (if Mac is reachable) so it pulls blocks, or  
- Copy `chain.json` carefully (advanced; both must share same genesis — they do by design).

Cleanest public launch: treat the **VPS as canonical seed**, everyone `--connect` there, including you at home.

---

## Security notes

- Do **not** expose dashboard `42070` to the internet unless you add auth (default binds localhost — good).
- Seed box can use a **burner wallet**; keep big balances offline.
- Never commit `wallet.json` or mnemonics.

---

## Quick “is it public?” checklist

- [ ] Process listening on `0.0.0.0:42069`
- [ ] Port open on firewall / router / cloud security group
- [ ] Friend or phone-on-LTE can connect with `--connect IP:42069`
- [ ] `SEEDS.md` + README show that IP
- [ ] Seed survives reboot (systemd on VPS, or Energy settings on Mac)
