# Howlcoin (HOWL)

> **📄 [White Paper](WHITEPAPER.md)** — full design, consensus, emission, wallets & Howlscan  
> Read online: **[howlscan.org/whitepaper](https://howlscan.org/whitepaper)**

```
        __    __
       /  \__/  \      _   _                 _           _
      |  ◕    ◕  |    | | | | _____      __ | | ___ ___ (_)_ __
       \   ▽    /     | |_| |/ _ \ \ /\ / / | |/ __/ _ \| | '_ \
        \______/      |  _  | (_) \ V  V /  | | (_| (_) | | | | |
       /|      |\     |_| |_|\___/ \_/\_/   |_|\___\___/|_|_| |_|
      (_|  ▬▬  |_)           Scrypt meme coin · ticker HOWL  ·  awoo
```

**Scrypt proof-of-work meme coin** — same *algorithm family* as early Dogecoin, built to actually run and mine on your machine.

> The moon heard a howl and howled back.

| | |
|---|---|
| **Name** | Howlcoin |
| **Ticker** | `HOWL` |
| **Algo** | Scrypt (`N=1024, r=1, p=1`) — Doge/LTC-style light Scrypt |
| **Block time** | ~60 seconds (target) |
| **Decimals** | 8 (1 HOWL = 100,000,000 howlies) |
| **Launch** | Fair-ish: genesis has **0** premine; you mine block 1+ |
| **Coinbase (early)** | **500,000 HOWL** / block for the first 1,000 blocks |
| **White paper** | [WHITEPAPER.md](WHITEPAPER.md) · [howlscan.org/whitepaper](https://howlscan.org/whitepaper) |
| **Explorer** | [https://howlscan.org](https://howlscan.org) |
| **Wallet app** | [https://howlscan.org/app](https://howlscan.org/app) |
| **Native browser** | Capacitor shell — [docs/NATIVE_BROWSER.md](docs/NATIVE_BROWSER.md) |
| **Howl Swap** | SOL/USDC → HOWL bridge — [docs/HOWL_SWAP.md](docs/HOWL_SWAP.md) |
| **Public seed** | `147.182.223.204:42069` |

This is a **real local Scrypt chain** you can mine, send, and explore — not an ERC-20 wrapper and not a Dogecoin mainnet fork binary. Think *early-Doge energy*: silly brand, serious-enough PoW, CPU-mineable while the network is young.

---

## Public mining (share with anyone)

Two pieces make Howlcoin “public”:

1. **This software** — open source (GitHub), so people can install the miner/node  
2. **A seed node you run** — online 24/7 with port **42069** open, so everyone syncs the **same** chain  

Without a public seed, each person only mines a private local chain.

### For miners (anyone on the internet)

```bash
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install --user -r requirements.txt
python3 -m howl init
python3 -m howl node --connect 147.182.223.204:42069
# dashboard: http://127.0.0.1:42070/  → click Mine
```

See [SEEDS.md](SEEDS.md) for seed details. Primary seed is a **DigitalOcean VPS** (always on).

### For you (host / manage the public seed)

```bash
# SSH to the droplet
ssh -i ~/.ssh/id_ed25519_github root@147.182.223.204
systemctl status howlcoin
journalctl -u howlcoin -f
```

Reinstall / update: [docs/VPS_SEED.md](docs/VPS_SEED.md) · `scripts/vps-install.sh`

---

## Native Howl Browser (optional)

Search/Discover can open **real websites** (no iframe blocks) inside a Capacitor app shell (Chromium on Android, WebKit on iOS):

```bash
export PATH="/usr/local/opt/node@20/bin:$PATH"   # Node 18+ required
cd native
npm install
npm run cap:sync
npm run cap:android   # or cap:ios
```

Details: **[docs/NATIVE_BROWSER.md](docs/NATIVE_BROWSER.md)**.

---

## Quick start (local)

```bash
cd howlcoin   # or ~/Desktop/howlcoin

# deps (once)
python3 -m pip install --user -r requirements.txt

# birth the chain + wallet
python3 -m howl init

# full node: P2P + wallet (recommended)
python3 -m howl node --connect 147.182.223.204:42069
# portal:  http://127.0.0.1:42070/
# wallet:  http://127.0.0.1:42070/app  → Add to Home Screen, then Create wallet
```

### Wallet (public chain — any device)

**Open:** **[https://howlscan.org/app](https://howlscan.org/app)** · guide: [howlscan.org/wallet](https://howlscan.org/wallet)

- Non-custodial: keys stay in your browser (PIN-encrypted)  
- **Syncs** balance & history from the live public ledger  
- **Send / receive** HOWL from any phone or computer  
- Restore with your 12-word phrase on a new device  
- Network fee (min **1 HOWL**) paid to miners  

Optional: **Add to Home Screen** for an app-like icon.

### Local node dashboard

While a node is running: `http://127.0.0.1:42070/` (portal) and `/app` (local tools / mining).

### Solo CLI mining

```bash
python3 -m howl mine
python3 -m howl mine -n 5
python3 -m howl wallet
python3 -m howl status
python3 -m howl send <ADDRESS> 1000          # default fee 1 HOWL
python3 -m howl send <ADDRESS> 1000 --fee 5  # higher fee to miners
```

Wallet + chain live in `~/.howlcoin/` by default. Override with `--data-dir`.

---

## Multi-node (friends mine the same chain)

**You (host):**

```bash
python3 -m howl node --host 0.0.0.0 --port 42069 --rpc-port 42070
# share your LAN IP, e.g. 192.168.1.42:42069
```

**Friend:**

```bash
cd ~/Desktop/howlcoin   # same software
python3 -m howl init --data-dir ~/.howlcoin-friend
python3 -m howl node \
  --data-dir ~/.howlcoin-friend \
  --port 42071 \
  --rpc-port 42072 \
  --connect 192.168.1.42:42069
# dashboard: http://127.0.0.1:42072/
```

They sync your blocks (same genesis), then both can mine; new blocks relay over P2P.  
You can also paste `host:port` into the dashboard **P2P peers** box.

> Same software + same genesis message/params = same network. Don’t wipe `chain.json` mid-flight unless everyone resets.

---

## Web dashboard

`howl node` serves a dark “moon howl” UI with:

- height / difficulty / supply
- wallet address + balance
- one-click Scrypt mining
- send HOWL
- peer connect + peer table
- richlist + event log
- logo at `assets/howlcoin-logo-meme-pup-coin.jpg`

Ports default: **P2P `42069`**, **dashboard `42070`**.

---

## Why “like Doge when it first came out”?

| Early Dogecoin | Howlcoin |
|----------------|----------|
| Scrypt PoW | Scrypt PoW (same N/r/p class) |
| ~1 minute blocks | 60s target |
| Fun meme branding | Wolf/howl meme, not a clone of the shiba IP |
| Easy for regular PCs *at launch* | Difficulty starts low so your iMac can mine |
| Community coin energy | No founders’ premine in genesis |

Mainnet DOGE today is ASIC territory. **HOWL is a fresh chain** — your CPU is the network (until friends join and difficulty climbs).

---

## Emission (whole HOWL per block)

| Height | Reward |
|--------|--------|
| 1 – 999 | 500,000 |
| 1,000 – 9,999 | 250,000 |
| 10,000 – 49,999 | 100,000 |
| 50,000 – 199,999 | 50,000 |
| 200,000 – 499,999 | 10,000 |
| 500,000+ | 1,000 (tail emission) |

Difficulty retargets every **20** blocks toward the 60s target.

---

## CLI map

| Command | What it does |
|---------|----------------|
| `init` | Genesis + **BIP39** wallet (prints 12-word phrase) |
| `init --force` | New mnemonic wallet (backs up old `wallet.json`) |
| `mnemonic` | Show recovery phrase (or legacy private key) |
| `restore word1 … word12` | Restore wallet from BIP39 words |
| `wallet` | Address + balance (`--show-keys`, `--show-mnemonic`) |
| **`node`** | **P2P + web dashboard (main entry)** |
| `dashboard` | Web UI only (no P2P) |
| `mine [-n N]` | Scrypt-mine N blocks (solo CLI) |
| `send <to> <amount>` | Queue a tx (confirm with `mine`) |
| `balance [addr]` | Balance lookup |
| `status` | Chain summary |
| `peers` | Saved peer list |
| `bench` | Local Scrypt H/s |
| `richlist` | Top holders |
| `export` | Tip JSON dump |

---

## BIP39 recovery

New wallets use a **12-word BIP39 mnemonic** derived at path:

`m/44'/42069'/0'/0/0`

```bash
python3 -m howl mnemonic                          # show phrase
python3 -m howl wallet --show-mnemonic
python3 -m howl restore word1 word2 ... word12    # restore
python3 -m howl init --force                      # brand-new phrase (backs up old)
```

**Legacy wallets** (created before v0.3) only have a hex private key — a mnemonic **cannot** be reverse-engineered from that key. Keep the hex key, or move funds to a new mnemonic wallet after `init --force`.

## Security / reality check

- **Educational + meme-grade** chain (account model, JSON storage).
- Not audited. Not listed. Not financial advice.
- **Back up** the 12 words **and/or** `~/.howlcoin/wallet.json` — lose them, lose the coins.
- Fees are optional and currently burned (kept simple).

---

## Project layout

```
howlcoin/
  howl/
    config.py        # chain params, subsidy, Scrypt settings
    scrypt_pow.py    # Scrypt header hash + miner
    crypto.py        # ECDSA keys + HOWL addresses
    blockchain.py    # chain, mempool, validation
    network.py       # P2P sync + block/tx relay
    dashboard.py     # web UI + JSON API
    wallet.py        # local wallet
    cli.py           # CLI
  assets/
    howlcoin-logo-meme-pup-coin.jpg
    brand.md
  bin/howl
  requirements.txt
  README.md
```

---

## Brand

- **Name:** Howlcoin  
- **Ticker:** HOWL  
- **Vibe:** midnight wolf, moon, green terminal phosphors  
- **Catchphrase:** *Scrypt till the moon howls.*

Much chain. Very scrypt. Awoo.
