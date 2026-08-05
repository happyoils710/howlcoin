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
