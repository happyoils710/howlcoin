# Wrapped HOWL (wHOWL) — Solana SPL

Native Howl L1 coin can be **wrapped** to an SPL token on Solana for DEXs (Jupiter, Raydium, etc.).

## Model

Semi-custodial (same trust model as Howl Swap Phase A):

| Direction | User does | Relayer does |
|-----------|-----------|--------------|
| **Wrap** | Sends native HOWL to deposit address | Mints **wHOWL** SPL to user ATA |
| **Unwrap** | Sends wHOWL SPL to treasury | Sends native HOWL on L1 |

Ratio: **1 HOWL ≈ 1 wHOWL** (minus fee bps, default 0.5%).

## Create the mint (VPS)

Treasury keypair must hold a little SOL for rent (~0.05 SOL):

```bash
# Fund Solana treasury first if needed
# Address: cat /var/lib/howlcoin/bridge-sol-treasury.address

bash /opt/howlcoin/scripts/create-howl-spl-mint.sh
```

This:

1. Installs Solana CLI if missing  
2. Creates SPL mint (8 decimals, authority = bridge Solana treasury)  
3. Writes `/var/lib/howlcoin/howl-spl-mint.meta.json`  
4. Appends `HOWL_SPL_MINT=…` and wrap env to `bridge.env`  
5. Restarts explorer  

Verify:

```bash
curl -sS https://howlscan.org/api/public/wrap | python3 -m json.tool
curl -sS https://howlscan.org/api/public/token-info | python3 -m json.tool
# contracts[] should include SPL wHOWL
```

## Relayer

```bash
cp /opt/howlcoin/deploy/howl-wrap-relayer.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now howl-wrap-relayer
journalctl -u howl-wrap-relayer -f
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/public/wrap` | Config + mint |
| GET | `/api/public/wrap/quote?amount=10&direction=wrap` | Quote |
| POST | `/api/public/wrap/order` | Create order |
| POST | `/api/public/wrap/order/<id>/tx` | Attach deposit tx |
| GET | `/api/public/wrap/order/<id>` | Status |

## Wallet

**Swap → Wrap HOWL ↔ wHOWL (SPL)** (or open `/app/` → Swap → Wrap).

## Env

```bash
HOWL_SPL_MINT=<mint>
HOWL_WRAP_ENABLED=1
HOWL_WRAP_HOWL_DEPOSIT=<H… wrap deposit / hot>
HOWL_WRAP_SOL_TREASURY=<Sol treasury>
HOWL_WRAP_FEE_BPS=50
HOWL_BRIDGE_HOT_WALLET=/var/lib/howlcoin/bridge-hot-wallet.json
```

## Notes

- HOWL remains a **native Scrypt L1** coin. wHOWL is an optional wrapped representation.  
- Mint authority is the Solana treasury key — keep that key offline-backed and secure.  
- Token metadata (name/symbol on Solscan) can be added later with Metaplex/metaboss.  


## Solscan name / symbol

The mint alone shows as generic "Token" until Metaplex metadata exists:

```bash
# after assets/whowl-token.json is live on howlscan.org
bash /opt/howlcoin/scripts/create-whowl-metadata.sh
```

## Recover a bare HOWL deposit (no order)

If someone sent HOWL to the deposit address without opening a wrap order:

```bash
# list stranded deposits
cd /opt/howlcoin
set -a; source /var/lib/howlcoin/bridge.env; set +a
/opt/howlcoin-venv/bin/python3 scripts/howl-wrap-relayer.py --list-orphans

# mint wHOWL to their Solana wallet (takes 0.5% fee from amount)
/opt/howlcoin-venv/bin/python3 scripts/howl-wrap-relayer.py \
  --fulfill-txid <HOWL_TXID> \
  --sol <USER_SOLANA_ADDRESS>
```

## Better ways to offer wrapped HOWL (product options)

Your current model is **semi-custodial mint/burn** (lock HOWL L1 → mint wHOWL SPL). That works for bootstrap. Alternatives ranked for a small pack:

| Approach | Trust | Effort | Best for |
|----------|-------|--------|----------|
| **A. Keep mint/burn + clear UX** (current, improved) | Trust mint authority | Low | Bootstrap / today |
| **B. Liquidity pool on Solana** (Raydium/Orca HOWL-index or wHOWL/SOL) | DEX custody of LP, not your mint for swaps | Med | Price discovery + SOL↔wHOWL without your relayer |
| **C. Portal / Wormhole-style message bridge** | Multisig guardians | High | “Real” cross-chain later |
| **D. Canonical bridge contract on Solana** (lock SOL → program CPI mint) | Program + authority | Med–High | On-chain rules, still need L1 proof |
| **E. Fiat/CEX listing of wHOWL** | Exchange | Ops | Liquidity, not tech |

**Recommendation for Howl now**

1. **Keep A for HOWL L1 ↔ wHOWL** (wrap/unwrap) — users already understand “lock / mint”.
2. **Use A for SOL → wHOWL** as a **treasury mint desk** (what we shipped) while volume is low.
3. **When wHOWL supply + demand grow:** seed a **Raydium wHOWL/SOL (or wHOWL/USDC) pool** so most users swap on Jupiter **without** opening a Howl order. Your mint remains for wrap/unwrap and emergency inventory.
4. **Do not** put HOWL native L1 on “every EVM chain” as a fake ERC-20 without a real bridge — that fragments trust. Prefer **one Solana wHOWL** + clear docs; later one EVM wHOWL only if you run a second audited bridge.

**Logos / recognition:** list wHOWL on Solana token lists (Jupiter strict list eventually), Metaplex metadata (done), and show the Howl logo in-wallet for HOWL + wHOWL on every screen.

