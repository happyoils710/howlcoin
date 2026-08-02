#!/usr/bin/env node
/**
 * Build the Capacitor www/ bundle:
 *  - Shell loads live Howl wallet (howlscan.org/app) by default
 *  - Offline fallback ships a local copy of public-wallet.html
 *  - Bridge enables native in-app browser (no iframe X-Frame blocks)
 */
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const REPO = path.resolve(ROOT, "..");
const WWW = path.join(ROOT, "www");
const ASSETS = path.join(REPO, "assets");

const LIVE_APP = process.env.HOWL_APP_URL || "https://howlscan.org/app";

function ensureDir(d) {
  fs.mkdirSync(d, { recursive: true });
}

function copyIfExists(src, dest) {
  if (!fs.existsSync(src)) {
    console.warn("skip missing", src);
    return false;
  }
  ensureDir(path.dirname(dest));
  fs.copyFileSync(src, dest);
  return true;
}

ensureDir(WWW);
ensureDir(path.join(WWW, "assets"));

// Local fallback assets (PWA still works offline-ish for shell)
const copies = [
  ["public-wallet.html", "assets/public-wallet.html"],
  ["howl-crypto.mjs", "assets/howl-crypto.mjs"],
  ["wallet-swap.mjs", "assets/wallet-swap.mjs"],
  ["wallet-nft.mjs", "assets/wallet-nft.mjs"],
  ["wallet-connect.mjs", "assets/wallet-connect.mjs"],
  ["wallet-manifest.webmanifest", "assets/wallet-manifest.webmanifest"],
  ["wallet-sw.js", "assets/wallet-sw.js"],
  ["howlcoin-logo-meme-pup-coin.jpg", "assets/howlcoin-logo-meme-pup-coin.jpg"],
];
for (const [from, to] of copies) {
  copyIfExists(path.join(ASSETS, from), path.join(WWW, to));
}

const bridgeJs = fs.readFileSync(path.join(ROOT, "src/howl-native-bridge.js"), "utf8");

// Capacitor entry: prefer live wallet, fallback to bundled HTML
const indexHtml = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover, maximum-scale=1"/>
  <meta name="color-scheme" content="dark light"/>
  <meta name="theme-color" content="#03010a"/>
  <title>Howlcoin</title>
  <style>
    html,body{margin:0;height:100%;background:#03010a;color:#e8fff8;font-family:system-ui,sans-serif}
    #boot{display:flex;flex-direction:column;align-items:center;justify-content:center;height:100%;gap:12px;padding:24px;text-align:center}
    #boot h1{font-size:1.4rem;margin:0;letter-spacing:.04em}
    #boot p{opacity:.7;margin:0;font-size:.9rem;max-width:280px;line-height:1.4}
    #boot button{margin-top:8px;border:0;border-radius:12px;padding:12px 18px;font-weight:700;background:#00ffc6;color:#03010a;cursor:pointer}
    #frame{position:fixed;inset:0;border:0;width:100%;height:100%;display:none;background:#03010a}
    #frame.on{display:block}
    .err{color:#ff8b8b;font-size:.85rem}
  </style>
</head>
<body>
  <div id="boot">
    <h1>Howlcoin</h1>
    <p id="status">Starting Howl Search browser…</p>
    <button type="button" id="btnLocal" style="display:none">Open offline wallet</button>
    <p class="err" id="err"></p>
  </div>
  <iframe id="frame" title="Howl wallet" allow="clipboard-read; clipboard-write; publickey-credentials-get *"></iframe>
  <script>
    window.HOWL_NATIVE = {
      liveAppUrl: ${JSON.stringify(LIVE_APP)},
      localAppUrl: "./assets/public-wallet.html",
      platform: "capacitor-shell"
    };
  </script>
  <script src="./howl-native-bridge.js"></script>
  <script src="./boot.js"></script>
</body>
</html>
`;

const bootJs = `/* Capacitor shell boot — load live wallet or local fallback */
(function () {
  var status = document.getElementById("status");
  var err = document.getElementById("err");
  var frame = document.getElementById("frame");
  var boot = document.getElementById("boot");
  var btnLocal = document.getElementById("btnLocal");
  var live = (window.HOWL_NATIVE && window.HOWL_NATIVE.liveAppUrl) || "https://howlscan.org/app";
  var local = (window.HOWL_NATIVE && window.HOWL_NATIVE.localAppUrl) || "./assets/public-wallet.html";

  function showFrame(url) {
    status.textContent = "Loading…";
    frame.onload = function () {
      boot.style.display = "none";
      frame.classList.add("on");
      // inject bridge into child when same-origin local; live origin uses postMessage
      tryInject();
    };
    frame.onerror = function () {
      err.textContent = "Failed to load " + url;
      btnLocal.style.display = "inline-block";
    };
    frame.src = url;
  }

  function tryInject() {
    // Live howlscan.org is cross-origin — wallet detects Capacitor via parent flag + bridge script on CDN path.
    // Local bundled wallet is same-origin-ish via file/capacitor — try direct inject.
    try {
      var doc = frame.contentDocument;
      if (!doc) return;
      if (doc.getElementById("howl-native-bridge")) return;
      var s = doc.createElement("script");
      s.id = "howl-native-bridge";
      s.src = "../howl-native-bridge.js";
      (doc.head || doc.documentElement).appendChild(s);
    } catch (e) {
      // cross-origin — wallet must load bridge itself when ?native=1
    }
  }

  btnLocal.onclick = function () {
    err.textContent = "";
    showFrame(local);
  };

  // Prefer live app with native marker so public-wallet enables Capacitor bridge
  var liveNative = live + (live.indexOf("?") >= 0 ? "&" : "?") + "native=1";

  // Quick connectivity probe
  var ctrl = typeof AbortController !== "undefined" ? new AbortController() : null;
  var t = setTimeout(function () {
    if (ctrl) ctrl.abort();
  }, 6000);

  var opts = { method: "GET", cache: "no-store" };
  if (ctrl) opts.signal = ctrl.signal;

  fetch(live, opts)
    .then(function (r) {
      clearTimeout(t);
      if (!r.ok) throw new Error("HTTP " + r.status);
      showFrame(liveNative);
    })
    .catch(function () {
      clearTimeout(t);
      status.textContent = "Offline — using bundled wallet";
      btnLocal.style.display = "inline-block";
      showFrame(local + (local.indexOf("?") >= 0 ? "&" : "?") + "native=1");
    });
})();
`;

fs.writeFileSync(path.join(WWW, "index.html"), indexHtml);
fs.writeFileSync(path.join(WWW, "boot.js"), bootJs);
fs.writeFileSync(path.join(WWW, "howl-native-bridge.js"), bridgeJs);

// Also patch local public-wallet copy to always include bridge when native=1
// (live site will get the same patch from repo deploy)

console.log("www ready →", WWW);
console.log("live app:", LIVE_APP);
console.log("bundled assets:", copies.length);
