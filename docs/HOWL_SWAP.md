# Howl Swap (Phase A) — SOL/USDC → native HOWL

Semi-custodial bridge for the public wallet.

1. User opens an **order** (Howl address + amount).
2. User sends **SOL** or **USDC** on Solana to the treasury deposit address.
3. **Relayer** detects the deposit and sends **native HOWL** from a hot wallet.

This is **not** trustless. Operators hold deposit funds and the HOWL inventory.

## Go live (bootstrap — recommended)

On the VPS as **root** (after code is at `/opt/howlcoin`):

```bash
# Default rates: 100k HOWL/SOL, 10 HOWL/USDC (~$0.10/HOWL)
bash /opt/howlcoin/scripts/install-howl-bridge.sh

# Launch index 1 HOWL ≈ $1:
bash /opt/howlcoin/scripts/install-howl-bridge.sh --howl-per-usdc 1

# Use an existing Solana treasury (no new keygen):
bash /opt/howlcoin/scripts/install-howl-bridge.sh --sol-treasury <base58>

# Only create wallets + bridge.env (no systemd start):
bash /opt/howlcoin/scripts/install-howl-bridge.sh --bootstrap-only
```

What bootstrap does:

1. Creates **HOWL hot wallet** → `/var/lib/howlcoin/bridge-hot-wallet.json`
2. Creates **Solana treasury keypair** → `/var/lib/howlcoin/bridge-sol-treasury.json` (or uses `--sol-treasury`)
3. Writes **`/var/lib/howlcoin/bridge.env`** (rates, secrets, paths)
4. Installs **`howl-bridge-relayer.service`** + explorer `EnvironmentFile` drop-in
5. Restarts explorer + starts relayer

Then **fund the HOWL hot address** printed by the script (inventory). Users send SOL/USDC to the Solana treasury address.

```bash
# Status
systemctl status howl-bridge-relayer
journalctl -u howl-bridge-relayer -n 50 --no-pager
curl -sS https://howlscan.org/api/public/bridge | python3 -m json.tool
cat /var/lib/howlcoin/bridge-bootstrap.json
```

Disable:

```bash
bash /opt/howlcoin/scripts/install-howl-bridge.sh --disable
```

## Server env (manual)

If you prefer hand-written env instead of bootstrap:

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


## SOL / USDC → wHOWL (SPL)

Same deposit flow as Phase A, but set `payout: "whowl"` on the order (wallet: **You receive → wHOWL**).

- User sends SOL/USDC to the Solana treasury.
- Relayer **mints wHOWL** to the user’s Solana address (`sol_from`) instead of sending L1 HOWL.
- Requires `HOWL_SPL_MINT` and mint authority keypair (bridge Solana treasury).
- Rate is the same HOWL-per-unit quote (1 wHOWL ≈ 1 HOWL index unit after fee).

API:

```json
POST /api/public/bridge/order
{
  "howl_address": "H…",
  "asset": "sol",
  "amount": 0.1,
  "sol_from": "Fg…",
  "payout": "whowl"
}
```
