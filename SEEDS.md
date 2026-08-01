# Public seed nodes

Anyone on the internet can join Howlcoin:

```bash
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install --user -r requirements.txt
python3 -m howl init
python3 -m howl node --connect 147.182.223.204:42069
```

Dashboard: http://127.0.0.1:42070/ → **Mine**

| Host | Port | Notes |
|------|------|--------|
| **`147.182.223.204`** | **`42069`** | DigitalOcean VPS seed (primary, 24/7) |

## Operators

```bash
ssh -i ~/.ssh/id_ed25519_github root@147.182.223.204
systemctl status howlcoin
journalctl -u howlcoin -f
```

Service: `howlcoin` (systemd, auto-start on reboot)  
Data: `/var/lib/howlcoin`  
Code: `/opt/howlcoin`
