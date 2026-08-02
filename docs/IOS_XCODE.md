# Run Howlcoin wallet in Xcode (iOS app)

The native app lives in `native/` (Capacitor). Xcode builds the **Howlcoin** shell; the wallet UI loads from **howlscan.org** (or a local bundle).

---

## 0. Prerequisites (one-time)

### A. Full Xcode (you already have `/Applications/Xcode.app`)

Open **Terminal** and run (enter your Mac password):

```bash
sudo xcode-select -s /Applications/Xcode.app/Contents/Developer
sudo xcodebuild -license accept
xcodebuild -version
```

You should see something like `Xcode 15.x` / `16.x`, **not** an error about Command Line Tools.

Also open Xcode once:

1. Open **Xcode** from Applications  
2. **Xcode → Settings → Platforms** (or Components)  
3. Install an **iOS Simulator** runtime if prompted  
4. If asked, install extra components

### B. Node 18+

```bash
export PATH="/usr/local/opt/node@20/bin:$PATH"
node -v   # must be v18 or higher
```

### C. CocoaPods

```bash
# easiest with Homebrew
brew install cocoapods
pod --version
```

---

## 1. Install JS deps & sync Capacitor

```bash
export PATH="/usr/local/opt/node@20/bin:$PATH"
cd ~/Desktop/howlcoin/native

npm install
npm run cap:sync
```

`cap:sync` copies the web shell and links iOS plugins.

---

## 2. Install iOS pods

```bash
cd ~/Desktop/howlcoin/native/ios/App
pod install
cd ~/Desktop/howlcoin/native
```

If `pod install` fails with encoding errors:

```bash
export LANG=en_US.UTF-8
pod install
```

---

## 3. Open the project in Xcode

**Important:** open the **workspace**, not the `.xcodeproj`.

### Option A — Capacitor CLI

```bash
export PATH="/usr/local/opt/node@20/bin:$PATH"
cd ~/Desktop/howlcoin/native
npx cap open ios
```

### Option B — Finder / open command

```bash
open ~/Desktop/howlcoin/native/ios/App/App.xcworkspace
```

---

## 4. Run on Simulator

In Xcode:

1. Top bar: select scheme **App**  
2. Device: pick an iPhone simulator (e.g. **iPhone 16**)  
3. Click the **▶ Run** button (or `Cmd + R`)  
4. Wait for build → Simulator launches → Howlcoin wallet loads  

Default config loads:

```text
https://howlscan.org/app?native=1
```

So you need **network** on the simulator. Search / Discover will open sites in the **native in-app browser** (not a blocked iframe).

---

## 5. Run on a real iPhone

1. Plug in the iPhone, unlock it, trust the computer  
2. In Xcode top bar, select **your device**  
3. **Signing & Capabilities** on target **App**:  
   - Check **Automatically manage signing**  
   - **Team**: your Apple ID (free account works for personal device)  
4. If bundle id conflicts, change **Bundle Identifier** to something unique, e.g.  
   `org.howlscan.wallet.yourname`  
5. **▶ Run**  
6. On the phone: **Settings → General → VPN & Device Management** → trust your developer certificate  

---

## Offline / no howlscan.org

Use the bundled wallet HTML instead of the live site:

```bash
cd ~/Desktop/howlcoin/native
cp capacitor.config.local.json capacitor.config.json
npm run cap:sync
cd ios/App && pod install && cd ../..
npx cap open ios
```

Then Run again in Xcode.

---

## After you change the web wallet

Web-only fixes on howlscan.org appear after a **reload** (remote mode).

If you change Capacitor plugins or `capacitor.config.json`:

```bash
cd ~/Desktop/howlcoin/native
npm run cap:sync
cd ios/App && pod install
# then rebuild in Xcode (Cmd+R)
```

---

## Common errors

| Error | Fix |
|--------|-----|
| `xcodebuild requires Xcode, but active developer directory is CommandLineTools` | `sudo xcode-select -s /Applications/Xcode.app/Contents/Developer` |
| `pod: command not found` | `brew install cocoapods` |
| `No such module 'Capacitor'` | Open **App.xcworkspace**, run `pod install` in `native/ios/App` |
| Blank white screen | Check Mac/simulator internet; or use local config above |
| Signing error | Add Apple ID under Xcode → Settings → Accounts; pick Team |
| CocoaPods UTF-8 | `export LANG=en_US.UTF-8` then `pod install` |

---

## Quick copy-paste (after Xcode is selected)

```bash
export PATH="/usr/local/opt/node@20/bin:$PATH"
export LANG=en_US.UTF-8

cd ~/Desktop/howlcoin/native
npm install
npm run cap:sync
cd ios/App && pod install && cd ../..
npx cap open ios
```

Then in Xcode: pick a simulator → **▶ Run**.
