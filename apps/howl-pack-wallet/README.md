# Howl Pack Wallet

Base-first non-custodial wallet for Howlcoin. Howl Pack visual language (mint + soft night) — not a Base/Coinbase clone.

## Stack

React · TypeScript · Vite · Tailwind · viem · Zustand · TanStack Query

## Dev

```bash
cd ~/src/howl-pack-wallet   # or apps/howl-pack-wallet in the monorepo
npm install
npm run dev                 # http://localhost:5174/pack/
```

## Build

```bash
npm run build               # dist/ with base /pack/
```

## Features

- Create / import BIP39 vault (AES-GCM encrypted on device)
- Same ETH path as classic wallet: `m/44'/60'/0'/0/i`
- Home assets + USD prices (DefiLlama)
- Send / receive (QR)
- Activity (local journal + optional Basescan key)
- Discover curated Base dApps
- Swap (0x on Base) + bridge entry (Jumper)
- Networks: Base, Base Sepolia, Ethereum, OP, Arbitrum
- Themes: Pack · Dark · Light · Neo · Bones

## Env

See `.env.example`. Optional: `VITE_BASESCAN_KEY`, `VITE_ZEROX_API_KEY`, `VITE_BASE_RPC`.

## Serve on Howlscan

Explorer should serve static `assets/pack-wallet` at `/pack` (SPA fallback to `index.html`).
