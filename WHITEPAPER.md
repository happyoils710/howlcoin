# Howlcoin (HOWL) White Paper

**Version 1.0** · August 2026  
**Ticker:** HOWL  
**Website / explorer:** [https://howlscan.org](https://howlscan.org)  
**Source:** [https://github.com/happyoils710/howlcoin](https://github.com/happyoils710/howlcoin)  
**Public seed:** `147.182.223.204:42069`

> *The moon heard a howl and howled back. Scrypt free. Much chain. Very wow.*

---

## Abstract

Howlcoin is an open-source, **Scrypt proof-of-work** cryptocurrency designed for **CPU-friendly mining**, transparent rules, and community experimentation. It is a standalone ledger (not a token on another smart-contract chain). Users run nodes, mine blocks, send HOWL between addresses, and inspect the chain on **Howlscan**.

Howlcoin prioritizes **clarity and accessibility** over industrial hashrate competition. Early block rewards are intentionally large so a small network of ordinary computers can bootstrap distribution. Emission then steps down by height, with a permanent **tail reward** so mining never fully ends.

**This software is experimental and community-oriented. It is not financial advice. There is no promise of price, exchange listing, or profit.**

---

## 1. Motivation

Many modern “coins” are database entries on someone else’s chain. Howlcoin instead offers:

1. **A real PoW chain** — blocks are earned by Scrypt work, not by mint buttons on a contract.  
2. **Runnable software** — one repository, Python implementation, simple CLI.  
3. **Public infrastructure** — a seed node and a public explorer (Howlscan).  
4. **Fair launch posture** — genesis creates **no spendable premine**; coins enter circulation via mining.

Howlcoin is built for people who want to **see** a chain: mine a block, watch height move, send to a friend, look it up on the explorer.

---

## 2. Design goals

| Goal | Approach |
|------|----------|
| Easy to mine at launch | Light Scrypt, modest initial difficulty |
| Predictable rules | Fixed emission schedule by block height |
| Open participation | Public seed + open-source node software |
| Inspectability | Howlscan block explorer + HTTP API |
| Simple wallets | BIP39 12-word seeds or raw private keys |
| Honest scope | Documented limits; no fake “enterprise” claims |

---

## 3. Consensus

### 3.1 Proof of work

Howlcoin uses **Scrypt** with parameters:

| Parameter | Value |
|-----------|--------|
| **N** | 1024 |
| **r** | 1 |
| **p** | 1 |
| **Output** | 32 bytes |

Block headers are serialized and hashed with Scrypt. A block is valid when the digest meets the current **difficulty** rule (legacy: leading zero hex nibbles; from height 120: continuous smooth target — see §3.2).

### 3.2 Block time and difficulty

| Parameter | Value |
|-----------|--------|
| Target block time | **60 seconds** |
| Difficulty retarget | Every **20** blocks |
| Max adjust per window | **4×** up or down |
| Initial difficulty | **4** (legacy nibble era) |
| Smooth difficulty (v0.6) | From height **120** |
| Stall relief | If block gap ≥ **2 hours**, extra difficulty drop (up to 16×) |

**Legacy (height &lt; 120):** difficulty is an integer count of leading zero **hex nibbles** in the Scrypt hash.

**Smooth (height ≥ 120):** header field stores **milli-nibble** work (`round(d × 1000)`). Proof-of-work uses a continuous target `hash < 2^(256 − 4d)`, so difficulty can move in small steps instead of 16× jumps. Stall relief is deterministic from block timestamps (not wall-clock at validation of ancient history beyond the block’s own timestamp).

Difficulty is adjusted so average block time tracks the target as hashrate changes.

### 3.3 Genesis

- **Height:** 0  
- **Spendable subsidy:** 0  
- **Inscription:** project launch message (see genesis hash on Howlscan)  
- All networks that share this genesis and software rules are the same *family*; only **synced peers** share one public tip.

### 3.4 Validation (high level)

A new block must:

- Link to the previous tip (`prev_hash`)  
- Carry correct height  
- Include a valid **coinbase** (mining reward)  
- Meet Scrypt difficulty  
- Include only valid signed transfers (if any) with correct balances and nonces  

Peers prefer a **longer valid chain** when syncing.

---

## 4. Transactions and accounts

Howlcoin uses a simple **account model** (address balances), not a full UTXO set.

### 4.1 Addresses

- Base58Check-style addresses with Howlcoin version byte  
- Typical form starts with **`H`**  
- Backed by **secp256k1** ECDSA keys  

### 4.2 Transfers

A transfer includes:

- `from`, `to`, `amount`, optional `fee`  
- `nonce` (replay protection per sender)  
- `public_key`, `signature`  
- optional `memo`  

Transfers enter a **mempool**, then are confirmed when included in a mined block.

### 4.3 Coinbase (mining reward)

Each block’s first transaction is a **coinbase**:

- **No sender** — new HOWL is created  
- **To** the miner’s address  
- Amount set by the emission schedule  

This is a **mining reward**, not a payment from another wallet.

### 4.4 Fees

Transfers require a **minimum network fee of 1 HOWL** (default 1 HOWL). The fee is deducted from the sender and paid to the **miner** of the confirming block (added to the coinbase alongside the block subsidy). Fees help compensate nodes that secure the network.

---

## 5. Monetary policy

### 5.1 Units

| Unit | Value |
|------|--------|
| 1 HOWL | 100,000,000 howlies |
| Decimals | 8 |

### 5.2 Block subsidy schedule

| Height range | Reward (HOWL / block) |
|--------------|------------------------|
| 0 (genesis) | 0 |
| 1 – 999 | **500,000** |
| 1,000 – 9,999 | **250,000** |
| 10,000 – 49,999 | **100,000** |
| 50,000 – 199,999 | **50,000** |
| 200,000 – 499,999 | **10,000** |
| 500,000+ | **1,000** (tail) |

### 5.3 Soft supply narrative

A soft narrative cap of **100 billion HOWL** is documented for long-horizon storytelling. Because of **tail emission**, total supply grows indefinitely at a low rate after height 500,000. There is no hard “burn to zero issuance” in the current rules.

### 5.4 Distribution

- No genesis premine  
- Coins are distributed to whoever mines (and to recipients of transfers)  
- Early periods heavily favor active miners  

---

## 6. Networking

| Item | Value |
|------|--------|
| Default P2P port | **42069** |
| Protocol | JSON-line messages over TCP |
| Magic | `HOWL` |
| Public seed (main) | **147.182.223.204:42069** |

Nodes exchange hellos, inventory, blocks, and transactions. A node that is behind requests blocks and may **adopt a longer valid chain** sharing the same genesis.

---

## 7. Wallets

### 7.1 BIP39

New wallets generate a **12-word English mnemonic**, derived along:

```text
m/44'/42069'/0'/0/0
```

Users must store the mnemonic offline. Anyone with the words controls the funds.

### 7.2 Import

Raw **64-character hex** secp256k1 private keys can be imported. Imported keys do not invent a mnemonic.

### 7.3 CLI essentials

```bash
python3 -m howl init
python3 -m howl wallet
python3 -m howl mine
python3 -m howl send <ADDRESS> <AMOUNT>
python3 -m howl node --connect 147.182.223.204:42069
```

---

## 8. Howlscan (block explorer)

**https://howlscan.org** is the public, read-only explorer for the main ledger.

Capabilities include:

- Latest blocks and transactions  
- Block, transaction, and address pages  
- Richlist and mempool  
- Search by height, hash, txid, or address  
- “Run a node” instructions with copy-paste commands  

The explorer **does not** mint coins and **does not** accept private keys. Client-side browser tools cannot alter the real chain—only the viewer’s local display.

API examples:

- `GET /api/public/summary`  
- `GET /api/public/blocks`  
- `GET /api/public/tx/{txid}`  
- `GET /api/public/address/{addr}`  

---

## 9. Software architecture

| Component | Role |
|-----------|------|
| `howl` CLI | Wallet, mine, send, node, explorer |
| `blockchain.py` | Chain state, validation, mempool |
| `scrypt_pow.py` | Scrypt header mining |
| `network.py` | P2P sync and relay |
| `dashboard.py` | Local node web UI |
| `explorer.py` | Howlscan multi-page explorer |

Implementation language: **Python 3**, MIT licensed (see `LICENSE`).

---

## 10. Security considerations

1. **Protect mnemonics and private keys** — never paste them into websites or group chats.  
2. **Verify software** — prefer official GitHub source and known seed/explorer URLs.  
3. **Small network risk** — low hashrate chains can be reorganized by a motivated attacker with more power.  
4. **Experimental code** — expect bugs; pin versions; back up wallets.  
5. **No insurance** — lost keys mean lost funds.  

---

## 11. NFTs, multi-chain deposits, and oracle (v0.5)

### 11.1 NFTs on Howlcoin

Howlcoin supports simple **on-chain NFT records**:

| Type | Purpose |
|------|---------|
| `nft_mint` | Create NFT with `nft_id`, `name`, `uri` (fee ≥ 1 HOWL) |
| `nft_transfer` | Transfer ownership of an existing `nft_id` |

NFTs are not ERC-721 contracts; they are first-class ledger entries secured by Scrypt PoW.

### 11.2 Little multi-chain oracle

Transactions of type `oracle` publish a key/value observation (`oracle_key`, `oracle_value`, `source_chain`) onto Howlcoin. The latest value per key is retained. This is a **lightweight notice board**, not a trust-minimized bridge: reporters are not slashed, and clients should treat data as advisory.

Example keys: `eth.block_height`, `sol.slot`, `xtz.level`, `howl.height`.

### 11.3 External chain deposits (wallet)

The public wallet derives deposit addresses from the same BIP39 seed for **Solana**, **Tezos (tz1)**, and **Ethereum** (plus ERC-20 / SPL tokens). Those assets remain on their native networks unless a separate swap venue is used.

## 12. Roadmap (non-binding)

Community-driven improvements may include:

- Stronger P2P (peer discovery, compact blocks)  
- Fee market paid to miners  
- Lightweight SPV or mobile clients  
- More explorer analytics  
- Hardening and formal test suites  
- Richer NFT metadata / marketplaces  
- Stronger oracle attestation (multi-reporter, challenges)  

Nothing on this list is a commitment or timeline.

---

## 13. Governance

Howlcoin is **open source**. Protocol changes happen by software upgrades that operators choose to run. There is no foundation treasury in genesis and no on-chain voting module in v1.

---

## 14. Disclaimer

Howlcoin is provided **as is**, without warranty of any kind. Participation may involve loss of funds, software failure, or chain splits. Authors and contributors are not responsible for damages. **Nothing in this paper is an offer of securities or investment advice.**

---

## 15. References (project resources)

| Resource | URL / value |
|----------|-------------|
| Explorer | https://howlscan.org |
| White paper (this document) | https://howlscan.org/whitepaper · `WHITEPAPER.md` in repo |
| Source code | https://github.com/happyoils710/howlcoin |
| Public seed | `147.182.223.204:42069` |
| License | MIT (`LICENSE`) |

---

## Appendix A — Quick start (join the public chain)

```bash
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install -r requirements.txt
python3 -m howl init
python3 -m howl node --connect 147.182.223.204:42069
```

Leave the node running to stay synced. Mine with `python3 -m howl mine` when ready.

---

## Appendix B — Genesis message

```
2026-08-01 Howlcoin: the moon heard a howl and howled back. Scrypt free. Much chain. Very wow.
```

---

*End of Howlcoin White Paper v1.0*  
*Scrypt till the moon howls.*
