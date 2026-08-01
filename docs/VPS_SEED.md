# Howlcoin VPS seed (Option B) — anyone on the internet

A small Linux VPS gives you a **stable public IP** on port **42069**.  
Better than home + bore tunnels (no Mac sleep, no changing ports).

## 1. Create a VPS (5 minutes)

Pick any provider:

| Provider | Link |
|----------|------|
| DigitalOcean | https://cloud.digitalocean.com/droplets/new |
| Vultr | https://my.vultr.com/ |
| Hetzner | https://console.hetzner.cloud/ |
| Linode/Akamai | https://cloud.linode.com/ |

Settings:

- **Image:** Ubuntu 24.04 LTS  
- **Size:** 1 vCPU / 1 GB RAM (~$4–6/mo)  
- **Region:** closest to you / your miners  
- **Auth:** add your SSH public key (recommended) or root password  
- Create droplet → copy the **IPv4 address**

### Your SSH public key (from this Mac)

If the provider asks for a public key, use the contents of:

```bash
cat ~/.ssh/id_ed25519_github.pub
```

(Or generate a dedicated key: `ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519_howl`)

---

## 2. Install Howlcoin seed (one command)

From **your Mac**:

```bash
# replace VPS_IP
ssh root@VPS_IP 'bash -s' < ~/Desktop/howlcoin/scripts/vps-install.sh
```

Or SSH in manually:

```bash
ssh root@VPS_IP
curl -fsSL https://raw.githubusercontent.com/happyoils710/howlcoin/main/scripts/vps-install.sh | bash
```

That will:

- clone https://github.com/happyoils710/howlcoin  
- install Python deps  
- open firewall **TCP 42069** (+ SSH)  
- start **systemd** service `howlcoin` (survives reboot)

---

## 3. Tell the world

Miners:

```bash
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install --user -r requirements.txt
python3 -m howl init
python3 -m howl node --connect VPS_IP:42069
```

Update repo seeds (on your Mac):

```bash
# edit SEEDS.md — put VPS_IP:42069 first
cd ~/Desktop/howlcoin
git add SEEDS.md README.md
git commit -m "Point public seed at VPS"
git push
```

---

## 4. Useful VPS commands

```bash
ssh root@VPS_IP
systemctl status howlcoin
journalctl -u howlcoin -f
# restart
systemctl restart howlcoin
# height / tip via local API (on VPS only)
curl -s http://127.0.0.1:42070/api/status | python3 -m json.tool
```

---

## 5. Chain tip / your home blocks

The VPS starts from the **same genesis** as GitHub Howlcoin.

- If the VPS is **new**, height starts at 0 (or 0 after init).  
- Your Mac may already be at height 7+.  
- **Simplest public network:** treat the **VPS as the main seed**; you and everyone mine by connecting to it:

```bash
# on your Mac — join the VPS network (use a separate data dir so you don't fight local chain)
python3 -m howl --data-dir ~/.howlcoin-mainnet node --connect VPS_IP:42069 --port 42071 --rpc-port 42072
```

To **push** your existing Mac chain onto the VPS (optional, advanced): stop `howlcoin` on VPS, copy `chain.json` only if you know what you're doing, or connect VPS briefly as a peer of a reachable full node.

---

## 6. Security

- Dashboard is bound to **127.0.0.1** (not public) — good.  
- Don’t store large balances on the VPS wallet.  
- Keep SSH key auth; disable password login when comfortable.  
- `ufw` allows only 22 + 42069.

---

## Checklist

- [ ] VPS created, have root SSH  
- [ ] `vps-install.sh` finished without errors  
- [ ] `systemctl status howlcoin` shows active  
- [ ] From home: `python3 -m howl node --connect VPS_IP:42069` works  
- [ ] `SEEDS.md` + README updated + `git push`  
- [ ] Share GitHub + connect line on socials  
