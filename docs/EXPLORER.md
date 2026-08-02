# Howlcoin block explorer (public + Telegram)

Read-only explorer for **both** chains:

| Tab | Default data dir | What it is |
|-----|------------------|------------|
| **Public** | `~/.howlcoin` or `/var/lib/howlcoin` on VPS | Seed / desktop public network |
| **Telegram** | `~/.howlcoin-telegram` | Chain used by @HowlMine_bot |

## Run on your Mac

```bash
cd ~/Desktop/howlcoin
python3 -m howl explorer
```

Open: **http://127.0.0.1:42080/**

Custom paths:

```bash
python3 -m howl explorer \
  --public-data ~/.howlcoin \
  --telegram-data ~/.howlcoin-telegram \
  --host 127.0.0.1 \
  --port 42080
```

## Features

- Switch **Public** / **Telegram** tabs  
- Recent blocks, height, difficulty, supply, mempool  
- Block detail (all txs)  
- Address balance + history  
- Txid lookup (confirmed + mempool)  
- Search box: height, hash, txid, or `H…` address  
- Auto-refresh every 15s  

## Run on VPS (public seed chain)

SSH to the droplet, then:

```bash
cd /opt/howlcoin && git pull
# public chain is usually:
python3 -m howl explorer \
  --public-data /var/lib/howlcoin \
  --telegram-data /var/lib/howlcoin-telegram \
  --host 127.0.0.1 \
  --port 42080
```

If the Telegram bot also runs on the VPS with `HOWL_DATA_DIR=/var/lib/howlcoin-telegram`, both tabs work there.

**Expose on the internet (optional):**

```bash
# bind all interfaces + open firewall
ufw allow 42080/tcp
python3 -m howl explorer --host 0.0.0.0 --port 42080 \
  --public-data /var/lib/howlcoin \
  --telegram-data /var/lib/howlcoin-telegram
```

Then: `http://147.182.223.204:42080/`  
(Read-only; still put it behind auth or Cloudflare if you care about abuse.)

## API (for bots / tools)

| Endpoint | Description |
|----------|-------------|
| `GET /api/networks` | List both chains |
| `GET /api/public/summary` | Public chain summary |
| `GET /api/telegram/summary` | Telegram chain summary |
| `GET /api/{net}/blocks?limit=20` | Recent blocks |
| `GET /api/{net}/block/{height\|hash}` | Block detail |
| `GET /api/{net}/tx/{txid}` | Transaction |
| `GET /api/{net}/address/{addr}` | Address + history |

`{net}` is `public` or `telegram`.

## Note

Public and Telegram are **separate ledgers** unless you intentionally use the same data dir.  
Balances on @HowlMine_bot do not appear on the public seed tab, and vice versa.
