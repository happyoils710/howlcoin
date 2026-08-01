# Public seed nodes

Anyone on the internet can join the Howlcoin network with:

```bash
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install --user -r requirements.txt
python3 -m howl init
python3 -m howl node --connect bore.pub:12057
```

Dashboard: http://127.0.0.1:42070/ → **Mine**

| Host | Port | Notes |
|------|------|--------|
| **`bore.pub`** | **`12057`** | Public TCP tunnel → primary seed (Scrypt HOWL) |

## Operators (this Mac)

Seed + public tunnel must stay running:

```bash
# terminal 1 — node
cd ~/Desktop/howlcoin
python3 -m howl node --host 0.0.0.0 --port 42069 --rpc-host 127.0.0.1 --rpc-port 42070

# terminal 2 — free public TCP tunnel (port may change if restarted)
bore local 42069 --to bore.pub
```

Or: `./scripts/start-public-seed.sh`

If the bore port changes, update this file and push to GitHub.

## LAN only (same Wi‑Fi)

```bash
python3 -m howl node --connect 192.168.1.96:42069
```
