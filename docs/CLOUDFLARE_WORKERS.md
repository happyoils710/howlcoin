# Howlscan on Cloudflare Workers

Full guide for putting **howlscan.org** on Cloudflare’s edge while keeping the Howlcoin chain API on the VPS.

App code: [`apps/howlscan-edge/`](../apps/howlscan-edge/README.md)

## Why this shape

Howlscan is a **Python explorer** (chain reads, auth, wrap/bridge APIs) plus **static wallet assets**.  
Rewriting the whole backend in Workers would block mining/API features. Instead:

1. **Edge** = static + HTML inject + short API cache (Workers Assets + Worker)
2. **Origin** = VPS explorer (`howlcoin-explorer`) for `/api/public/*` and server HTML

That matches Cloudflare’s 2026 recommendation (Workers + assets) without abandoning the ledger node.

## DNS checklist

| Name | Type | Content | Proxy status |
|------|------|---------|--------------|
| `origin` | A | VPS IP `147.182.223.204` | **DNS only** |
| `@` / `howlscan.org` | Worker custom domain | Worker `howlscan` | Proxied |
| `www` | Worker custom domain | Worker `howlscan` | Proxied |

Remove or replace the old orange-cloud A record on `@` that pointed only at the VPS once the Worker custom domain is attached (Cloudflare will manage the apex via the Worker).

## Nginx origin Host

Allow `origin.howlscan.org` (and keep `howlscan.org`) in nginx `server_name` so grey-cloud origin works:

```nginx
server_name howlscan.org www.howlscan.org origin.howlscan.org;
```

TLS: either certbot for `origin.howlscan.org`, or temporarily `ORIGIN=http://147.182.223.204` with Worker setting Host (prefer HTTPS).

## GitHub → Cloudflare

### Option A — Dashboard (easiest)

1. Workers & Pages → Create → Connect GitHub → `howlcoin`
2. Root: `apps/howlscan-edge`
3. Build: `npm run build`
4. Deploy: `npx wrangler deploy`
5. Env production: `ORIGIN=https://origin.howlscan.org`, `TRIPPY_DEFAULT=mild`

### Option B — GitHub Actions

See `.github/workflows/howlscan-edge.yml`.

Create API token: Cloudflare → My Profile → API Tokens → **Edit Cloudflare Workers** template.  
Secrets: `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`.

## Rollback

- Detach Worker routes / custom domains  
- Point `@` A record orange-cloud back to VPS IP  
- Site serves only from nginx again  

## Security notes

- Never put wallet seed material in Worker env  
- Auth cookies stay origin-bound; if you change domains carefully test `/api/public/auth/*`  
- Edge cache skips `/api/public/auth/*`  

