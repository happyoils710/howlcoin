# Howl Pack Wallet (full)

Primary wallet at **https://howlscan.org/app/**

## Architecture

- **Pack shell** (React): onboarding, unlock, Pack theme bottom nav (Home · Play · Charts · Discover · More)
- **Classic engine** (`public-wallet.html`): all product features — HOWL L1, SOL, EVM/Base, City, Play, NFTs, contracts, oracle, Howl Swap bridge, WalletConnect, browser, charts
- **Shared vault**: `howl_public_wallet_v2` (same PIN unlocks both)
- Classic loads inside Pack via `/classic?embed=1` (chrome hidden)

## URLs

| Path | Role |
|------|------|
| `/app/` | Primary Pack UI (merged) |
| `/classic` | Standalone classic (full chrome) |
| `/pack/` | Redirects → `/app/` |

## Build

```bash
cd apps/howl-pack-wallet   # or ~/src/howl-pack-wallet
npm install && npm run build
cp -R dist/* ../../assets/pack-wallet/
```

Restart explorer after deploy.
