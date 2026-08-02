# WalletConnect (external dApps)

Howlcoin Wallet can pair with **external dApps** (Uniswap, etc. opened in Safari/Chrome) via **WalletConnect v2**, and can announce an **EIP-1193 / EIP-6963** provider inside the app.

## What users do

1. Unlock **https://howlscan.org/app**
2. Open **More → WalletConnect**
3. On the dApp: choose **WalletConnect** → copy the `wc:…` link (or scan if mobile deep-links)
4. Paste into Howl and tap **Connect**
5. **Approve** the connection / signature / transaction prompts

Deep links also work:

```text
https://howlscan.org/app#wc=wc:…
https://howlscan.org/app?uri=wc:…
```

## Server setup (required for pairing)

WalletConnect needs a free **Reown Cloud** project id (public client id):

1. Create a project: [https://cloud.reown.com](https://cloud.reown.com)
2. On the Howlscan VPS / explorer service:

```bash
# /etc/systemd/system/howlcoin-explorer.service  (Environment=)
Environment=HOWL_WC_PROJECT_ID=YOUR_PROJECT_ID_HERE
```

```bash
systemctl daemon-reload
systemctl restart howlcoin-explorer
```

3. Check:

```bash
curl -sS https://howlscan.org/api/public/walletconnect
# { "enabled": true, "projectId": "…", … }
```

Without `HOWL_WC_PROJECT_ID`, the UI still loads but pairing stays disabled and shows setup instructions.

## Chains advertised

| CAIP-2 | Network |
|--------|---------|
| eip155:1 | Ethereum |
| eip155:10 | Optimism |
| eip155:8453 | Base |
| eip155:56 | BNB Chain |
| eip155:43114 | Avalanche |

Signing uses the same BIP39-derived **ETH** key as the in-app wallet.

## In-page provider

**More → WalletConnect → Enable in-page provider** sets `window.ethereum` / EIP-6963 **Howlcoin** for dApps that load inside this app. External browsers still need WalletConnect.

## Files

| Path | Role |
|------|------|
| `assets/wallet-connect.mjs` | WC Web3Wallet + EIP-1193 + EIP-712 |
| `assets/public-wallet.html` | UI + deep links |
| `GET /api/public/walletconnect` | projectId config |

## Listing in WalletConnect Explorer

After the project works in production, submit the wallet to Reown’s explorer (metadata: name Howlcoin, url howlscan.org, icon from `/assets/howlcoin-logo-meme-pup-coin.jpg`) so dApps can show “Howlcoin” in the wallet list.
