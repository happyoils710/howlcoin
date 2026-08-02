# Howl Swap (Phase A) — SOL/USDC → native HOWL

Semi-custodial bridge for the public wallet.

1. User opens an **order** (Howl address + amount).
2. User sends **SOL** or **USDC** on Solana to the treasury deposit address.
3. **Relayer** detects the deposit and sends **native HOWL** from a hot wallet.

This is **not** trustless. Operators hold deposit funds and the HOWL inventory.

## Server env

```bash
export HOWL_BRIDGE_ENABLED=1
export HOWL_BRIDGE_SOL_TREASURY=<solana base58 address>
# optional separate USDC receiver (defaults to SOL treasury owner)
export HOWL_BRIDGE_USDC_TREASURY=
export HOWL_BRIDGE_HOWL_PER_SOL=100000    # HOWL per 1 SOL (before fee)
export HOWL_BRIDGE_HOWL_PER_USDC=10
export HOWL_BRIDGE_FEE_BPS=100            # 1%
export HOWL_BRIDGE_MIN_SOL=0.01
export HOWL_BRIDGE_MAX_SOL=10
export HOWL_BRIDGE_DATA=/var/lib/howlcoin
export HOWL_PUBLIC_DATA=/var/lib/howlcoin
export HOWL_BRIDGE_ADMIN_SECRET=<random>
export HOWL_BRIDGE_HOT_WALLET=/var/lib/howlcoin/bridge-hot-wallet.json
export HOWL_NODE_RPC=http://127.0.0.1:42070
export SOLANA_RPC=https://api.mainnet-beta.solana.com
```

Restart explorer after setting env:

```bash
systemctl edit howlcoin-explorer   # add Environment= lines
systemctl restart howlcoin-explorer
```

## API

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/api/public/bridge` | Config, rates, deposit addresses |
| GET | `/api/public/bridge/quote?asset=sol&amount=0.1` | Quote |
| POST | `/api/public/bridge/order` | Create order JSON body |
| GET | `/api/public/bridge/order/<id>` | Status |
| GET | `/api/public/bridge/orders?howl=H…` | User history |
| POST | `/api/public/bridge/order/<id>/tx` | Attach Solana tx sig |

Create order body:

```json
{
  "howl_address": "H…",
  "asset": "sol",
  "amount": 0.1,
  "sol_from": "optional-user-sol-address"
}
```

## Relayer

```bash
python3 scripts/howl-bridge-relayer.py --once --dry-run
python3 scripts/howl-bridge-relayer.py --interval 20
```

Hot wallet must hold enough HOWL + fees. After credit, a **miner** must confirm the HOWL tx (or run `howl mine` on the seed).

## Wallet UI

**Swap → Howl Swap** tab: quote → create order → send SOL to shown address → poll until completed.

## Security checklist

- Use a dedicated Solana treasury and HOWL hot wallet  
- Keep `HOWL_BRIDGE_ADMIN_SECRET` private  
- Cap `MAX_SOL` / inventory  
- Monitor relayer logs  
- Publish rate changes publicly  

## Later phases

- Wrapped HOWL (SPL) + Jupiter  
- Multi-sig treasury  
- Stronger proofs of reserves  
