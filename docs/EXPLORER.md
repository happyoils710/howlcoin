# Howlcoin block explorer (Howlscan)

Read-only explorer for the **public** Howlcoin ledger.

| Network | Default data dir |
|---------|------------------|
| **Public** | `~/.howlcoin` or `/var/lib/howlcoin` on the seed VPS |

## Live site

**https://howlscan.org/**

## Run locally

```bash
cd ~/Desktop/howlcoin
python3 -m howl explorer
```

Open: **http://127.0.0.1:42080/**

```bash
python3 -m howl explorer \
  --public-data ~/.howlcoin \
  --host 127.0.0.1 \
  --port 42080
```

## Features

- Latest blocks and transactions  
- Block / tx / address detail  
- Richlist and mempool  
- Search height, hash, txid, or address  
- **Run a node** page with copy-paste sync commands  
- Auto-refresh  

## VPS (howlscan.org)

See [HOWLSCAN.md](HOWLSCAN.md).

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/networks` | List networks |
| `GET /api/public/summary` | Chain summary |
| `GET /api/public/blocks?limit=20` | Recent blocks |
| `GET /api/public/txs?limit=20` | Recent transactions |
| `GET /api/public/block/{height\|hash}` | Block detail |
| `GET /api/public/tx/{txid}` | Transaction |
| `GET /api/public/address/{addr}` | Address + history |
| `GET /api/public/richlist` | Top balances |
| `GET /api/public/mempool` | Pending txs |
