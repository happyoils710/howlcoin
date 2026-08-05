/**
 * Howlscan edge Worker
 * --------------------
 * - Serves static wallet/assets from Workers Assets (global CDN)
 * - Proxies /api/* + explorer HTML to ORIGIN (VPS chain data)
 * - Injects trippy theme CSS/JS into HTML responses
 * - Edge-caches public GET APIs briefly for snappy explorer loads
 */

const API_CACHE_TTL = 8; // seconds — tip moves ~60s; keep short
const STATIC_CACHE_TTL = 300;

const HOP_BY_HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-authenticate",
  "proxy-authorization",
  "te",
  "trailers",
  "transfer-encoding",
  "upgrade",
  "content-encoding",
  "content-length",
  "cf-connecting-ip",
  "cf-ipcountry",
  "cf-ray",
  "cf-visitor",
  "x-forwarded-proto",
  "x-real-ip",
]);

function originBase(env: Env): string {
  const o = (env.ORIGIN || "https://origin.howlscan.org").replace(/\/$/, "");
  return o;
}

function copyRequestHeaders(req: Request): Headers {
  const h = new Headers();
  req.headers.forEach((v, k) => {
    if (HOP_BY_HOP.has(k.toLowerCase())) return;
    // Avoid broken Host toward origin; fetch() sets Host from URL
    if (k.toLowerCase() === "host") return;
    h.set(k, v);
  });
  // Tell origin this came through the edge (optional analytics)
  h.set("X-Howl-Edge", "workers");
  return h;
}

function copyResponseHeaders(res: Response, extra?: Record<string, string>): Headers {
  const h = new Headers();
  res.headers.forEach((v, k) => {
    if (HOP_BY_HOP.has(k.toLowerCase())) return;
    // Strip origin CSP if it blocks our inject; re-set soft defaults below
    if (k.toLowerCase() === "content-security-policy") return;
    h.set(k, v);
  });
  if (extra) {
    for (const [k, v] of Object.entries(extra)) h.set(k, v);
  }
  h.set("X-Howl-Edge", "1");
  return h;
}

function isHtml(res: Response, path: string): boolean {
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("text/html")) return true;
  // some origin routes omit CT
  if (path === "/" || path.endsWith(".html") || !path.includes(".")) return ct === "" || ct.includes("text");
  return false;
}

function tripLevel(request: Request, env: Env): "off" | "mild" | "full" {
  const url = new URL(request.url);
  const q = (url.searchParams.get("trip") || "").toLowerCase();
  if (q === "off" || q === "mild" || q === "full") return q;
  const cookie = request.headers.get("Cookie") || "";
  const m = cookie.match(/(?:^|;\s*)howl_trip=(off|mild|full)/i);
  if (m) return m[1].toLowerCase() as "off" | "mild" | "full";
  const d = (env.TRIPPY_DEFAULT || "mild").toLowerCase();
  if (d === "off" || d === "full") return d;
  return "mild";
}

/** Paths we prefer to serve from edge assets (fast + trippy wallet). */
function preferEdgeAsset(path: string): boolean {
  if (path.startsWith("/assets/")) return true;
  if (path === "/robots.txt" || path === "/security.txt") return true;
  if (path === "/whitepaper" || path === "/whitepaper.html") return true;
  if (path === "/app" || path === "/app/" || path.startsWith("/app/")) return true;
  if (path === "/classic" || path === "/classic/") return true;
  // wallet service worker + modules often live under /assets
  return false;
}

async function tryAssets(request: Request, env: Env, rewritePath?: string): Promise<Response | null> {
  if (!env.ASSETS) return null;
  let req = request;
  if (rewritePath) {
    const u = new URL(request.url);
    u.pathname = rewritePath;
    req = new Request(u.toString(), request);
  }
  const res = await env.ASSETS.fetch(req);
  if (res.status === 404) return null;
  // Clone with cache headers for static-ish paths
  const path = new URL(req.url).pathname;
  const headers = copyResponseHeaders(res, {
    "Cache-Control": path.startsWith("/assets/")
      ? `public, max-age=${STATIC_CACHE_TTL}, stale-while-revalidate=600`
      : "public, max-age=60",
  });
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

async function proxyOrigin(
  request: Request,
  env: Env,
  ctx: ExecutionContext,
  opts: { cacheGet?: boolean } = {},
): Promise<Response> {
  const url = new URL(request.url);
  const target = new URL(url.pathname + url.search, originBase(env));

  const init: RequestInit = {
    method: request.method,
    headers: copyRequestHeaders(request),
    redirect: "manual",
  };
  if (request.method !== "GET" && request.method !== "HEAD") {
    init.body = request.body;
    // @ts-expect-error duplex required for streaming body in some runtimes
    init.duplex = "half";
  }

  const cacheable =
    opts.cacheGet &&
    request.method === "GET" &&
    url.pathname.startsWith("/api/public/") &&
    !url.pathname.includes("/auth/");

  const cache = caches.default;
  const cacheKey = new Request(target.toString(), { method: "GET" });

  if (cacheable) {
    const hit = await cache.match(cacheKey);
    if (hit) {
      const h = new Headers(hit.headers);
      h.set("X-Howl-Cache", "HIT");
      return new Response(hit.body, { status: hit.status, headers: h });
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(target.toString(), init);
  } catch (e) {
    return Response.json(
      {
        error: "origin_unreachable",
        message: e instanceof Error ? e.message : String(e),
        origin: originBase(env),
        hint: "Set ORIGIN to a grey-cloud hostname (e.g. origin.howlscan.org → VPS).",
      },
      { status: 502 },
    );
  }

  // Follow one hop of same-host redirects rewriting Location to edge host
  if (upstream.status >= 300 && upstream.status < 400) {
    const loc = upstream.headers.get("Location");
    if (loc) {
      try {
        const abs = new URL(loc, target);
        if (abs.hostname === target.hostname || abs.hostname.endsWith("howlscan.org")) {
          const edgeLoc = new URL(abs.pathname + abs.search, url.origin).toString();
          const h = copyResponseHeaders(upstream, { Location: edgeLoc });
          return new Response(null, { status: upstream.status, headers: h });
        }
      } catch {
        /* fall through */
      }
    }
  }

  const path = url.pathname;
  let body: BodyInit | null = upstream.body;
  let headers = copyResponseHeaders(upstream);

  if (request.method === "GET" && isHtml(upstream, path)) {
    const trip = tripLevel(request, env);
    const html = await upstream.text();
    body = injectTrippy(html, trip, url.origin);
    headers.set("Content-Type", "text/html; charset=utf-8");
    headers.set("Cache-Control", "public, max-age=30, stale-while-revalidate=60");
    headers.append(
      "Set-Cookie",
      `howl_trip=${trip}; Path=/; Max-Age=31536000; SameSite=Lax`,
    );
  }

  const out = new Response(body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers,
  });

  if (cacheable && upstream.ok) {
    const toCache = out.clone();
    const ch = new Headers(toCache.headers);
    ch.set("Cache-Control", `public, max-age=${API_CACHE_TTL}`);
    ch.set("X-Howl-Cache", "STORE");
    ctx.waitUntil(cache.put(cacheKey, new Response(toCache.body, { status: toCache.status, headers: ch })));
    headers.set("X-Howl-Cache", "MISS");
  }

  return out;
}

function injectTrippy(html: string, trip: "off" | "mild" | "full", origin: string): string {
  if (trip === "off") {
    // still expose toggle bootstrap so user can turn trip back on
    const boot = `<script src="/assets/howl-trippy.js" data-trip="off" defer></script>`;
    if (html.includes("</head>")) return html.replace("</head>", `${boot}\n</head>`);
    return boot + html;
  }
  const css = `<link rel="stylesheet" href="/assets/howl-trippy.css" data-howl-trip="${trip}"/>`;
  const boot = `<script src="/assets/howl-trippy.js" data-trip="${trip}" defer></script>`;
  const cls = `howl-trip howl-trip-${trip}`;
  let out = html;
  if (/<html[^>]*>/i.test(out)) {
    out = out.replace(/<html([^>]*)>/i, (_m, attrs) => {
      if (/class=/i.test(attrs)) {
        return `<html${attrs.replace(/class=["']([^"']*)["']/, `class="$1 ${cls}"`)}>`;
      }
      return `<html${attrs} class="${cls}">`;
    });
  } else {
    out = `<!DOCTYPE html><html class="${cls}">` + out;
  }
  if (out.includes("</head>")) {
    out = out.replace("</head>", `${css}\n${boot}\n</head>`);
  } else {
    out = css + boot + out;
  }
  // void backdrop layer for mild/full
  if (out.includes("<body")) {
    out = out.replace(
      /<body([^>]*)>/i,
      `<body$1><div class="howl-trip-fx" aria-hidden="true"><div class="howl-nebula"></div><canvas class="howl-stars" id="howlStars"></canvas></div>`,
    );
  }
  void origin;
  return out;
}

export default {
  async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
    const url = new URL(request.url);
    const path = url.pathname;

    // Edge health (does not hit origin)
    if (path === "/api/edge/health" || path === "/cdn-cgi/howl/health") {
      return Response.json({
        ok: true,
        service: "howlscan-edge",
        trippy: env.TRIPPY_DEFAULT || "mild",
        origin: originBase(env),
        ts: Date.now(),
      });
    }

    // Trip toggle API (sets cookie)
    if (path === "/api/edge/trip" && request.method === "POST") {
      let level = "mild";
      try {
        const j = (await request.json()) as { level?: string };
        if (j.level === "off" || j.level === "mild" || j.level === "full") level = j.level;
      } catch {
        /* keep mild */
      }
      return new Response(JSON.stringify({ ok: true, level }), {
        headers: {
          "Content-Type": "application/json",
          "Set-Cookie": `howl_trip=${level}; Path=/; Max-Age=31536000; SameSite=Lax`,
        },
      });
    }

    // Prefer edge static for wallet + theme assets
    if (preferEdgeAsset(path)) {
      // Map /app → public-wallet.html
      if (path === "/app" || path === "/app/" || path === "/classic" || path === "/classic/") {
        const asset =
          (await tryAssets(request, env, "/app/index.html")) ||
          (await tryAssets(request, env, "/app.html")) ||
          (await tryAssets(request, env, "/public-wallet.html"));
        if (asset) {
          // Inject trippy into wallet HTML too
          const trip = tripLevel(request, env);
          const html = injectTrippy(await asset.text(), trip, url.origin);
          return new Response(html, {
            status: 200,
            headers: {
              "Content-Type": "text/html; charset=utf-8",
              "Cache-Control": "public, max-age=60",
              "X-Howl-Edge": "asset-app",
              "Set-Cookie": `howl_trip=${trip}; Path=/; Max-Age=31536000; SameSite=Lax`,
            },
          });
        }
      }
      if (path === "/whitepaper" || path === "/whitepaper/") {
        const asset = await tryAssets(request, env, "/whitepaper.html");
        if (asset) {
          const trip = tripLevel(request, env);
          const html = injectTrippy(await asset.text(), trip, url.origin);
          return new Response(html, {
            status: 200,
            headers: {
              "Content-Type": "text/html; charset=utf-8",
              "Cache-Control": "public, max-age=120",
              "X-Howl-Edge": "asset-whitepaper",
            },
          });
        }
      }
      const asset = await tryAssets(request, env);
      if (asset) return asset;
      // fall through to origin for missing assets
    }

    // Public APIs → origin (short edge cache on GET)
    if (path.startsWith("/api/")) {
      return proxyOrigin(request, env, ctx, { cacheGet: true });
    }

    // Everything else (explorer SPA shell from Python) → origin + trippy inject
    return proxyOrigin(request, env, ctx, { cacheGet: false });
  },
} satisfies ExportedHandler<Env>;
