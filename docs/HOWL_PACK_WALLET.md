# Howl Pack Wallet

Base-first non-custodial EVM wallet for Howlcoin (React + TypeScript + viem).

## URL

- Production: https://howlscan.org/app/
- Local: `cd apps/howl-pack-wallet && npm install && npm run dev` → http://localhost:5174/app/

## Source

- Preferred workspace if Desktop iCloud is flaky: `~/src/howl-pack-wallet`
- Monorepo: `apps/howl-pack-wallet`
- Built static assets: `assets/pack-wallet/` (served by explorer at `/app`)

## Build & ship

```bash
cd apps/howl-pack-wallet   # or ~/src/howl-pack-wallet
npm install
npm run build
# copy dist → assets/pack-wallet
rm -rf ../../assets/pack-wallet && mkdir -p ../../assets/pack-wallet
cp -R dist/* ../../assets/pack-wallet/
```

Restart explorer after deploy.

## Features

Home (assets + USD), Activity, Discover (curated Base dApps), Swap (0x) / Bridge (Jumper),
vault create/import, multi-account, networks (Base default), themes (Pack default).

Same BIP44 ETH path as classic `/app`: `m/44'/60'/0'/0/i`.

## Env (optional)

See `apps/howl-pack-wallet/.env.example`.
