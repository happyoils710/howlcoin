# Howlscan Edge (Cloudflare Workers)

Deploy **howlscan.org** on Cloudflare’s global network:

| Layer | What |
|--------|------|
| **Workers Assets** | Wallet (`/app`), whitepaper, CSS/JS/images — edge CDN |
| **Worker** | Proxy `/api/*` + explorer HTML to your VPS origin |
| **Trippy theme** | Nebula + stars + trip toggle (Off / Mild / Full) |
| **Edge cache** | Short TTL on public GET APIs |

> **2026 path:** Workers + static assets (not legacy Pages).  
> Connect this folder to GitHub in the Cloudflare dashboard for auto-deploy.

## Architecture

```
Browser → Cloudflare Worker (howlscan.org)
            ├─ /assets/*  /app  /whitepaper  → ASSETS (this repo build)
            ├─ /api/edge/*                   → Worker only
            └─ /api/*  + explorer HTML       → ORIGIN (VPS, grey-cloud)
```

**Critical:** `ORIGIN` must **not** be `howlscan.org` once the Worker owns that hostname (proxy loop).  
Use a DNS-only (grey cloud) name:

| DNS record | Type | Value | Proxy |
|------------|------|-------|--------|
| `origin.howlscan.org` | A | `147.182.223.204` | **DNS only** (grey) |
| `howlscan.org` | — | Worker route / custom domain | Proxied by Worker |
| `www.howlscan.org` | CNAME / Worker | same | Worker |

VPS nginx should still serve the explorer on that IP (Host: `howlscan.org` or `origin.howlscan.org`).

## One-time setup

### 1. Node deps

```bash
cd apps/howlscan-edge
npm install
```

### 2. Cloudflare account

```bash
npx wrangler login
```

### 3. DNS (Cloudflare dashboard → howlscan.org)

1. Add **A** `origin` → `147.182.223.204`, cloud **grey** (DNS only).
2. Confirm nginx on VPS answers:
   ```bash
   curl -sS -H 'Host: origin.howlscan.org' http://147.182.223.204/api/public/summary | head
   # or with TLS if you terminate on origin:
   curl -sS https://origin.howlscan.org/api/public/summary | head
   ```
3. If TLS on origin is awkward, set `"ORIGIN": "http://147.182.223.204"` temporarily and send `Host: howlscan.org` (see worker vars / wrangler). Prefer HTTPS origin.

### 4. Deploy

```bash
npm run deploy
```

### 5. Attach domain

Cloudflare dashboard → **Workers & Pages** → **howlscan** → **Settings** → **Domains & Routes**:

- Add `howlscan.org`
- Add `www.howlscan.org`

Or uncomment `routes` in `wrangler.jsonc` and redeploy.

### 6. Git-connected deploys (recommended)

1. [Cloudflare Dashboard](https://dash.cloudflare.com) → **Workers & Pages** → **Create** → **Import a repository** (GitHub).
2. Select **`happyoils710/howlcoin`** (or your fork).
3. Configure:
   - **Root directory:** `apps/howlscan-edge`
   - **Build command:** `npm run build`
   - **Deploy command:** `npx wrangler deploy`
   - Production branch: `main`
4. Add env var **ORIGIN** = `https://origin.howlscan.org` (or your origin).
5. Every push to `main` rebuilds assets + deploys the Worker.

Alternatively use the included GitHub Action (`.github/workflows/howlscan-edge.yml`) with secret `CLOUDFLARE_API_TOKEN` + `CLOUDFLARE_ACCOUNT_ID`.

## Local dev

```bash
cd apps/howlscan-edge
cp .dev.vars.example .dev.vars
# ORIGIN=https://howlscan.org   # fine for local dev (Worker not on that host yet)
npm run dev
```

Open the URL wrangler prints (usually http://127.0.0.1:8787).

Trip levels:

- `?trip=off` / `?trip=mild` / `?trip=full`
- Floating toggle (bottom-right) on every HTML page
- Cookie + `localStorage` key `howl_trip`

## Health checks

```bash
curl -sS https://howlscan.org/api/edge/health
curl -sS https://howlscan.org/api/public/summary
```

## What’s still on the VPS?

- Scrypt P2P seed (`howlcoin.service`)
- Explorer Python API + chain DB (`howlcoin-explorer.service`)
- Bridge / wrap relayers

The Worker does **not** replace the chain node — it fronts the website globally.

## Trippy design

- `assets/howl-trippy.css` — nebula, glass, neon, reduced-motion safe  
- `assets/howl-trippy.js` — starfield + trip toggle  
- Worker injects both into origin HTML and wallet HTML  

## Scripts

| npm script | Action |
|------------|--------|
| `build` | Copy `assets/` → `public/` |
| `dev` | build + `wrangler dev` |
| `deploy` | build + `wrangler deploy` |
| `tail` | live logs |

