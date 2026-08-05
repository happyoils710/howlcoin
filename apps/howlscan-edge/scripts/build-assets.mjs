/**
 * Copy Howlscan static files into apps/howlscan-edge/public for Workers Assets.
 * Compatible with Node 18+ (uses fs.cp when available, else recursive copyFile).
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const edgeRoot = path.join(__dirname, "..");
const repoRoot = path.join(edgeRoot, "..", "..");
const assetsSrc = path.join(repoRoot, "assets");
const publicDir = path.join(edgeRoot, "public");

function must(p, label) {
  if (!fs.existsSync(p)) {
    console.error(`Missing ${label}: ${p}`);
    process.exit(1);
  }
}

function rmrf(p) {
  if (!fs.existsSync(p)) return;
  if (typeof fs.rmSync === "function") {
    fs.rmSync(p, { recursive: true, force: true });
    return;
  }
  // fallback
  const st = fs.statSync(p);
  if (st.isDirectory()) {
    for (const name of fs.readdirSync(p)) rmrf(path.join(p, name));
    fs.rmdirSync(p);
  } else fs.unlinkSync(p);
}

function mkdirp(p) {
  fs.mkdirSync(p, { recursive: true });
}

function copyFile(from, to) {
  mkdirp(path.dirname(to));
  fs.copyFileSync(from, to);
}

function copyRecursive(from, to) {
  const st = fs.statSync(from);
  if (st.isDirectory()) {
    mkdirp(to);
    for (const name of fs.readdirSync(from)) {
      copyRecursive(path.join(from, name), path.join(to, name));
    }
  } else {
    copyFile(from, to);
  }
}

must(assetsSrc, "repo assets/");

rmrf(path.join(publicDir, "assets"));
rmrf(path.join(publicDir, "app"));
mkdirp(path.join(publicDir, "assets"));
mkdirp(path.join(publicDir, "app"));

const files = [
  "howl-site-theme.css",
  "howl-site-theme.js",
  "howl-site-theme-boot.js",
  "howl-trippy.css",
  "howl-trippy.js",
  "howl-crypto.mjs",
  "howl-native-bridge.js",
  "wallet-connect.mjs",
  "wallet-nft.mjs",
  "wallet-swap.mjs",
  "wallet-sw.js",
  "wallet-manifest.webmanifest",
  "howlcoin-logo-meme-pup-coin.jpg",
  "robots.txt",
  "security.txt",
  "whitepaper.html",
  "public-wallet.html",
];

for (const f of files) {
  const from = path.join(assetsSrc, f);
  if (!fs.existsSync(from)) {
    console.warn("skip missing", f);
    continue;
  }
  if (f === "public-wallet.html") {
    copyFile(from, path.join(publicDir, "app", "index.html"));
    copyFile(from, path.join(publicDir, "public-wallet.html"));
  } else if (f === "whitepaper.html" || f === "robots.txt" || f === "security.txt") {
    copyFile(from, path.join(publicDir, f));
  } else {
    copyFile(from, path.join(publicDir, "assets", f));
  }
}

const pack = path.join(assetsSrc, "pack-wallet");
if (fs.existsSync(pack)) {
  copyRecursive(pack, path.join(publicDir, "assets", "pack-wallet"));
}

fs.writeFileSync(
  path.join(publicDir, "edge-fallback.html"),
  `<!DOCTYPE html><html class="howl-trip howl-trip-mild"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Howlscan Edge</title>
<link rel="stylesheet" href="/assets/howl-trippy.css"/>
<script src="/assets/howl-trippy.js" data-trip="mild" defer></script>
</head><body style="font-family:system-ui;padding:2rem;color:#f2f4ff">
<h1>Howlscan edge is live</h1>
<p>Static assets are on Cloudflare. Chain API comes from origin.</p>
<p><a href="/" style="color:#00f0ff">Explorer</a> · <a href="/app/" style="color:#00f0ff">Wallet</a> ·
<a href="/api/edge/health" style="color:#00f0ff">Edge health</a></p>
</body></html>
`,
);

fs.writeFileSync(path.join(publicDir, ".assets-built"), new Date().toISOString() + "\n");
console.log("howlscan-edge public/ ready →", publicDir);
