# Howl Browser (Capacitor native shell)

Open-source **Chromium / WebKit** shell around the Howlcoin wallet so Search and Discover open real websites (Custom Tabs / SFSafariViewController / in-app WebView) instead of blocked iframes.

## What you get

| Layer | Role |
|--------|------|
| **Wallet UI** | Same `https://howlscan.org/app` (non-custodial HOWL wallet) |
| **Howl Search** | Server multi-source search (unchanged) |
| **Native browser** | `@capacitor/browser` + Capgo InAppBrowser — full site load |
| **Howl Reader** | Fallback when running as a pure web PWA |

News sites that send `X-Frame-Options` load fine in the native overlay WebView because they are **top-level** documents there, not iframes.

## Requirements

- **Node.js 18+** (repo uses Node 20 via Homebrew `node@20` on this machine)
- For iOS: macOS + Xcode + CocoaPods
- For Android: Android Studio + SDK

```bash
# if needed
export PATH="/usr/local/opt/node@20/bin:$PATH"
node -v   # >= 18
```

## One-time setup

```bash
cd native
npm install
npm run sync:www          # build www/ shell + copy assets
npx cap add ios           # once
npx cap add android       # once
npm run cap:sync
```

### Live wallet (default)

`capacitor.config.json` points the app at:

```text
https://howlscan.org/app?native=1
```

Capacitor injects its bridge into that page. The wallet detects `native=1` / Capacitor and routes opens through:

1. **Capgo InAppBrowser** `openWebView` (in-app browser chrome)
2. else **`@capacitor/browser`** (Chrome Custom Tabs / Safari View Controller)

### Offline / local shell

Use the local config (bundled `public-wallet.html`):

```bash
cp capacitor.config.local.json capacitor.config.json
npm run cap:sync
```

Or set a custom live URL:

```bash
HOWL_APP_URL=https://howlscan.org/app npm run sync:www
```

## Run

```bash
# iOS Simulator / device
npm run cap:ios
# then press Run in Xcode

# Android
npm run cap:android
# then Run in Android Studio
```

## How the wallet uses it

In `assets/public-wallet.html`:

- Loads `/assets/howl-native-bridge.js`
- `isHowlNative()` → Capacitor or `?native=1`
- `openUrlInApp` / Discover cards call `howlNativeOpen(url)` first
- Web PWA still uses iframe + Howl Reader when native plugins are missing

## Project layout

```text
native/
  package.json
  capacitor.config.json       # production → live howlscan app
  capacitor.config.local.json # offline www shell
  src/howl-native-bridge.js
  scripts/sync-www.js
  www/                        # generated (gitignored content ok)
  ios/                        # after cap add ios
  android/                    # after cap add android
assets/howl-native-bridge.js  # served by howlscan explorer
```

## Notes

- This is **not** shipping Opera; it uses the same class of engine (Chromium on Android, WebKit on iOS) that Opera/Chrome/Safari use.
- Keep `allowNavigation` limited to Howlscan so random links do not navigate the wallet WebView away — external pages open in the **plugin** browser overlay.
- After changing the live wallet HTML, users only need an app restart (remote URL mode); no store rebuild for pure web fixes.
- Rebuild the native binary when you change Capacitor plugins or `capacitor.config.json`.

## Troubleshooting

| Issue | Fix |
|--------|-----|
| `node` too old | `export PATH="/usr/local/opt/node@20/bin:$PATH"` |
| Sites still blocked | Confirm app has `?native=1` and plugins synced (`npx cap sync`) |
| Blank app | Check network; fall back to `capacitor.config.local.json` |
| iOS pods | `cd ios/App && pod install` |
| Plugin missing | `npm install && npx cap sync` |
