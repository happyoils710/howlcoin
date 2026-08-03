"""
Howlcoin multi-chain block explorer (read-only).

Default network:
  - public → ~/.howlcoin or HOWL_PUBLIC_DATA (seed / main ledger)

Run:
  python3 -m howl explorer
  open http://127.0.0.1:42080/
"""

from __future__ import annotations

import concurrent.futures
import html as html_lib
import json
import mimetypes
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .blockchain import Blockchain
from .config import (
    DEFAULT_DATA_DIR,
    DEFAULT_TX_FEE_HOWLIES,
    MIN_TX_FEE_HOWLIES,
)
from .crypto import is_valid_address
from .wallet import format_howl

# Live seed node RPC (for broadcasting browser-signed txs into the public mempool)
NODE_RPC = os.environ.get("HOWL_NODE_RPC", "http://127.0.0.1:42070").rstrip("/")
# Server-side Solana RPC (browser mainnet endpoints often 403 howlscan.org origin)
SOLANA_RPC = os.environ.get(
    "SOLANA_RPC", "https://api.mainnet-beta.solana.com"
).rstrip("/")


def solana_rpc_call(method: str, params: list, timeout: int = 20) -> Any:
    """JSON-RPC to Solana from the server (avoids browser CORS / origin bans)."""
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode()
    endpoints = [
        SOLANA_RPC,
        "https://api.mainnet-beta.solana.com",
        "https://solana-rpc.publicnode.com",
    ]
    # de-dupe preserve order
    seen = set()
    uniq = []
    for e in endpoints:
        if e and e not in seen:
            seen.add(e)
            uniq.append(e)
    last_err: Optional[Exception] = None
    for url in uniq:
        try:
            req = urllib.request.Request(
                url,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Howlscan/0.5 (+https://howlscan.org)",
                    "Accept": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if data.get("error"):
                last_err = RuntimeError(str(data["error"]))
                continue
            return data.get("result")
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"Solana RPC failed: {last_err}")


# CoinGecko IDs used by the public wallet portfolio total
_PRICE_COIN_IDS = (
    "bitcoin,ethereum,solana,binancecoin,avalanche-2,litecoin,bitcoin-cash,"
    "dogecoin,tether,usd-coin,dai,shiba-inu,leo-token,wrapped-bitcoin,"
    "chainlink,uniswap,tezos,tron,ripple,stellar,hyperliquid"
)
_price_cache: Dict[str, Any] = {"ts": 0.0, "data": None}


def fetch_market_prices(force: bool = False) -> Dict[str, Any]:
    """
    USD + BTC prices for wallet assets (CoinGecko simple/price, cached ~60s).
    Returns { prices: { id: {usd, btc} }, updated, source }.
    """
    now = time.time()
    if (
        not force
        and _price_cache.get("data")
        and (now - float(_price_cache.get("ts") or 0)) < 60
    ):
        return _price_cache["data"]  # type: ignore[return-value]
    url = (
        "https://api.coingecko.com/api/v3/simple/price?"
        + urllib.parse.urlencode(
            {
                "ids": _PRICE_COIN_IDS,
                "vs_currencies": "usd,btc",
            }
        )
    )
    try:
        raw = json.loads(
            _http_get(
                url,
                headers={
                    "User-Agent": "Howlscan/0.5 (+https://howlscan.org)",
                    "Accept": "application/json",
                },
                timeout=12,
            ).decode("utf-8", errors="ignore")
        )
        if not isinstance(raw, dict) or not raw:
            raise RuntimeError("empty price response")
        # normalize: ensure floats
        prices: Dict[str, Dict[str, float]] = {}
        for cid, row in raw.items():
            if not isinstance(row, dict):
                continue
            usd = row.get("usd")
            btc = row.get("btc")
            entry: Dict[str, float] = {}
            if usd is not None:
                try:
                    entry["usd"] = float(usd)
                except (TypeError, ValueError):
                    pass
            if btc is not None:
                try:
                    entry["btc"] = float(btc)
                except (TypeError, ValueError):
                    pass
            if entry:
                prices[cid] = entry
        # stables fallback if CG omits
        for stable in ("tether", "usd-coin", "dai"):
            prices.setdefault(stable, {"usd": 1.0, "btc": 0.0})
        if "bitcoin" in prices and prices["bitcoin"].get("usd"):
            btc_usd = prices["bitcoin"]["usd"]
            for stable in ("tether", "usd-coin", "dai"):
                if prices[stable].get("btc", 0) == 0 and btc_usd > 0:
                    prices[stable]["btc"] = 1.0 / btc_usd
            prices["bitcoin"]["btc"] = 1.0
        out = {
            "prices": prices,
            "updated": int(now),
            "source": "coingecko",
            "cached": False,
        }
        _price_cache["ts"] = now
        _price_cache["data"] = {**out, "cached": True}
        return out
    except Exception as e:
        if _price_cache.get("data"):
            stale = dict(_price_cache["data"])  # type: ignore[arg-type]
            stale["stale"] = True
            stale["error"] = str(e)
            return stale
        return {
            "prices": {},
            "updated": int(now),
            "source": "none",
            "error": str(e),
        }

# Optional wrapped-token contracts (for CMC/CoinCodex when you deploy them)
# HOWL is a *native* Scrypt coin — it has no default EVM/SPL contract.
HOWL_ERC20_CONTRACT = os.environ.get("HOWL_ERC20_CONTRACT", "").strip()
HOWL_BEP20_CONTRACT = os.environ.get("HOWL_BEP20_CONTRACT", "").strip()
HOWL_SPL_MINT = os.environ.get("HOWL_SPL_MINT", "").strip()
HOWL_SITE = os.environ.get("HOWL_SITE", "https://howlscan.org").rstrip("/")
HOWL_GITHUB = os.environ.get("HOWL_GITHUB", "https://github.com/happyoils710/howlcoin")
HOWL_SEED = os.environ.get("HOWL_SEED", "147.182.223.204:42069")
# WalletConnect / Reown Cloud project id (public client id — free at cloud.reown.com)
HOWL_WC_PROJECT_ID = os.environ.get("HOWL_WC_PROJECT_ID", "").strip()
# NFT media uploads (compressed images for Howlcoin mints)
MEDIA_DIR = Path(
    os.environ.get(
        "HOWL_MEDIA_DIR",
        str(Path(os.environ.get("HOWL_PUBLIC_DATA", "/var/lib/howlcoin")) / "media"),
    )
)
MEDIA_MAX_BYTES = int(os.environ.get("HOWL_MEDIA_MAX_BYTES", str(450_000)))


def _save_nft_media(
    image_b64: str,
    mime: str = "image/jpeg",
    base_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Decode base64 image, write under media dir, return public URL path."""
    import base64
    import hashlib
    import re

    raw_b64 = (image_b64 or "").strip()
    if raw_b64.startswith("data:"):
        # data:image/jpeg;base64,....
        m = re.match(r"^data:([^;]+);base64,(.+)$", raw_b64, re.I | re.S)
        if not m:
            raise ValueError("invalid data URL")
        mime = m.group(1).strip() or mime
        raw_b64 = m.group(2)
    raw_b64 = re.sub(r"\s+", "", raw_b64)
    try:
        data = base64.b64decode(raw_b64, validate=False)
    except Exception as e:
        raise ValueError(f"bad base64: {e}") from e
    if not data:
        raise ValueError("empty image")
    if len(data) > MEDIA_MAX_BYTES:
        raise ValueError(
            f"image too large ({len(data)} bytes; max {MEDIA_MAX_BYTES})"
        )
    mime = (mime or "image/jpeg").split(";")[0].strip().lower()
    allowed = {
        "image/jpeg": ".jpg",
        "image/jpg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
    }
    if mime not in allowed:
        # sniff
        if data[:3] == b"\xff\xd8\xff":
            mime, ext = "image/jpeg", ".jpg"
        elif data[:8] == b"\x89PNG\r\n\x1a\n":
            mime, ext = "image/png", ".png"
        elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
            mime, ext = "image/webp", ".webp"
        elif data[:6] in (b"GIF87a", b"GIF89a"):
            mime, ext = "image/gif", ".gif"
        else:
            raise ValueError("unsupported image type (use jpeg/png/webp/gif)")
    else:
        ext = allowed[mime]
    digest = hashlib.sha256(data).hexdigest()[:32]
    root = Path(base_dir) if base_dir else MEDIA_DIR
    root.mkdir(parents=True, exist_ok=True)
    fname = f"{digest}{ext}"
    path = root / fname
    if not path.is_file():
        path.write_bytes(data)
    rel = f"/media/{fname}"
    return {
        "url": f"{HOWL_SITE}{rel}",
        "path": rel,
        "sha256": digest,
        "bytes": len(data),
        "mime": mime,
    }


def howl_token_info(chain: Optional[Blockchain] = None) -> Dict[str, Any]:
    """
    Official project identifiers for explorers and market aggregators.
    Native HOWL is not an ERC-20; genesis hash is the chain fingerprint.
    """
    genesis = ""
    height = None
    tip = ""
    circulating = None
    if chain is not None:
        try:
            genesis = chain.genesis_hash()
            height = chain.height()
            tip = chain.tip()["hash"]
            circulating = chain.summary().get("circulating")
        except Exception:
            pass
    contracts = []
    if HOWL_ERC20_CONTRACT:
        contracts.append(
            {
                "chain": "ethereum",
                "standard": "ERC-20",
                "address": HOWL_ERC20_CONTRACT,
                "explorer": f"https://etherscan.io/token/{HOWL_ERC20_CONTRACT}",
            }
        )
    if HOWL_BEP20_CONTRACT:
        contracts.append(
            {
                "chain": "bsc",
                "standard": "BEP-20",
                "address": HOWL_BEP20_CONTRACT,
                "explorer": f"https://bscscan.com/token/{HOWL_BEP20_CONTRACT}",
            }
        )
    if HOWL_SPL_MINT:
        contracts.append(
            {
                "chain": "solana",
                "standard": "SPL",
                "address": HOWL_SPL_MINT,
                "explorer": f"https://solscan.io/token/{HOWL_SPL_MINT}",
            }
        )
    return {
        "name": "Howlcoin",
        "symbol": "HOWL",
        "type": "native_coin",
        "platform": "Howlcoin (own L1)",
        "algorithm": "Scrypt",
        "scrypt": {"N": 1024, "r": 1, "p": 1},
        "decimals": 8,
        "contract_address": None,  # native L1 — no single contract
        "contract_note": (
            "Howlcoin (HOWL) is a native Scrypt proof-of-work cryptocurrency "
            "with its own blockchain (not an ERC-20/BEP-20/SPL token). "
            "There is no smart-contract address for native HOWL. "
            "Use genesis_hash + explorer for chain identity. "
            "Optional wrapped contracts appear under contracts[] when deployed."
        ),
        "genesis_hash": genesis,
        "genesis_block_url": f"{HOWL_SITE}/#/public/block/0",
        "explorer": HOWL_SITE,
        "explorer_api": f"{HOWL_SITE}/api/public/summary",
        "website": HOWL_SITE,
        "whitepaper": f"{HOWL_SITE}/whitepaper",
        "github": HOWL_GITHUB,
        "wallet": f"{HOWL_SITE}/app",
        "seed_node": HOWL_SEED,
        "source_code": HOWL_GITHUB,
        "height": height,
        "tip": tip,
        "circulating": circulating,
        "contracts": contracts,  # wrapped tokens only
        "listing": {
            "coinmarketcap_hint": "Submit as a native coin (own blockchain), not a token. Put explorer + genesis_hash; leave contract blank or N/A.",
            "coingecko_hint": "Category: own blockchain / Scrypt. Explorer URL required. Contract only if listing a wrapped version.",
            "coincodex_hint": "Native coin — use website + block explorer. Contract address only for wrapped HOWL on ETH/BSC/SOL.",
        },
    }


# ---------------------------------------------------------------------------
# Howl Search — multi-source open-web index (server-side, in-app results)
# ---------------------------------------------------------------------------

_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
_HOWL_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _http_get(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 12) -> bytes:
    req = urllib.request.Request(url, headers=headers or _HOWL_HEADERS, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _http_post(
    url: str,
    data: bytes,
    headers: Optional[Dict[str, str]] = None,
    timeout: int = 12,
) -> bytes:
    h = dict(headers or _HOWL_HEADERS)
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _clean_html(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html_lib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def _unwrap_ddg(href: str) -> str:
    href = html_lib.unescape((href or "").strip())
    if "uddg=" in href:
        try:
            full = (
                href
                if "://" in href
                else ("https:" + href if href.startswith("//") else href)
            )
            parsed = urllib.parse.urlparse(full)
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("uddg"):
                return urllib.parse.unquote(qs["uddg"][0])
        except Exception:
            pass
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            return urllib.parse.unquote(m.group(1))
    if href.startswith("//"):
        return "https:" + href
    return href


def _is_ad_or_junk(link: str) -> bool:
    low = (link or "").lower()
    if "duckduckgo.com/y.js" in low or "ad_domain=" in low:
        return True
    if "bing.com/aclick" in low or "doubleclick" in low:
        return True
    return False


def _search_ddg(query: str, limit: int = 12) -> List[Dict[str, str]]:
    """Open web results via DuckDuckGo HTML (when not bot-blocked)."""
    post_url = "https://html.duckduckgo.com/html/"
    data = urllib.parse.urlencode({"q": query, "b": ""}).encode("utf-8")
    try:
        page = _http_post(post_url, data, timeout=12).decode("utf-8", errors="ignore")
    except Exception:
        get_url = post_url + "?" + urllib.parse.urlencode({"q": query})
        try:
            page = _http_get(get_url, timeout=12).decode("utf-8", errors="ignore")
        except Exception:
            return []

    if "anomaly" in page.lower() and page.count("result__a") == 0:
        return []

    blocks = re.findall(
        r'class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>'
        r'.*?class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
        page,
        flags=re.I | re.S,
    )
    if not blocks:
        blocks = []
        for m in re.finditer(
            r'<a\b([^>]*\bclass="[^"]*result__a[^"]*"[^>]*)>(.*?)</a>',
            page,
            flags=re.I | re.S,
        ):
            attrs, title = m.group(1), m.group(2)
            hm = re.search(r'href="([^"]+)"', attrs, flags=re.I)
            if not hm:
                continue
            tail = page[m.end() : m.end() + 800]
            sm = re.search(
                r'class="result__snippet"[^>]*>(.*?)</(?:a|td|div)',
                tail,
                flags=re.I | re.S,
            )
            blocks.append((hm.group(1), title, sm.group(1) if sm else ""))

    out: List[Dict[str, str]] = []
    seen = set()
    for href, title, snip in blocks:
        link = _unwrap_ddg(href)
        if not link.startswith("http") or _is_ad_or_junk(link):
            continue
        key = link.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "title": (_clean_html(title) or link)[:200],
                "url": link,
                "snippet": _clean_html(snip)[:280],
                "source": "web",
            }
        )
        if len(out) >= limit:
            break
    return out


def _search_mojeek(query: str, limit: int = 12) -> List[Dict[str, str]]:
    """Open web via Mojeek HTML (independent index — Howl Search source)."""
    url = "https://www.mojeek.com/search?" + urllib.parse.urlencode({"q": query})
    try:
        page = _http_get(url, timeout=12).decode("utf-8", errors="ignore")
    except Exception:
        return []

    out: List[Dict[str, str]] = []
    seen = set()
    for m in re.finditer(
        r'class="title"[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>',
        page,
        flags=re.I | re.S,
    ):
        link = html_lib.unescape(m.group(1).strip())
        title = _clean_html(m.group(2))
        if "mojeek.com" in link.lower():
            continue
        key = link.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        tail = page[m.end() : m.end() + 500]
        sm = re.search(r'<p class="s">(.*?)</p>', tail, flags=re.I | re.S)
        snip = _clean_html(sm.group(1)) if sm else ""
        out.append(
            {
                "title": (title or link)[:200],
                "url": link,
                "snippet": snip[:280],
                "source": "web",
            }
        )
        if len(out) >= limit:
            break
    return out


def _search_open_web(query: str, limit: int = 12) -> List[Dict[str, str]]:
    """Aggregate open-web SERPs; try multiple indexes for resilience."""
    # Mojeek first (reliable), then DDG when available
    primary = _search_mojeek(query, limit=limit)
    if len(primary) >= max(3, limit // 2):
        return primary[:limit]
    secondary = _search_ddg(query, limit=limit)
    seen = {r["url"].split("#", 1)[0].rstrip("/").lower() for r in primary}
    for r in secondary:
        key = r["url"].split("#", 1)[0].rstrip("/").lower()
        if key in seen:
            continue
        primary.append(r)
        seen.add(key)
        if len(primary) >= limit:
            break
    return primary[:limit]


def _search_wikipedia(query: str, limit: int = 4) -> List[Dict[str, str]]:
    url = "https://en.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {
            "action": "opensearch",
            "search": query,
            "limit": str(limit),
            "namespace": "0",
            "format": "json",
        }
    )
    try:
        raw = json.loads(
            _http_get(
                url,
                headers={
                    "User-Agent": "HowlSearch/0.5 (+https://howlscan.org)",
                    "Accept": "application/json",
                },
                timeout=8,
            ).decode("utf-8", errors="ignore")
        )
    except Exception:
        return []
    if not isinstance(raw, list) or len(raw) < 4:
        return []
    titles, descs, links = raw[1], raw[2], raw[3]
    out = []
    for i, title in enumerate(titles):
        link = links[i] if i < len(links) else ""
        if not link:
            continue
        out.append(
            {
                "title": str(title)[:200],
                "url": link,
                "snippet": (descs[i] if i < len(descs) else "")[:280] or "Wikipedia",
                "source": "wiki",
            }
        )
    return out


def _search_coingecko(query: str, limit: int = 4) -> List[Dict[str, str]]:
    """Crypto asset hits from CoinGecko public search."""
    url = "https://api.coingecko.com/api/v3/search?" + urllib.parse.urlencode(
        {"query": query}
    )
    try:
        raw = json.loads(
            _http_get(
                url,
                headers={
                    "User-Agent": "HowlSearch/0.5 (+https://howlscan.org)",
                    "Accept": "application/json",
                },
                timeout=8,
            ).decode("utf-8", errors="ignore")
        )
    except Exception:
        return []
    coins = (raw or {}).get("coins") or []
    out = []
    for c in coins[:limit]:
        cid = c.get("id") or ""
        name = c.get("name") or cid
        sym = (c.get("symbol") or "").upper()
        if not cid:
            continue
        out.append(
            {
                "title": f"{name} ({sym})" if sym else name,
                "url": f"https://www.coingecko.com/en/coins/{cid}",
                "snippet": f"Crypto asset · market rank #{c.get('market_cap_rank') or '—'}",
                "source": "crypto",
            }
        )
    return out


def web_search(query: str, limit: int = 12) -> List[Dict[str, str]]:
    """
    Howl Search — multi-source open-web search for in-app results.
    Merges general web, Wikipedia, and crypto market pages.
    """
    q = (query or "").strip()
    if not q:
        return []
    limit = max(1, min(20, int(limit)))

    by_src: Dict[str, List[Dict[str, str]]] = {"web": [], "wiki": [], "crypto": []}
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        futs = {
            pool.submit(_search_open_web, q, max(limit + 4, 12)): "web",
            pool.submit(_search_wikipedia, q, 3): "wiki",
            pool.submit(_search_coingecko, q, 3): "crypto",
        }
        for fut in concurrent.futures.as_completed(futs, timeout=18):
            try:
                src = futs[fut]
                by_src[src] = fut.result() or []
            except Exception:
                continue

    # Interleave sources so open-web always appears (not drowned by markets/wiki)
    quotas = {
        "web": max(limit - 4, limit // 2 + 1),
        "crypto": min(3, max(1, limit // 4)),
        "wiki": min(2, max(1, limit // 5)),
    }
    results: List[Dict[str, str]] = []
    seen = set()

    def take(src: str, n: int) -> None:
        for item in by_src.get(src) or []:
            if n <= 0 or len(results) >= limit:
                return
            link = item.get("url") or ""
            if not link.startswith("http"):
                continue
            key = link.split("#", 1)[0].rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "title": (item.get("title") or link)[:200],
                    "url": link,
                    "snippet": (item.get("snippet") or "")[:280],
                    "source": item.get("source") or src,
                }
            )
            n -= 1

    # Crypto + wiki first (small), then majority open web, then fill any remainder
    take("crypto", quotas["crypto"])
    take("wiki", quotas["wiki"])
    take("web", quotas["web"])
    for src in ("web", "crypto", "wiki"):
        if len(results) >= limit:
            break
        take(src, limit - len(results))
    return results


# ---------------------------------------------------------------------------
# Discover — crypto tech radar (RSS + web scouts + optional Grok agent)
# ---------------------------------------------------------------------------

_DISCOVER_CACHE: Dict[str, Any] = {"ts": 0.0, "payload": None}
_DISCOVER_TTL_SEC = 12 * 60  # 12 minutes

_DISCOVER_FEEDS: List[Tuple[str, str]] = [
    ("Cointelegraph", "https://cointelegraph.com/rss"),
    ("Decrypt", "https://decrypt.co/feed"),
    ("CryptoNews", "https://cryptonews.com/news/feed/"),
    ("Bitcoin Magazine", "https://bitcoinmagazine.com/.rss/full/"),
    ("Ethereum Blog", "https://blog.ethereum.org/feed.xml"),
    ("Solana News", "https://solana.com/news/rss.xml"),
    ("Crypto Tech (Reddit)", "https://www.reddit.com/r/CryptoTechnology/.rss"),
]

_CRYPTO_RELEVANCE = re.compile(
    r"\b(crypto|bitcoin|btc|ethereum|eth\b|solana|defi|nft|blockchain|web3|"
    r"layer[- ]?2|\bl2\b|zk|rollup|token|stablecoin|dex|amm|dao|mev|"
    r"mainnet|testnet|wallet|protocol|rwa|on-?chain|scrypt|howl|mining|"
    r"validator|consensus|smart contract|airdrop|perp|liquidity)\b",
    re.I,
)

_TRUSTED_CRYPTO_SOURCES = {
    "Cointelegraph",
    "Decrypt",
    "CryptoNews",
    "Bitcoin Magazine",
    "Ethereum Blog",
    "Solana News",
    "Crypto Tech (Reddit)",
    "Howl Scout",
}

_DISCOVER_SCOUT_QUERIES = [
    "new blockchain protocol launch",
    "new crypto L2 mainnet",
    "zero knowledge zk rollup crypto",
    "crypto AI agent protocol",
    "RWA tokenization blockchain",
    "new DeFi protocol 2026",
]

_CATEGORY_RULES: List[Tuple[str, re.Pattern]] = [
    ("ai", re.compile(r"\b(ai agent|llm|machine learning|artificial intelligence|grok|agentic)\b", re.I)),
    ("l2", re.compile(r"\b(layer[- ]?2|l2|rollup|optimistic|zk[- ]?evm|zk[- ]?sync|arbitrum|optimism|base chain)\b", re.I)),
    ("zk", re.compile(r"\b(zero[- ]knowledge|zk[- ]?proof|zkp|validity proof)\b", re.I)),
    ("defi", re.compile(r"\b(defi|amm|dex|lending|liquidity|yield|perp|swap)\b", re.I)),
    ("rwa", re.compile(r"\b(rwa|real[- ]world asset|tokeniz)\b", re.I)),
    ("security", re.compile(r"\b(hack|exploit|vulnerability|audit|bridge attack|rug)\b", re.I)),
    ("protocol", re.compile(r"\b(mainnet|testnet|protocol|consensus|scrypt|pow|pos|validator)\b", re.I)),
    ("nft", re.compile(r"\b(nft|ordinals|collectible)\b", re.I)),
    ("policy", re.compile(r"\b(sec |regulation|etf|law|ban|lawsuit)\b", re.I)),
]


def _parse_rss(xml_bytes: bytes, source: str, max_items: int = 12) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []

    # RSS 2.0 + Atom
    channel_items = root.findall(".//item")
    if not channel_items:
        # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for entry in root.findall(".//a:entry", ns)[:max_items]:
            title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
            link_el = entry.find("a:link", ns)
            link = ""
            if link_el is not None:
                link = link_el.get("href") or (link_el.text or "")
            summary = entry.findtext("a:summary", default="", namespaces=ns) or entry.findtext(
                "a:content", default="", namespaces=ns
            )
            published = entry.findtext("a:updated", default="", namespaces=ns) or entry.findtext(
                "a:published", default="", namespaces=ns
            )
            if title and link:
                items.append(
                    {
                        "title": _clean_html(title)[:200],
                        "url": link.strip(),
                        "snippet": _clean_html(summary or "")[:280],
                        "source": source,
                        "published": published or "",
                        "kind": "rss",
                    }
                )
        return items[:max_items]

    for it in channel_items[:max_items]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:
            guid = it.findtext("guid")
            if guid and str(guid).startswith("http"):
                link = str(guid).strip()
        desc = it.findtext("description") or it.findtext(
            "{http://purl.org/rss/1.0/modules/content/}encoded"
        ) or ""
        pub = it.findtext("pubDate") or it.findtext("published") or ""
        if not title or not link:
            continue
        items.append(
            {
                "title": _clean_html(title)[:200],
                "url": link,
                "snippet": _clean_html(desc)[:280],
                "source": source,
                "published": pub,
                "kind": "rss",
            }
        )
    return items


def _pub_ts(published: str) -> float:
    if not published:
        return 0.0
    try:
        return parsedate_to_datetime(published).timestamp()
    except Exception:
        pass
    try:
        # ISO-ish
        return time.mktime(time.strptime(published[:19], "%Y-%m-%dT%H:%M:%S"))
    except Exception:
        return 0.0


def _categorize(title: str, snippet: str) -> Tuple[str, List[str]]:
    blob = f"{title} {snippet}"
    tags: List[str] = []
    cat = "crypto"
    for name, rx in _CATEGORY_RULES:
        if rx.search(blob):
            tags.append(name)
            if cat == "crypto":
                cat = name
    # keyword tags
    for kw in ("solana", "ethereum", "bitcoin", "howl", "scrypt", "mev", "stablecoin"):
        if re.search(rf"\b{kw}\b", blob, re.I) and kw not in tags:
            tags.append(kw)
    return cat, tags[:6]


def _score_item(item: Dict[str, Any], now: float) -> float:
    title = item.get("title") or ""
    snip = item.get("snippet") or ""
    blob = f"{title} {snip}".lower()
    score = 1.0
    # recency
    ts = _pub_ts(item.get("published") or "")
    if ts > 0:
        age_h = max(0.0, (now - ts) / 3600.0)
        score += max(0.0, 8.0 - age_h / 6.0)  # fresher = higher
    # novelty signals
    for w, pts in (
        ("launch", 2.0),
        ("mainnet", 2.2),
        ("testnet", 1.2),
        ("raises", 1.0),
        ("funding", 1.0),
        ("open source", 1.5),
        ("ai agent", 2.5),
        ("protocol", 1.0),
        ("zk", 1.3),
        ("l2", 1.3),
        ("breakthrough", 1.5),
        ("first", 0.8),
        ("new ", 0.6),
    ):
        if w in blob:
            score += pts
    # downrank pure price spam a bit
    if re.search(r"\b(price prediction|to the moon|buy now)\b", blob):
        score -= 2.0
    if item.get("kind") == "scout":
        score += 0.8
    return score


def _fetch_feed(source: str, url: str) -> List[Dict[str, Any]]:
    try:
        raw = _http_get(
            url,
            headers={
                "User-Agent": _BROWSER_UA,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
            timeout=10,
        )
        return _parse_rss(raw, source, max_items=10)
    except Exception:
        return []


def _scout_web() -> List[Dict[str, Any]]:
    """Lightweight web scouts for emerging crypto tech mentions."""
    out: List[Dict[str, Any]] = []
    # rotate a couple queries each refresh for freshness
    idx = int(time.time() // _DISCOVER_TTL_SEC) % max(1, len(_DISCOVER_SCOUT_QUERIES))
    queries = [
        _DISCOVER_SCOUT_QUERIES[idx],
        _DISCOVER_SCOUT_QUERIES[(idx + 1) % len(_DISCOVER_SCOUT_QUERIES)],
        _DISCOVER_SCOUT_QUERIES[(idx + 2) % len(_DISCOVER_SCOUT_QUERIES)],
    ]
    for q in queries:
        try:
            for r in _search_open_web(q, limit=4):
                out.append(
                    {
                        "title": r["title"],
                        "url": r["url"],
                        "snippet": r.get("snippet") or "",
                        "source": "Howl Scout",
                        "published": "",
                        "kind": "scout",
                        "query": q,
                    }
                )
        except Exception:
            continue
    return out


def _xai_enrich(items: List[Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """Optional Grok agent: tag + 'why it matters' for top stories."""
    key = (os.environ.get("XAI_API_KEY") or "").strip()
    if not key or not items:
        return None
    slim = [
        {
            "i": i,
            "title": it.get("title"),
            "snippet": (it.get("snippet") or "")[:160],
            "source": it.get("source"),
        }
        for i, it in enumerate(items[:14])
    ]
    prompt = (
        "You are Howl Scout, a crypto-tech radar agent for the Howlcoin wallet.\n"
        "For each story, return JSON array only (no markdown) with objects:\n"
        '{"i": number, "category": short tag, "tags": [..], "why": one sentence why it matters for builders/traders}\n'
        "Focus on new protocols, L2s, ZK, DeFi, AI agents, RWA, security — skip pure price spam.\n"
        f"Stories:\n{json.dumps(slim, ensure_ascii=False)}"
    )
    body = json.dumps(
        {
            "model": "grok-4-1-fast-non-reasoning",
            "messages": [
                {"role": "system", "content": "Reply with pure JSON array only."},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.3,
        }
    ).encode("utf-8")
    try:
        raw = _http_post(
            "https://api.x.ai/v1/chat/completions",
            body,
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "User-Agent": "HowlDiscover/0.5",
            },
            timeout=28,
        )
        data = json.loads(raw.decode("utf-8", errors="ignore"))
        text = (
            ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        )
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        arr = json.loads(text)
        if not isinstance(arr, list):
            return None
        by_i = {int(x["i"]): x for x in arr if isinstance(x, dict) and "i" in x}
        enriched = []
        for i, it in enumerate(items):
            e = dict(it)
            meta = by_i.get(i)
            if meta:
                if meta.get("category"):
                    e["category"] = str(meta["category"])[:40]
                if meta.get("tags"):
                    e["tags"] = [str(t)[:24] for t in meta["tags"][:6]]
                if meta.get("why"):
                    e["why"] = str(meta["why"])[:240]
            enriched.append(e)
        return enriched
    except Exception:
        return None


def discover_feed(force: bool = False) -> Dict[str, Any]:
    """
    Aggregate live crypto-tech signals: RSS + web scouts + ranking agent.
    Cached for a few minutes to stay polite to publishers.
    """
    now = time.time()
    if (
        not force
        and _DISCOVER_CACHE.get("payload")
        and (now - float(_DISCOVER_CACHE.get("ts") or 0)) < _DISCOVER_TTL_SEC
    ):
        return _DISCOVER_CACHE["payload"]  # type: ignore[return-value]

    collected: List[Dict[str, Any]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        futs = [pool.submit(_fetch_feed, name, url) for name, url in _DISCOVER_FEEDS]
        futs.append(pool.submit(_scout_web))
        for fut in concurrent.futures.as_completed(futs, timeout=22):
            try:
                collected.extend(fut.result() or [])
            except Exception:
                continue

    # de-dupe + keep crypto-relevant only
    seen = set()
    uniq: List[Dict[str, Any]] = []
    for it in collected:
        url = (it.get("url") or "").split("#", 1)[0].rstrip("/")
        if not url.startswith("http"):
            continue
        key = url.lower()
        if key in seen:
            continue
        src = it.get("source") or ""
        blob = f"{it.get('title') or ''} {it.get('snippet') or ''}"
        trusted = src in _TRUSTED_CRYPTO_SOURCES or it.get("kind") == "scout"
        if not trusted and not _CRYPTO_RELEVANCE.search(blob):
            continue
        # still require some crypto signal for generic-looking titles from mixed feeds
        if src not in _TRUSTED_CRYPTO_SOURCES and it.get("kind") != "scout":
            if not _CRYPTO_RELEVANCE.search(blob):
                continue
        seen.add(key)
        cat, tags = _categorize(it.get("title") or "", it.get("snippet") or "")
        it = dict(it)
        it["category"] = cat
        it["tags"] = tags
        it["score"] = _score_item(it, now)
        it["why"] = ""
        uniq.append(it)

    uniq.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    top = uniq[:28]

    agent = "howl-scout"
    ai_on = bool((os.environ.get("XAI_API_KEY") or "").strip())
    enriched = _xai_enrich(top) if ai_on else None
    if enriched:
        top = enriched
        agent = "howl-scout+grok"

    # stable ids for UI
    for i, it in enumerate(top):
        it["id"] = f"d{i}-" + str(abs(hash(it.get("url") or it.get("title") or i)) % 10**10)

    payload = {
        "engine": "Howl Discover",
        "agent": agent,
        "ai_enabled": ai_on and agent.endswith("grok"),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "count": len(top),
        "items": [
            {
                "id": it.get("id"),
                "title": it.get("title"),
                "url": it.get("url"),
                "snippet": it.get("snippet") or "",
                "source": it.get("source") or "",
                "category": it.get("category") or "crypto",
                "tags": it.get("tags") or [],
                "why": it.get("why") or "",
                "published": it.get("published") or "",
                "kind": it.get("kind") or "rss",
                "score": round(float(it.get("score") or 0), 2),
            }
            for it in top
        ],
        "note": (
            "Live radar of new crypto tech from open web + publisher feeds. "
            + (
                "Grok agent enriches cards when XAI_API_KEY is set."
                if ai_on
                else "Add XAI_API_KEY on the server for Grok agent blurbs."
            )
        ),
    }
    _DISCOVER_CACHE["ts"] = now
    _DISCOVER_CACHE["payload"] = payload
    return payload


# ---------------------------------------------------------------------------
# Howl Reader — in-app article view when sites block iframes (news, etc.)
# ---------------------------------------------------------------------------

# Domains that commonly refuse embedding — open via Reader by default
_FRAME_BLOCK_HOSTS = re.compile(
    r"(^|\.)("
    r"cointelegraph\.com|decrypt\.co|coindesk\.com|theblock\.co|cryptonews\.com|"
    r"bitcoinmagazine\.com|coinbureau\.com|reuters\.com|bloomberg\.com|wsj\.com|"
    r"nytimes\.com|ft\.com|medium\.com|substack\.com|mirror\.xyz|"
    r"twitter\.com|x\.com|youtube\.com|youtu\.be|facebook\.com|instagram\.com|"
    r"reddit\.com|linkedin\.com|github\.com|google\.com|binance\.com|"
    r"coinmarketcap\.com|coingecko\.com|tradingview\.com"
    r")$",
    re.I,
)

_ALLOWED_READER_TAGS = {
    "p",
    "br",
    "h1",
    "h2",
    "h3",
    "h4",
    "strong",
    "b",
    "em",
    "i",
    "ul",
    "ol",
    "li",
    "blockquote",
    "a",
    "img",
    "figure",
    "figcaption",
    "pre",
    "code",
    "span",
    "div",
    "section",
    "article",
    "hr",
}


def prefers_reader(url: str) -> bool:
    try:
        host = (urllib.parse.urlparse(url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return bool(_FRAME_BLOCK_HOSTS.search(host))
    except Exception:
        return False


def _sanitize_reader_html(raw: str) -> str:
    """Strip scripts/styles and non-content tags; keep a readable subset."""
    if not raw:
        return ""
    s = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", raw)
    s = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", s)
    s = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", s)
    s = re.sub(r"(?is)<iframe[^>]*>.*?</iframe>", " ", s)
    s = re.sub(r"(?is)<!--.*?-->", " ", s)
    # drop attributes except href/src/alt on allowed tags
    def repl_tag(m: re.Match) -> str:
        full = m.group(0)
        if full.startswith("</"):
            name = full[2:-1].strip().lower()
            return f"</{name}>" if name in _ALLOWED_READER_TAGS else ""
        name_m = re.match(r"<([a-zA-Z0-9]+)", full)
        if not name_m:
            return ""
        name = name_m.group(1).lower()
        if name not in _ALLOWED_READER_TAGS:
            return ""
        attrs = ""
        if name == "a":
            hm = re.search(r'\bhref\s*=\s*["\']([^"\']+)["\']', full, re.I)
            if hm:
                href = hm.group(1)
                if href.startswith(("http://", "https://", "/")):
                    attrs = f' href="{html_lib.escape(href, quote=True)}" target="_blank" rel="noopener noreferrer"'
        elif name == "img":
            sm = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', full, re.I)
            am = re.search(r'\balt\s*=\s*["\']([^"\']*)["\']', full, re.I)
            if sm and sm.group(1).startswith(("http://", "https://", "/")):
                alt = am.group(1) if am else ""
                attrs = (
                    f' src="{html_lib.escape(sm.group(1), quote=True)}"'
                    f' alt="{html_lib.escape(alt, quote=True)}" loading="lazy"'
                )
            else:
                return ""
        self_close = full.rstrip().endswith("/>") or name in ("br", "hr", "img")
        if self_close and name in ("br", "hr", "img"):
            return f"<{name}{attrs}/>"
        return f"<{name}{attrs}>"

    s = re.sub(r"</?[a-zA-Z][^>]*>", repl_tag, s)
    s = re.sub(r"\s{2,}", " ", s)
    # collapse empty tags noise lightly
    s = re.sub(r"(?i)<(p|div|span)>\s*</\1>", "", s)
    return s.strip()


def _extract_main_html(page: str) -> str:
    # Prefer semantic containers
    patterns = [
        r"(?is)<article\b[^>]*>(.*?)</article>",
        r'(?is)<main\b[^>]*>(.*?)</main>',
        r'(?is)<div[^>]+class="[^"]*(?:article-body|article__body|post-content|entry-content|story-body|content-body|rich-text)[^"]*"[^>]*>(.*?)</div>',
        r'(?is)<section[^>]+class="[^"]*(?:article|post-content|entry-content)[^"]*"[^>]*>(.*?)</section>',
    ]
    for pat in patterns:
        m = re.search(pat, page)
        if m and len(m.group(1)) > 200:
            return m.group(1)
    # fallback: body
    m = re.search(r"(?is)<body[^>]*>(.*?)</body>", page)
    return m.group(1) if m else page


def fetch_reader(url: str) -> Dict[str, Any]:
    """
    Fetch a URL server-side and return a sanitized reader document
    so news sites that block iframes still open inside Howl Search.
    """
    u = (url or "").strip()
    if not u.startswith("http"):
        raise ValueError("url must be http(s)")
    # safety: no local/private targets
    parsed = urllib.parse.urlparse(u)
    host = (parsed.hostname or "").lower()
    if host in ("localhost", "127.0.0.1", "0.0.0.0", "::1") or host.endswith(".local"):
        raise ValueError("blocked host")
    if re.match(r"^(10\.|192\.168\.|172\.(1[6-9]|2\d|3[0-1])\.)", host):
        raise ValueError("blocked host")

    raw = _http_get(
        u,
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=14,
    )
    # cap size
    page = raw[:1_500_000].decode("utf-8", errors="ignore")
    title_m = re.search(r"(?is)<title[^>]*>(.*?)</title>", page)
    title = _clean_html(title_m.group(1)) if title_m else ""
    if not title:
        og = re.search(
            r'(?is)<meta[^>]+property=["\']og:title["\'][^>]+content=["\']([^"\']+)["\']',
            page,
        ) or re.search(
            r'(?is)<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:title["\']',
            page,
        )
        title = _clean_html(og.group(1)) if og else (host or u)

    desc_m = re.search(
        r'(?is)<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']+)["\']',
        page,
    ) or re.search(
        r'(?is)<meta[^>]+property=["\']og:description["\'][^>]+content=["\']([^"\']+)["\']',
        page,
    )
    description = _clean_html(desc_m.group(1)) if desc_m else ""

    main = _extract_main_html(page)
    content = _sanitize_reader_html(main)
    # if still tiny, try description + paragraphs from page
    if len(_clean_html(content)) < 120:
        paras = re.findall(r"(?is)<p\b[^>]*>(.*?)</p>", page)
        joined = " ".join(f"<p>{_clean_html(p)}</p>" for p in paras[:40] if len(_clean_html(p)) > 40)
        content = _sanitize_reader_html(joined) or f"<p>{html_lib.escape(description or 'No extractable article text.')}</p>"

    text_len = len(_clean_html(content))
    return {
        "url": u,
        "title": title[:240],
        "description": description[:400],
        "content_html": content[:200_000],
        "text_len": text_len,
        "prefer_reader": True,
        "host": host,
        "engine": "Howl Reader",
    }

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

DEFAULT_PUBLIC = Path.home() / ".howlcoin"


def _chain_or_none(data_dir: Path) -> Optional[Blockchain]:
    data_dir = data_dir.expanduser()
    if not (data_dir / "chain.json").exists():
        return None
    try:
        return Blockchain(data_dir)
    except Exception:
        return None


class ExplorerHub:
    """Holds multiple named chains and reloads them from disk on demand."""

    def __init__(self, networks: Dict[str, Path]):
        self.paths = {k: Path(v).expanduser() for k, v in networks.items()}
        self._chains: Dict[str, Blockchain] = {}
        self.refresh_all()

    def refresh_all(self) -> None:
        for name, path in self.paths.items():
            c = _chain_or_none(path)
            if c:
                self._chains[name] = c
            elif name in self._chains:
                # keep stale if temporarily missing
                pass

    def refresh(self, name: str) -> Optional[Blockchain]:
        path = self.paths.get(name)
        if not path:
            return None
        if name in self._chains:
            try:
                self._chains[name].reload_from_disk()
                return self._chains[name]
            except Exception:
                pass
        c = _chain_or_none(path)
        if c:
            self._chains[name] = c
        return self._chains.get(name)

    def list_networks(self) -> List[Dict[str, Any]]:
        self.refresh_all()
        out = []
        for name, path in self.paths.items():
            c = self._chains.get(name)
            if c:
                try:
                    c.reload_from_disk()
                except Exception:
                    pass
                s = c.summary()
                tip_age = s.get("tip_age_seconds")
                # "live" = chain data present. Slow block times at high diff are normal.
                d_label = s.get("difficulty_label") or str(s.get("difficulty"))
                d_f = s.get("difficulty_float")
                expect_n = s.get("expected_hashes_next")
                out.append(
                    {
                        "id": name,
                        "label": name.replace("_", " ").title(),
                        "path": str(path),
                        "online": True,
                        "height": s["height"],
                        "tip": s["tip"],
                        "tip_timestamp": s.get("tip_timestamp"),
                        "tip_age_seconds": tip_age,
                        "circulating": s["circulating"],
                        "difficulty": s["difficulty"],
                        "difficulty_float": d_f,
                        "difficulty_label": d_label,
                        "next_difficulty": s.get("next_difficulty"),
                        "next_difficulty_label": s.get("next_difficulty_label"),
                        "expected_hashes_next": expect_n,
                        "protocol": s.get("protocol"),
                        "version": s.get("version"),
                        "smooth_diff_activation_height": s.get("smooth_diff_activation_height"),
                        "mempool": s["mempool"],
                        "status": "live",
                        "status_note": (
                            "Network online · last block "
                            + (
                                f"{int(tip_age // 3600)}h ago"
                                if tip_age is not None and tip_age >= 3600
                                else f"{int((tip_age or 0) // 60)}m ago"
                                if tip_age is not None
                                else "—"
                            )
                            + f" · diff {d_label}"
                            + (
                                " · v0.6 smooth difficulty"
                                if (s.get("height") or 0) + 1 >= (s.get("smooth_diff_activation_height") or 120)
                                else ""
                            )
                        ),
                    }
                )
            else:
                out.append(
                    {
                        "id": name,
                        "label": name.replace("_", " ").title(),
                        "path": str(path),
                        "online": False,
                        "height": None,
                        "tip": None,
                        "note": "No chain.json yet — mine or sync first",
                    }
                )
        return out

    def get(self, name: str) -> Optional[Blockchain]:
        return self.refresh(name)


EXPLORER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<meta name="theme-color" content="#0c0f14" id="themeColorMeta"/>
<title>Howlscan — Howlcoin Block Explorer</title>
<link rel="icon" href="/assets/howlcoin-logo-meme-pup-coin.jpg"/>
<script>
/* Apply theme before paint (site + wallet keys stay in sync) */
(function(){
  try{
    var t=localStorage.getItem('howlscan_theme_v1')||localStorage.getItem('howl_theme_v1')||'dark';
    if(['light','dark','neo','bones'].indexOf(t)<0) t='dark';
    document.documentElement.setAttribute('data-theme', t);
  }catch(e){}
})();
</script>
<link rel="stylesheet" href="/assets/howl-site-theme.css"/>
<style>
/* —— Themes (match wallet: Light · Dark · Neo · Bones) —— */
html[data-theme="dark"], :root{
  --bg:#0c0f14; --bg2:#12161e; --panel:#161b26; --panel2:#1a2130;
  --border:#252d3d; --text:#e8edf7; --muted:#8b95a8; --link:#4da3ff;
  --green:#3dff9a; --amber:#ffb020; --red:#ff6b7a; --chip:#222a3a;
  --row:#121722; --rowh:#1a2233;
  --top-bg:rgba(12,15,20,.94); --bottom-bg:rgba(12,15,20,.96);
  --btn-bg:linear-gradient(180deg,#2f6fed,#1f55c9); --btn-fg:#fff;
  --banner-bg:linear-gradient(135deg,rgba(61,255,154,.07),rgba(12,15,20,.95) 40%,rgba(77,163,255,.06));
  --banner-edge:rgba(12,15,20,.95);
  --active-bg:rgba(77,163,255,.15); --active-border:rgba(77,163,255,.45); --active-text:#9cc9ff;
  --ok-bg:rgba(61,255,154,.12); --warn-bg:rgba(255,176,32,.12); --blue-bg:rgba(77,163,255,.12);
  --primary-border:rgba(61,255,154,.4);
  --safe-b:env(safe-area-inset-bottom,0px);
  --safe-t:env(safe-area-inset-top,0px);
  --bottom-nav-h:64px;
  --fx:0;
}
html[data-theme="light"]{
  --bg:#f4f6fa; --bg2:#eef1f6; --panel:#ffffff; --panel2:#f0f3f8;
  --border:#d8dee8; --text:#0f1419; --muted:#5c667a; --link:#1565c0;
  --green:#0d9f6e; --amber:#d97706; --red:#dc2626; --chip:#e8ecf2;
  --row:#ffffff; --rowh:#eef2f8;
  --top-bg:rgba(255,255,255,.94); --bottom-bg:rgba(255,255,255,.96);
  --btn-bg:#1565c0; --btn-fg:#fff;
  --banner-bg:#eef6f2; --banner-edge:rgba(244,246,250,.95);
  --active-bg:rgba(21,101,192,.1); --active-border:rgba(21,101,192,.4); --active-text:#1565c0;
  --ok-bg:rgba(13,159,110,.12); --warn-bg:rgba(217,119,6,.12); --blue-bg:rgba(21,101,192,.1);
  --primary-border:rgba(13,159,110,.45);
  --fx:0;
}
html[data-theme="neo"]{
  --bg:#03010a; --bg2:#050214; --panel:rgba(12,8,32,.88); --panel2:rgba(8,6,24,.95);
  --border:rgba(0,255,200,.22); --text:#eef6ff; --muted:#8b9bbf; --link:#00e5ff;
  --green:#00ffc6; --amber:#ffc14d; --red:#ff4d6d; --chip:rgba(8,6,24,.9);
  --row:rgba(8,6,24,.75); --rowh:rgba(0,255,198,.08);
  --top-bg:rgba(3,1,12,.9); --bottom-bg:rgba(3,1,12,.96);
  --btn-bg:linear-gradient(135deg,#00ffc6,#00e5ff); --btn-fg:#03140f;
  --banner-bg:linear-gradient(135deg,rgba(0,255,198,.12),rgba(3,1,10,.95) 45%,rgba(192,132,252,.1));
  --banner-edge:rgba(3,1,12,.95);
  --active-bg:rgba(0,255,198,.12); --active-border:rgba(0,255,198,.5); --active-text:#00ffc6;
  --ok-bg:rgba(0,255,198,.12); --warn-bg:rgba(255,193,77,.12); --blue-bg:rgba(0,229,255,.12);
  --primary-border:rgba(0,255,198,.45);
  --fx:1;
}
html[data-theme="bones"]{
  --bg:#000; --bg2:#000; --panel:#000; --panel2:#0a0a0a;
  --border:#fff; --text:#fff; --muted:#a0a0a0; --link:#fff;
  --green:#fff; --amber:#fff; --red:#fff; --chip:#0a0a0a;
  --row:#000; --rowh:#111;
  --top-bg:#000; --bottom-bg:#000;
  --btn-bg:#fff; --btn-fg:#000;
  --banner-bg:#000; --banner-edge:#000;
  --active-bg:#fff; --active-border:#fff; --active-text:#000;
  --ok-bg:#111; --warn-bg:#111; --blue-bg:#111;
  --primary-border:#fff;
  --fx:0;
}
*{box-sizing:border-box}
*,*::before,*::after{border-radius:0!important} /* sharp edges */
html{-webkit-text-size-adjust:100%}
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);line-height:1.45;-webkit-tap-highlight-color:transparent}
/* Neo subtle void wash */
html[data-theme="neo"] body::before{
  content:"";position:fixed;inset:0;z-index:0;pointer-events:none;opacity:var(--fx);
  background:
    radial-gradient(900px 500px at 10% -10%,rgba(0,255,198,.1),transparent 55%),
    radial-gradient(700px 400px at 100% 0%,rgba(192,132,252,.12),transparent 50%),
    linear-gradient(180deg,#050214 0%,#03010a 50%,#02040f 100%);
}
html[data-theme="bones"] body::before{display:none!important}
body > *{position:relative;z-index:1}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.84rem;word-break:break-all}
.muted{color:var(--muted)}
.topbar{display:flex;align-items:center;gap:10px;padding:10px 14px;padding-top:calc(10px + var(--safe-t));
  border-bottom:1px solid var(--border);background:var(--top-bg);backdrop-filter:blur(12px);
  position:sticky;top:0;z-index:40}
.topbar img{width:36px;height:36px;border-radius:50%;object-fit:cover;border:2px solid color-mix(in srgb,var(--green) 45%,transparent);flex-shrink:0}
html[data-theme="bones"] .topbar img{filter:grayscale(1) contrast(1.1);border-color:#fff}
.brand{font-weight:750;letter-spacing:.02em;min-width:0;cursor:pointer}
.brand span{color:var(--green)}
.brand small{display:block;font-weight:500;color:var(--muted);font-size:.72rem;margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.nav{display:flex;gap:6px;flex-wrap:wrap;margin-left:4px}
.nav button,.chipbtn,button.chipbtn{border:1px solid var(--border);background:var(--chip);color:var(--text);
  border-radius:10px;padding:8px 12px;cursor:pointer;font:inherit;font-size:.85rem;font-weight:600;
  min-height:40px;touch-action:manipulation}
.nav button.active,.chipbtn.active{background:var(--active-bg);border-color:var(--active-border);color:var(--active-text)}
.nav button:hover,.chipbtn:hover{border-color:var(--green)}
.grow{flex:1}
.top-actions{display:flex;gap:6px;align-items:center;flex-wrap:wrap;justify-content:flex-end}
.theme-select{border:1px solid var(--border);background:var(--chip);color:var(--text);
  padding:8px 10px;font:inherit;font-size:.82rem;font-weight:700;min-height:40px;cursor:pointer;
  max-width:118px}
.theme-grid{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:0 0 12px}
.theme-pill{border:1px solid var(--border);background:var(--panel);color:var(--text);
  padding:10px 8px;font:inherit;font-weight:700;font-size:.78rem;cursor:pointer;text-align:center}
.theme-pill small{display:block;font-weight:500;color:var(--muted);font-size:.68rem;margin-top:3px}
.theme-pill.on{border-color:var(--green);color:var(--green);background:var(--active-bg)}
html[data-theme="bones"] .theme-pill.on{background:#fff;color:#000}
.iconbtn{border:1px solid var(--border);background:var(--chip);color:var(--text);border-radius:10px;
  width:42px;height:42px;padding:0;cursor:pointer;font:inherit;font-size:1.15rem;font-weight:700;
  display:none;align-items:center;justify-content:center;flex-shrink:0;touch-action:manipulation}
.iconbtn:active{background:var(--panel2)}
.hero{padding:22px 16px 8px;max-width:1200px;margin:0 auto}
.hero h2{margin:0 0 6px;font-size:1.45rem;font-weight:750}
.hero .muted{margin:0;font-size:.92rem}
.ascii-banner{margin:12px 0 10px;padding:14px 0;border-radius:12px;border:1px solid var(--border);
  background:var(--banner-bg);
  overflow:hidden;position:relative;display:flex;justify-content:center;align-items:center}
.ascii-banner::before,.ascii-banner::after{content:"";position:absolute;top:0;bottom:0;width:56px;z-index:2;pointer-events:none}
.ascii-banner::before{left:0;background:linear-gradient(90deg,var(--banner-edge),transparent)}
.ascii-banner::after{right:0;background:linear-gradient(270deg,var(--banner-edge),transparent)}
html[data-theme="bones"] .ascii-banner::before,
html[data-theme="bones"] .ascii-banner::after{display:none}
.ascii-track{flex:0 0 auto;will-change:transform;animation:howl-pan 20s linear infinite}
.ascii-track pre{margin:0;padding:0 24px;font-family:"SF Mono","Menlo","Consolas","DejaVu Sans Mono",ui-monospace,monospace;
  font-size:clamp(.58rem,1.05vw,.82rem);line-height:1.22;letter-spacing:.04em;color:var(--green);
  white-space:pre;text-shadow:0 0 20px color-mix(in srgb,var(--green) 18%,transparent)}
html[data-theme="bones"] .ascii-track pre{text-shadow:none}
html[data-theme="light"] .ascii-track pre{text-shadow:none}
@keyframes howl-pan{
  0%, 22%{transform:translateX(0)}
  48%{transform:translateX(calc(-55vw - 55%))}
  48.02%{transform:translateX(calc(55vw + 55%))}
  78%, 100%{transform:translateX(0)}
}
@media(prefers-reduced-motion:reduce){.ascii-track{animation:none;transform:none}}
.searchwrap{max-width:1200px;margin:0 auto;padding:8px 16px 14px}
.searchbox{display:flex;gap:8px;background:var(--panel);border:1px solid var(--border);border-radius:14px;padding:6px}
.searchbox input{flex:1;border:0;outline:0;background:transparent;color:var(--text);font:inherit;padding:12px 12px;min-width:0;font-size:16px}
.searchbox button{border:0;border-radius:10px;background:var(--btn-bg);color:var(--btn-fg);
  font-weight:700;padding:12px 16px;cursor:pointer;min-height:44px;flex-shrink:0;touch-action:manipulation}
.stats{max-width:1200px;margin:0 auto;padding:0 16px 14px;display:grid;
  grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px}
.stat{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:12px 12px 10px}
.stat .k{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}
.stat .v{font-size:1.15rem;font-weight:750;margin-top:5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stat .s{font-size:.74rem;color:var(--muted);margin-top:3px}
.main{max-width:1200px;margin:0 auto;padding:0 16px 40px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.card{background:var(--panel);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.card h3{margin:0;padding:13px 14px;font-size:.95rem;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center;gap:8px}
.card h3 .more{font-size:.8rem;font-weight:600;color:var(--link);white-space:nowrap}
.table-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;padding:10px 12px;color:var(--muted);font-size:.7rem;text-transform:uppercase;
  letter-spacing:.05em;border-bottom:1px solid var(--border);background:var(--panel2);white-space:nowrap}
td{padding:11px 12px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:0}
tbody tr{background:var(--row);cursor:pointer}
tbody tr:hover{background:var(--rowh)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.72rem;font-weight:700}
.badge.ok{background:var(--ok-bg);color:var(--green)}
.badge.warn{background:var(--warn-bg);color:var(--amber)}
.badge.blue{background:var(--blue-bg);color:var(--link)}
.detail{padding:14px}
.kv{display:grid;grid-template-columns:140px 1fr;gap:8px 12px;margin:10px 0}
.kv .k{color:var(--muted);font-size:.85rem}
.back{border:1px solid var(--border);background:var(--chip);color:var(--text);border-radius:10px;
  padding:10px 14px;cursor:pointer;font:inherit;font-weight:600;margin-bottom:10px;min-height:42px;touch-action:manipulation}
.footer{max-width:1200px;margin:0 auto;padding:10px 16px 28px;color:var(--muted);font-size:.8rem;
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.err{padding:24px;color:var(--amber)}
.amount{font-weight:700;color:var(--green)}
.neg{color:var(--red)}
.skeleton{opacity:.55}
.quick-row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
.desktop-only{display:block}
.mobile-only{display:none}
/* Card list (mobile-friendly) */
.mlist{display:flex;flex-direction:column}
.mrow{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;
  padding:12px 14px;border-bottom:1px solid var(--border);background:var(--row);cursor:pointer;
  text-align:left;min-height:56px}
.mrow:last-child{border-bottom:0}
.mrow:active{background:var(--rowh)}
.mrow .ml{min-width:0;flex:1}
.mrow .mr{text-align:right;flex-shrink:0}
.mrow .mt{font-weight:700;font-size:.95rem}
.mrow .ms{color:var(--muted);font-size:.78rem;margin-top:3px}
.mrow .ma{font-weight:700;color:var(--green);font-size:.92rem}
/* Drawer + bottom nav (mobile) */
.drawer-bg{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:50}
.drawer-bg.open{display:block}
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(86vw,320px);background:var(--bg2);
  border-left:1px solid var(--border);z-index:60;padding:calc(14px + var(--safe-t)) 14px calc(20px + var(--safe-b));
  transform:translateX(100%);transition:transform .22s ease;overflow-y:auto}
.drawer.open{transform:translateX(0)}
.drawer h4{margin:0 0 10px;font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
.drawer .ditem{display:flex;align-items:center;gap:10px;width:100%;text-align:left;border:1px solid var(--border);
  background:var(--panel);color:var(--text);border-radius:12px;padding:12px 14px;margin:0 0 8px;
  font:inherit;font-weight:600;font-size:.92rem;cursor:pointer;text-decoration:none;min-height:48px;touch-action:manipulation}
.drawer .ditem.primary{border-color:var(--primary-border);color:var(--green)}
.drawer .ditem:active{background:var(--panel2)}
.drawer .close-row{display:flex;justify-content:space-between;align-items:center;margin-bottom:14px}
.bottom-nav{display:none;position:fixed;left:0;right:0;bottom:0;z-index:35;
  background:var(--bottom-bg);backdrop-filter:blur(14px);border-top:1px solid var(--border);
  padding:6px 4px calc(6px + var(--safe-b));grid-template-columns:repeat(5,1fr);gap:0}
.bnav-item{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
  border:0;background:transparent;color:var(--muted);font:inherit;font-size:.62rem;font-weight:650;
  padding:6px 2px;cursor:pointer;min-height:52px;touch-action:manipulation;text-decoration:none}
.bnav-item .ico{font-size:1.15rem;line-height:1}
.bnav-item.active{color:var(--green)}
.bnav-item:active{opacity:.75}

/* —— Mobile —— */
@media(max-width:900px){
  .cols{grid-template-columns:1fr}
}
@media(max-width:760px){
  body{padding-bottom:calc(var(--bottom-nav-h) + var(--safe-b) + 8px)}
  .top-actions.desktop-nav{display:none}
  .nav{display:none}
  .iconbtn{display:inline-flex}
  .brand small{display:none}
  .topbar{gap:8px;padding:8px 12px;padding-top:calc(8px + var(--safe-t))}
  .topbar img{width:34px;height:34px}
  .hero{padding:12px 14px 4px}
  .hero h2{font-size:1.2rem}
  .hero .sub-desktop{display:none}
  .ascii-banner{display:none} /* cleaner mobile: drop ascii marquee */
  .searchwrap{padding:6px 12px 12px;position:sticky;top:52px;z-index:30;background:var(--bg)}
  .searchbox{padding:4px;border-radius:12px}
  .searchbox input{padding:11px 10px;font-size:16px}
  .searchbox button{padding:10px 14px}
  .stats{grid-template-columns:1fr 1fr;gap:8px;padding:0 12px 12px}
  .stat .s{display:none}
  .stat.stat-wide{grid-column:1 / -1}
  .stat .v{font-size:1.05rem}
  .main{padding:0 12px 24px}
  .desktop-only{display:none!important}
  .mobile-only{display:block}
  .card h3{padding:12px 12px}
  .detail{padding:12px}
  .kv{grid-template-columns:1fr;gap:2px 0}
  .kv .k{font-size:.72rem;text-transform:uppercase;letter-spacing:.04em;margin-top:8px}
  .kv > div:not(.k){padding-bottom:6px;border-bottom:1px solid rgba(37,45,61,.55)}
  .quick-row{display:none} /* bottom nav covers these */
  .footer{display:none}
  .bottom-nav{display:grid}
  .table-wrap{margin:0 -2px}
  th,td{padding:10px 10px}
  /* hide less-critical table cols on very small if any table still shown */
  .hide-sm{display:none!important}
  .page-actions{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px}
  .page-actions .back{margin-bottom:0}
}
@media(max-width:380px){
  .stats{grid-template-columns:1fr 1fr}
  .bnav-item{font-size:.58rem}
}
</style>
</head>
<body>
<div class="topbar">
  <img src="/assets/howlcoin-logo-meme-pup-coin.jpg" alt="HOWL" onclick="goHome()" style="cursor:pointer"/>
  <div class="brand" onclick="goHome()">Howl<span>scan</span><small>Howlcoin block explorer</small></div>
  <div class="nav" id="nav"></div>
  <div class="grow"></div>
  <div class="top-actions desktop-nav">
    <label class="muted" for="themeSelect" style="font-size:.72rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;margin:0 2px 0 0">Look</label>
    <select class="theme-select" id="themeSelect" title="Appearance" aria-label="Appearance theme" onchange="setTheme(this.value)">
      <option value="dark">Dark</option>
      <option value="light">Light</option>
      <option value="neo">Neo</option>
      <option value="bones">Bones</option>
    </select>
    <button class="chipbtn" onclick="location.hash='#/'+net+'/richlist'">Richlist</button>
    <button class="chipbtn" onclick="location.hash='#/'+net+'/mempool'">Mempool</button>
    <button class="chipbtn" onclick="location.hash='#/'+net+'/block/0'">Genesis</button>
    <a class="chipbtn" href="/whitepaper" style="text-decoration:none;display:inline-flex;align-items:center">White paper</a>
    <a class="chipbtn" href="/token" style="text-decoration:none;display:inline-flex;align-items:center;color:var(--text)">Token info</a>
    <a class="chipbtn" href="/wallet" style="text-decoration:none;display:inline-flex;align-items:center">Wallet</a>
    <button class="chipbtn" style="border-color:var(--primary-border);color:var(--green)" onclick="location.hash='#/run'">Run a node</button>
    <button class="chipbtn" onclick="location.hash='#/health'">Health</button>
    <button class="chipbtn" onclick="refreshData()">Refresh</button>
  </div>
  <button class="iconbtn" id="menu-btn" type="button" aria-label="Menu" onclick="toggleDrawer(true)">☰</button>
</div>

<div class="drawer-bg" id="drawer-bg" onclick="toggleDrawer(false)"></div>
<aside class="drawer" id="drawer" aria-hidden="true">
  <div class="close-row">
    <strong style="font-size:1rem">Menu</strong>
    <button class="iconbtn" type="button" style="display:inline-flex" aria-label="Close" onclick="toggleDrawer(false)">✕</button>
  </div>
  <h4>Explore</h4>
  <button class="ditem" type="button" onclick="navTo('#/'+net)">🏠 Home</button>
  <button class="ditem" type="button" onclick="navTo('#/'+net+'/richlist')">🏆 Richlist</button>
  <button class="ditem" type="button" onclick="navTo('#/'+net+'/mempool')">⏳ Mempool</button>
  <button class="ditem" type="button" onclick="navTo('#/'+net+'/block/0')">🌱 Genesis</button>
  <h4 style="margin-top:16px">Get started</h4>
  <button class="ditem primary" type="button" onclick="navTo('#/run')">🐺 Run a node</button>
  <button class="ditem" type="button" onclick="navTo('#/health')">💓 Network health</button>
  <a class="ditem" href="/wallet">👛 Wallet</a>
  <a class="ditem" href="/token">🏷 Token / contract info</a>
  <a class="ditem" href="/whitepaper">📄 White paper</a>
  <a class="ditem" href="https://github.com/happyoils710/howlcoin" target="_blank" rel="noopener">⌥ GitHub</a>
  <h4 style="margin-top:16px">Appearance</h4>
  <div class="theme-grid" id="themeGrid">
    <button type="button" class="theme-pill" data-theme="dark" onclick="setTheme('dark')">Dark<small>Default</small></button>
    <button type="button" class="theme-pill" data-theme="light" onclick="setTheme('light')">Light<small>Daylight</small></button>
    <button type="button" class="theme-pill" data-theme="neo" onclick="setTheme('neo')">Neo<small>Cyber glow</small></button>
    <button type="button" class="theme-pill" data-theme="bones" onclick="setTheme('bones')">Bones<small>B&amp;W · no gradient</small></button>
  </div>
  <h4 style="margin-top:8px">Network</h4>
  <div id="drawer-nav"></div>
  <button class="ditem" type="button" onclick="toggleDrawer(false);refreshData()">↻ Refresh data</button>
</aside>

<div class="hero" id="hero-static">
  <div class="ascii-banner" aria-hidden="true" id="howl-banner-host"></div>
  <h2>Blockchain explorer for <span style="color:var(--green)">Howlcoin</span></h2>
  <p class="muted sub-desktop">Search blocks, transactions, and addresses across the public network</p>
  <p class="muted mobile-only" style="margin:0;font-size:.88rem">Search height, hash, tx, or address</p>
</div>
<div class="searchwrap" id="searchwrap">
  <div class="searchbox">
    <input id="q" placeholder="Height, hash, txid, or H… address" enterkeyhint="search" autocomplete="off"
      onkeydown="if(event.key==='Enter')doSearch()"/>
    <button type="button" onclick="doSearch()">Search</button>
  </div>
</div>
<div id="app"></div>
<div class="footer">
  <div>Howlscan · Scrypt PoW · not financial advice ·
    <a href="#/public">Home</a> ·
    <a href="/token">Token info</a> ·
    <a href="/whitepaper">White paper</a> ·
    <a href="/wallet">Wallet</a> ·
    <a href="#/run">Run a node</a> ·
    <a href="#/public/richlist">Richlist</a> ·
    <a href="#/public/mempool">Mempool</a> ·
    <a href="#/public/block/0">Genesis</a>
  </div>
  <div>API <span class="mono">/api/networks</span> · seed <span class="mono">147.182.223.204:42069</span> ·
    <a href="https://github.com/happyoils710/howlcoin" target="_blank" rel="noopener">Code</a>
  </div>
</div>
<nav class="bottom-nav" id="bottom-nav" aria-label="Primary">
  <button type="button" class="bnav-item" data-tab="home" onclick="goHome()"><span class="ico">⌂</span>Home</button>
  <button type="button" class="bnav-item" data-tab="search" onclick="focusSearch()"><span class="ico">⌕</span>Search</button>
  <button type="button" class="bnav-item" data-tab="richlist" onclick="location.hash='#/'+net+'/richlist'"><span class="ico">★</span>Richlist</button>
  <button type="button" class="bnav-item" data-tab="mempool" onclick="location.hash='#/'+net+'/mempool'"><span class="ico">◎</span>Mempool</button>
  <button type="button" class="bnav-item" data-tab="more" onclick="toggleDrawer(true)"><span class="ico">☰</span>More</button>
</nav>
<script>
let net='public', networks=[];
const SEED = '147.182.223.204:42069';
const REPO = 'https://github.com/happyoils710/howlcoin';
const LS_THEME = 'howlscan_theme_v1';
const LS_THEME_WALLET = 'howl_theme_v1';
const THEMES = ['light','dark','neo','bones'];
const $ = s => document.querySelector(s);
const app = () => $('#app');

function setTheme(name){
  const t = THEMES.includes(name) ? name : 'dark';
  document.documentElement.setAttribute('data-theme', t);
  // Persist on both keys so /app wallet and every Howlscan page stay aligned
  try{
    localStorage.setItem(LS_THEME, t);
    localStorage.setItem(LS_THEME_WALLET, t);
  }catch(e){}
  const meta = document.getElementById('themeColorMeta');
  if(meta){
    meta.content = t==='light' ? '#f4f6fa'
      : t==='neo' ? '#03010a'
      : t==='bones' ? '#000000'
      : '#0c0f14';
  }
  const sel = document.getElementById('themeSelect');
  if(sel && sel.value !== t) sel.value = t;
  document.querySelectorAll('.theme-pill, .howl-theme-pill').forEach(p=>{
    p.classList.toggle('on', p.getAttribute('data-theme') === t);
  });
}
function readStoredTheme(){
  try{
    const a = localStorage.getItem(LS_THEME);
    const b = localStorage.getItem(LS_THEME_WALLET);
    if(THEMES.includes(a)) return a;
    if(THEMES.includes(b)) return b;
  }catch(e){}
  return 'dark';
}
function applyStoredTheme(){
  setTheme(readStoredTheme());
}
applyStoredTheme();
// Keep theme through hash routes, back/forward, and tab focus
window.addEventListener('hashchange', applyStoredTheme);
window.addEventListener('pageshow', applyStoredTheme);
document.addEventListener('visibilitychange', ()=>{
  if(document.visibilityState === 'visible') applyStoredTheme();
});
async function api(p){const r=await fetch(p); const j=await r.json(); if(!r.ok) throw new Error(j.error||r.statusText); return j}
function short(h,n=12){if(!h)return '—'; h=String(h); return h.length<=n?h:h.slice(0,n)+'…'}
function fmtAmt(a){if(a==null||a==='')return '—'; const n=Number(a)/1e8; return n.toLocaleString(undefined,{maximumFractionDigits:8})+' HOWL'}
function fmtCompact(n){
  // short number for stat boxes: 40499998 → 40.5M
  const x=Number(n);
  if(!isFinite(x)) return '—';
  const abs=Math.abs(x);
  const sign=x<0?'-':'';
  if(abs>=1e12) return sign+(abs/1e12).toFixed(2).replace(/\.?0+$/,'')+'T';
  if(abs>=1e9) return sign+(abs/1e9).toFixed(2).replace(/\.?0+$/,'')+'B';
  if(abs>=1e6) return sign+(abs/1e6).toFixed(2).replace(/\.?0+$/,'')+'M';
  if(abs>=1e3) return sign+(abs/1e3).toFixed(1).replace(/\.0$/,'')+'K';
  if(abs>=1) return sign+abs.toFixed(abs>=100?0:2).replace(/\.?0+$/,'');
  return sign+abs.toFixed(4).replace(/\.?0+$/,'');
}
function circulatingShort(s){
  // s.circulating is like "40499998.00000000 HOWL" or use howlies
  if(s.circulating_howlies!=null) return fmtCompact(Number(s.circulating_howlies)/1e8);
  const raw=String(s.circulating||'').replace(/ HOWL/i,'').replace(/,/g,'').trim();
  return fmtCompact(raw);
}
function fmtTime(ts){if(!ts)return '—'; try{return new Date(ts*1000).toLocaleString()}catch(e){return '—'}}

function ago(ts){if(!ts)return ''; const s=Math.max(0,Math.floor(Date.now()/1000-ts));
  if(s<60)return s+'s ago'; if(s<3600)return Math.floor(s/60)+'m ago'; if(s<86400)return Math.floor(s/3600)+'h ago'; return Math.floor(s/86400)+'d ago'}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function copyText(text, btn){
  navigator.clipboard.writeText(text).then(()=>{
    if(btn){ const t=btn.textContent; btn.textContent='Copied!'; setTimeout(()=>btn.textContent=t,1200); }
  }).catch(()=>{ prompt('Copy this:', text); });
}
function copyBtn(text){
  const safe = String(text).replace(/\\/g,'\\\\').replace(/'/g,"\\'");
  return `<button class="chipbtn" style="padding:2px 8px;font-size:.72rem;margin-left:6px" onclick="event.stopPropagation();copyText('${safe}', this)">Copy</button>`;
}
function cmdBox(title, cmd){
  return `<div style="margin:12px 0;padding:12px 14px;background:var(--bg);border:1px solid var(--border);border-radius:10px">
    <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;margin-bottom:8px">
      <b style="font-size:.9rem">${esc(title)}</b>
      <button class="chipbtn" onclick='copyText(${JSON.stringify(cmd)}, this)'>Copy</button>
    </div>
    <pre class="mono" style="margin:0;white-space:pre-wrap;color:var(--green);font-size:.82rem">${esc(cmd)}</pre>
  </div>`;
}
function crumbs(parts){
  // parts: [{label, href?}]
  return `<div class="muted" style="margin:0 0 12px;font-size:.85rem">${parts.map((p,i)=>
    p.href?`<a href="${p.href}">${esc(p.label)}</a>`:esc(p.label)
  ).join(' <span style="opacity:.5">/</span> ')}</div>`;
}
function linkBlock(h){return `<a href="#/${net}/block/${encodeURIComponent(h)}">${esc(h)}</a>`}
function linkTx(t){if(!t)return '—'; return `<a class="mono" href="#/${net}/tx/${encodeURIComponent(t)}">${esc(short(t,14))}</a>`}
function linkAddr(a){if(!a||a==='HOWL_GENESIS_BURN') return `<span class="mono">${esc(a||'—')}</span>`;
  return `<a class="mono" href="#/${net}/address/${encodeURIComponent(a)}">${esc(short(a,12))}</a>`}

function renderNav(){
  const html = networks.map(n=>`
    <button class="${n.id===net?'active':''}" onclick="switchNet('${n.id}')">
      ${esc(n.label)} ${n.online?`<span class="badge blue">#${n.height}</span>`:'<span class="badge warn">off</span>'}
    </button>`).join('');
  const nav = $('#nav');
  if(nav) nav.innerHTML = html;
  const dnav = $('#drawer-nav');
  if(dnav){
    dnav.innerHTML = networks.map(n=>`
      <button class="ditem" type="button" onclick="switchNet('${n.id}');toggleDrawer(false)">
        ${esc(n.label)} ${n.online?`· #${n.height}`:'· offline'}
      </button>`).join('') || '<div class="muted" style="padding:8px">No networks</div>';
  }
}
function switchNet(id){net=id; location.hash=`#/${net}`}
function goHome(){ location.hash = '#/' + net; }
function toggleDrawer(open){
  const d = $('#drawer'), bg = $('#drawer-bg');
  if(!d || !bg) return;
  const on = open === true ? true : open === false ? false : !d.classList.contains('open');
  d.classList.toggle('open', on);
  bg.classList.toggle('open', on);
  d.setAttribute('aria-hidden', on ? 'false' : 'true');
  document.body.style.overflow = on ? 'hidden' : '';
}
function navTo(hash){ toggleDrawer(false); location.hash = hash; }
function focusSearch(){
  const sw = $('#searchwrap');
  const h = $('#hero-static');
  if(h) h.style.display = '';
  if(sw){ sw.style.display = ''; sw.scrollIntoView({behavior:'smooth', block:'start'}); }
  const q = $('#q');
  if(q){ q.focus(); try{ q.select(); }catch(e){} }
}
function setBottomTab(tab){
  document.querySelectorAll('.bnav-item').forEach(el=>{
    el.classList.toggle('active', el.dataset.tab === tab);
  });
}
function activeTabFromRoute(parts){
  if(!parts.length || (parts.length===1 && networks.find(n=>n.id===parts[0]))) return 'home';
  if(parts[0]==='run' || parts[0]==='node' || parts[0]==='sync') return 'more';
  if(parts[1]==='richlist' || parts[0]==='richlist') return 'richlist';
  if(parts[1]==='mempool' || parts[0]==='mempool') return 'mempool';
  return 'home';
}

async function loadNetworks(){
  const d=await api('/api/networks');
  networks=d.networks||[];
  if(!networks.find(n=>n.id===net)) net=(networks[0]&&networks[0].id)||'public';
  renderNav();
}

function ensureBanner(){
  // Mount banner once so data refresh never restarts the animation
  const host = document.getElementById('howl-banner-host');
  if(!host || host.dataset.ready==='1') return;
  const art = [
    '      __      __                                                    ',
    '     /  \\____/  \\      _   _                 _           _         ',
    '    |   ◕    ◕   |    | | | | _____      __ | | ___ ___ (_)_ __    ',
    '     \\    ▽     /     | |_| |/ _ \\ \\ /\\ / / | |/ __/ _ \\| | \'_ \\   ',
    '      \\________/      |  _  | (_) \\ V  V /  | | (_| (_) | | | | |  ',
    '      /|      |\\      |_| |_|\\___/ \\_/\\_/   |_|\\___/\\___/|_|_| |_|  ',
    '     (_|  ▬▬  |_)            Scrypt · HOWL · awoo                   ',
  ].join('\n');
  host.innerHTML = `<div class="ascii-track"><pre>${art}</pre></div>`;
  host.dataset.ready = '1';
}
function setHeroVisible(show){
  const h = document.getElementById('hero-static');
  const s = document.querySelector('.searchwrap');
  if(h) h.style.display = show ? '' : 'none';
  if(s) s.style.display = show ? '' : 'none';
}

async function loadHome(){
  ensureBanner();
  setHeroVisible(true);
  setBottomTab('home');
  await loadNetworks();
  const s=await api(`/api/${net}/summary`);
  if(!s.online){
    app().innerHTML=`<div class="main"><div class="card detail err">Chain <b>${esc(net)}</b> offline.<br><span class="mono">${esc(s.path||'')}</span><br>${esc(s.note||'')}</div></div>`;
    return;
  }
  const [blocks, txs]=await Promise.all([
    api(`/api/${net}/blocks?limit=15`),
    api(`/api/${net}/txs?limit=15`),
  ]);
  const bl = blocks.blocks||[];
  const tl = txs.transactions||[];
  const tipTs = s.tip_timestamp || (bl[0] && bl[0].timestamp) || 0;
  const tipAge = tipTs ? ago(tipTs) : '—';
  const ageSec = s.tip_age_seconds != null ? s.tip_age_seconds : (tipTs ? Math.max(0, Math.floor(Date.now()/1000 - tipTs)) : 0);
  const dLabel = s.difficulty_label || String(s.difficulty ?? '—');
  const dFloat = s.difficulty_float != null ? Number(s.difficulty_float) : null;
  const dShow = dFloat != null && dFloat >= 1 ? dFloat.toFixed(3) : String(s.difficulty ?? '—');
  const expectN = s.expected_hashes_next;
  const expectTxt = expectN != null ? (expectN >= 1e6 ? (expectN/1e6).toFixed(2)+'M' : expectN >= 1e3 ? (expectN/1e3).toFixed(1)+'k' : String(Math.round(expectN))) : '—';
  const smoothOn = (s.height||0) + 1 >= (s.smooth_diff_activation_height||120);
  // High difficulty → slow blocks; do not imply the network is down
  const slowMining = (dFloat != null ? dFloat >= 5 : (s.difficulty||0) >= 5) || ageSec > 600;
  const liveNote = slowMining
    ? `Seed online · last block ${tipAge} · diff ${dLabel} — CPU mining can take a while. Chain is live.`
    : `Seed online · last block ${tipAge} · network live`;
  // Only replace #app — hero/banner stay mounted so animation never restarts
  app().innerHTML = `
  <div class="main" style="padding-top:4px;padding-bottom:4px">
    <div class="card" style="padding:12px 14px;border-color:rgba(61,255,154,.35);background:rgba(61,255,154,.06)">
      <div style="display:flex;flex-wrap:wrap;align-items:center;gap:8px">
        <span class="badge ok">LIVE</span>
        <span style="font-weight:700;color:var(--green)">Howlcoin public network</span>
        <span class="badge" style="background:rgba(192,132,252,.15);color:#c084fc;border:1px solid rgba(192,132,252,.35)">v0.6 smooth diff</span>
        <span class="muted" style="font-size:.88rem">${esc(liveNote)}</span>
      </div>
      <p class="muted" style="margin:8px 0 0;font-size:.82rem;line-height:1.4">
        Seed <span class="mono">147.182.223.204:42069</span> · height <b>${s.height}</b>
        ${smoothOn
          ? ` · <b>smooth difficulty</b> active from height ${s.smooth_diff_activation_height||120} (continuous work target + 2h stall relief).`
          : ` · smooth difficulty activates at height <b>${s.smooth_diff_activation_height||120}</b> (next blocks).`}
        Nodes must run <b>v0.6+</b>.
        <a href="#/run">Run a node / mine</a>
        · <a href="#/health">Health</a>
      </p>
      <div id="tipTicker" class="mono" style="margin-top:10px;padding:8px 10px;border:1px solid rgba(0,255,198,.2);background:rgba(0,0,0,.25);font-size:.78rem;overflow:hidden;white-space:nowrap;cursor:pointer"
        onclick="location.hash='#/${net}/block/${s.height}'"
        title="Tap for tip block">
        <span style="color:var(--green)">● tip</span>
        <span class="muted"> #${s.height}</span>
        · <span id="tipTickerHash">${esc(short(s.tip,18))}</span>
        <span class="muted"> · ${esc(tipAge)}</span>
      </div>
    </div>
  </div>
  <div class="stats">
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/block/${s.height}'">
      <div class="k">Height</div><div class="v">${s.height}</div><div class="s">last ${esc(tipAge)}</div></div>
    <div class="stat" title="${esc(dLabel)}"><div class="k">Difficulty</div><div class="v">${esc(dShow)}</div><div class="s">${smoothOn?'smooth work':'Scrypt PoW'} · ~${esc(expectTxt)} H</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/richlist'" title="${esc(String(s.circulating||''))}">
      <div class="k">Circulating</div><div class="v">${esc(circulatingShort(s))}</div><div class="s">HOWL · richlist</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/mempool'">
      <div class="k">Mempool</div><div class="v">${s.mempool}</div><div class="s">pending txs</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/richlist'">
      <div class="k">Addresses</div><div class="v">${s.addresses??'—'}</div><div class="s">richlist</div></div>
    <div class="stat stat-wide" style="cursor:pointer" onclick="location.hash='#/${net}/block/${encodeURIComponent(s.tip)}'">
      <div class="k">Tip hash</div><div class="v mono" style="font-size:.85rem">${esc(short(s.tip,14))}</div><div class="s">tap → tip block</div></div>
  </div>
  <div class="main" style="padding-bottom:8px">
    <div class="quick-row">
      <button class="chipbtn" onclick="location.hash='#/${net}/block/0'">Genesis #0</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/block/${s.height}'">Latest #${s.height}</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/richlist'">Top addresses</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/mempool'">Mempool (${s.mempool})</button>
      <button class="chipbtn" style="border-color:rgba(61,255,154,.45);color:var(--green)" onclick="location.hash='#/run'">Run a node / sync</button>
    </div>
  </div>
  <div class="main cols">
    <div class="card">
      <h3>Latest blocks <a class="more" href="#/${net}/block/${s.height}">tip →</a></h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Height</th><th>Hash</th><th>Txs</th><th>Miner</th><th>Reward</th><th>Time</th></tr></thead>
        <tbody>
          ${bl.map(b=>`<tr onclick="location.hash='#/${net}/block/${b.height}'">
            <td><b>${linkBlock(b.height)}</b></td>
            <td class="mono">${esc(short(b.hash,12))}</td>
            <td>${b.tx_count}</td>
            <td onclick="event.stopPropagation()">${linkAddr(b.miner)}</td>
            <td class="amount">${fmtAmt(b.reward)}</td>
            <td class="muted" title="${esc(fmtTime(b.timestamp))}">${ago(b.timestamp)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${bl.map(b=>`<div class="mrow" onclick="location.hash='#/${net}/block/${b.height}'">
          <div class="ml">
            <div class="mt">Block #${b.height}</div>
            <div class="ms mono">${esc(short(b.hash,10))} · ${b.tx_count} tx · ${ago(b.timestamp)}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(b.reward)}</div><div class="ms">reward</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No blocks</div></div>'}
      </div>
    </div>
    <div class="card">
      <h3>Latest transactions <a class="more" href="#/${net}/mempool">mempool →</a></h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>Flow</th><th>Amount</th><th>Status</th></tr></thead>
        <tbody>
          ${tl.map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${linkTx(t.txid)}</td>
            <td><span class="badge ${t.type==='coinbase'?'ok':'blue'}">${t.type==='coinbase'?'reward':'transfer'}</span></td>
            <td class="mono" onclick="event.stopPropagation()">${t.type==='coinbase'?'new coins → '+linkAddr(t.to):linkAddr(t.from)+' → '+linkAddr(t.to)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${t.confirmed?`<span class="badge ok" onclick="event.stopPropagation();location.hash='#/${net}/block/${t.block_height}'">#${t.block_height}</span>`:`<span class="badge warn">mempool</span>`}</td>
          </tr>`).join('') || '<tr><td colspan="5" class="muted" style="padding:16px">No transactions yet</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${tl.map(t=>`<div class="mrow" onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
          <div class="ml">
            <div class="mt">${t.type==='coinbase'?'Mining reward':'Transfer'} <span class="badge ${t.confirmed?'ok':'warn'}" style="margin-left:4px">${t.confirmed?'#'+t.block_height:'pool'}</span></div>
            <div class="ms mono">${t.type==='coinbase'?'→ '+esc(short(t.to,10)):esc(short(t.from,8))+' → '+esc(short(t.to,8))}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No transactions yet</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showBlock(id){
  setHeroVisible(false);
  setBottomTab('home');
  await loadNetworks();
  const d=await api(`/api/${net}/block/${encodeURIComponent(id)}`);
  const b=d.block; const txs=b.transactions||[];
  const cb=txs.find(t=>t.type==='coinbase');
  const h=b.height;
  const prev = h>0 ? h-1 : null;
  const fees = txs.filter(t=>t.type!=='coinbase').reduce((s,t)=>s+(Number(t.fee)||0),0);
  const xferN = txs.filter(t=>t.type!=='coinbase').length;
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Block #'+h}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      ${prev!=null?`<button class="chipbtn" onclick="location.hash='#/${net}/block/${prev}'">← #${prev}</button>`:''}
      <button class="chipbtn" onclick="location.hash='#/${net}/block/${h+1}'">#${h+1} →</button>
      <button class="chipbtn" onclick="copyText(${JSON.stringify(String(b.hash||''))}, this)">Copy hash</button>
    </div>
    <div class="stats" style="margin-bottom:10px">
      <div class="stat"><div class="k">Height</div><div class="v">#${b.height}</div><div class="s">${esc(ago(b.header.timestamp))}</div></div>
      <div class="stat"><div class="k">Mined</div><div class="v" style="font-size:.9rem">${esc(fmtTime(b.header.timestamp))}</div><div class="s">block time</div></div>
      <div class="stat"><div class="k">Txs</div><div class="v">${txs.length}</div><div class="s">${xferN} transfer · fees ${fmtAmt(fees)}</div></div>
      <div class="stat"><div class="k">Reward</div><div class="v" style="font-size:.95rem">${fmtAmt(cb&&cb.amount)}</div><div class="s">miner ${cb&&cb.to?esc(short(cb.to,10)):'—'}</div></div>
    </div>
    <div class="card detail">
      <div class="badge blue">Block</div>
      <span class="badge ok" style="margin-left:6px">Verified on Howlcoin</span>
      <h2 style="margin:8px 0 4px;font-size:1.25rem">Block #${b.height}</h2>
      <div class="mono">${esc(b.hash)}${copyBtn(b.hash)}</div>
      <div class="kv" style="margin-top:12px">
        <div class="k">Height</div><div>${b.height}</div>
        <div class="k">Timestamp</div><div>${esc(fmtTime(b.header.timestamp))} <span class="muted">(${ago(b.header.timestamp)})</span></div>
        <div class="k">Difficulty</div><div>${b.header.difficulty}</div>
        <div class="k">Nonce</div><div class="mono">${b.header.nonce}</div>
        <div class="k">Merkle root</div><div class="mono">${esc(b.header.merkle_root||'—')}</div>
        <div class="k">Previous</div><div class="mono">${b.height>0?linkBlock(b.header.prev_hash)+copyBtn(b.header.prev_hash):'— genesis'}</div>
        <div class="k">Miner</div><div>${linkAddr(cb&&cb.to)}${cb&&cb.to?copyBtn(cb.to):''}</div>
        <div class="k">Reward</div><div class="amount">${fmtAmt(cb&&cb.amount)}</div>
        <div class="k">Fees in block</div><div>${fmtAmt(fees)}</div>
        <div class="k">Transactions</div><div>${txs.length}</div>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>Transactions</h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>Flow</th><th>Amount</th></tr></thead>
        <tbody>
          ${txs.map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${t.txid?linkTx(t.txid):'—'}</td>
            <td><span class="badge ${t.type==='coinbase'?'ok':'blue'}">${t.type==='coinbase'?'mining reward':'transfer'}</span></td>
            <td class="mono" onclick="event.stopPropagation()">${t.type==='coinbase'?'new coins → '+linkAddr(t.to):linkAddr(t.from)+' → '+linkAddr(t.to)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${txs.map(t=>`<div class="mrow" onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
          <div class="ml">
            <div class="mt">${t.type==='coinbase'?'Mining reward':'Transfer'}</div>
            <div class="ms mono">${t.type==='coinbase'?'→ '+esc(short(t.to,12)):esc(short(t.from,8))+' → '+esc(short(t.to,8))}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No txs</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showTx(id){
  setHeroVisible(false);
  setBottomTab('home');
  await loadNetworks();
  const d=await api(`/api/${net}/tx/${encodeURIComponent(id)}`);
  const t=d.tx;
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Transaction'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      ${d.confirmed?`<button class="chipbtn" onclick="location.hash='#/${net}/block/${d.block_height}'">Block #${d.block_height}</button>`:
        `<button class="chipbtn" onclick="location.hash='#/${net}/mempool'">Mempool</button>`}
    </div>
    <div class="card detail" style="margin-top:4px">
      <div class="badge ${d.confirmed?'ok':'warn'}">${d.confirmed?'Confirmed':'Mempool'}</div>
      <h2 style="margin:8px 0 4px;font-size:1.25rem">Transaction</h2>
      <div class="mono">${esc(t.txid||id)}${copyBtn(t.txid||id)}</div>
      <div class="kv" style="margin-top:12px">
        <div class="k">Status</div><div>${d.confirmed?('Block '+linkBlock(d.block_height)):'Unconfirmed'}</div>
        <div class="k">Type</div><div>${esc(t.type||'transfer')}</div>
        ${t.type==='coinbase'?`
          <div class="k">Source</div><div>Mining reward (no sender — new HOWL created)</div>
          <div class="k">Miner (to)</div><div>${linkAddr(t.to)}</div>
          <div class="k">Reward</div><div class="amount">${fmtAmt(t.amount)}</div>
        `:`
          <div class="k">From</div><div>${linkAddr(t.from)}</div>
          <div class="k">To</div><div>${linkAddr(t.to)}</div>
          <div class="k">Amount</div><div class="amount">${fmtAmt(t.amount)}</div>
          <div class="k">Fee</div><div>${fmtAmt(t.fee||0)}</div>
          <div class="k">Nonce</div><div>${t.nonce??'—'}</div>
          <div class="k">Memo</div><div>${esc(t.memo||'—')}</div>
        `}
      </div>
    </div>
  </div>`;
}

async function showAddr(addr){
  setHeroVisible(false);
  setBottomTab('richlist');
  await loadNetworks();
  const d=await api(`/api/${net}/address/${encodeURIComponent(addr)}`);
  const hist = d.transactions||[];
  const known = {
    'HOWL_GENESIS_BURN': 'Genesis burn',
  };
  const tag = known[d.address] || (String(d.address||'').startsWith('H') ? 'Howlcoin address' : 'Address');
  // mini activity strip (heights)
  const heights = hist.map(t=>t.block_height).filter(h=>h!=null).slice(0,24);
  const maxH = heights.length ? Math.max(...heights) : 1;
  const spark = heights.length
    ? `<div style="display:flex;align-items:flex-end;gap:3px;height:36px;margin:10px 0 4px">${heights.map(h=>{
        const pct = Math.max(8, Math.round(100 * (Number(h)||0) / maxH));
        return `<div title="#${h}" style="flex:1;min-width:4px;height:${pct}%;background:var(--green);opacity:.75"></div>`;
      }).join('')}</div><div class="muted" style="font-size:.72rem">Recent activity heights (newest left)</div>`
    : `<p class="muted" style="font-size:.85rem;margin:8px 0">No activity spark yet</p>`;
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Richlist',href:'#/'+net+'/richlist'},{label:'Address'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/richlist'">Richlist</button>
      <button class="chipbtn" onclick="copyText(${JSON.stringify(String(d.address||''))}, this)">Copy address</button>
    </div>
    <div class="stats" style="margin-bottom:10px">
      <div class="stat"><div class="k">Balance</div><div class="v" style="font-size:1rem">${esc(d.balance_fmt)}</div><div class="s">HOWL</div></div>
      <div class="stat"><div class="k">Nonce</div><div class="v">${d.nonce}</div><div class="s">next send</div></div>
      <div class="stat"><div class="k">Txs shown</div><div class="v">${d.tx_count}</div><div class="s">history</div></div>
    </div>
    <div class="card detail" style="margin-top:4px">
      <div class="badge blue">${esc(tag)}</div>
      <span class="badge ok" style="margin-left:6px">Verified on Howlcoin</span>
      <h2 style="margin:8px 0 4px;font-size:1.25rem">Wallet</h2>
      <div class="mono">${esc(d.address)}${copyBtn(d.address)}</div>
      ${spark}
      <div class="kv" style="margin-top:12px">
        <div class="k">Balance</div><div class="amount" style="font-size:1.25rem">${esc(d.balance_fmt)}</div>
        <div class="k">Nonce</div><div>${d.nonce}</div>
        <div class="k">Shown txs</div><div>${d.tx_count}</div>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>History</h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Dir</th><th>Txid</th><th>Amount</th><th>Block</th></tr></thead>
        <tbody>
          ${hist.map(t=>`<tr>
            <td><span class="badge ${t.direction==='in'||t.type==='coinbase'?'ok':'warn'}">${esc(t.direction||t.type)}</span></td>
            <td>${t.txid?linkTx(t.txid):'—'}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${t.block_height!=null?linkBlock(t.block_height):'—'}</td>
          </tr>`).join('')||'<tr><td colspan="4" class="muted" style="padding:16px">No transactions</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${hist.map(t=>`<div class="mrow" onclick="${t.txid?`location.hash='#/${net}/tx/${encodeURIComponent(t.txid)}'`:''}">
          <div class="ml">
            <div class="mt"><span class="badge ${t.direction==='in'||t.type==='coinbase'?'ok':'warn'}">${esc(t.direction||t.type)}</span>
              ${t.block_height!=null?' · #'+t.block_height:''}</div>
            <div class="ms mono">${t.txid?esc(short(t.txid,14)):'—'}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No transactions</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showRichlist(){
  setHeroVisible(false);
  setBottomTab('richlist');
  await loadNetworks();
  const d=await api(`/api/${net}/richlist?limit=50`);
  const rows = d.richlist||[];
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Richlist'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/${net}'">← Home</button></div>
    <div class="card" style="margin-top:4px">
      <h3>Top addresses</h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>#</th><th>Address</th><th>Balance</th></tr></thead>
        <tbody>
          ${rows.map(r=>`<tr onclick="location.hash='#/${net}/address/${encodeURIComponent(r.address)}'">
            <td>${r.rank}</td>
            <td onclick="event.stopPropagation()">${linkAddr(r.address)}</td>
            <td class="amount">${esc(r.balance_fmt)}</td>
          </tr>`).join('')||'<tr><td colspan="3" class="muted" style="padding:16px">No balances</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${rows.map(r=>`<div class="mrow" onclick="location.hash='#/${net}/address/${encodeURIComponent(r.address)}'">
          <div class="ml">
            <div class="mt">#${r.rank} <span class="mono" style="font-weight:500">${esc(short(r.address,12))}</span></div>
            <div class="ms mono">${esc(short(r.address,20))}</div>
          </div>
          <div class="mr"><div class="ma">${esc(r.balance_fmt)}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No balances</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showMempool(){
  setHeroVisible(false);
  setBottomTab('mempool');
  await loadNetworks();
  const d=await api(`/api/${net}/mempool`);
  const rows = d.transactions||[];
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Mempool'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/${net}'">← Home</button></div>
    <div class="card" style="margin-top:4px">
      <h3>Mempool <span class="badge warn">${d.count||0} pending</span></h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Txid</th><th>From → To</th><th>Amount</th><th>Fee</th></tr></thead>
        <tbody>
          ${rows.map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${linkTx(t.txid)}</td>
            <td class="mono" onclick="event.stopPropagation()">${linkAddr(t.from)} → ${linkAddr(t.to)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${fmtAmt(t.fee||0)}</td>
          </tr>`).join('')||'<tr><td colspan="4" class="muted" style="padding:16px">Mempool empty</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${rows.map(t=>`<div class="mrow" onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
          <div class="ml">
            <div class="mt mono">${esc(short(t.txid,12))}</div>
            <div class="ms mono">${esc(short(t.from,8))} → ${esc(short(t.to,8))}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div><div class="ms">fee ${fmtAmt(t.fee||0)}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">Mempool empty</div></div>'}
      </div>
    </div>
  </div>`;
}

function sparkline(values, {w=280,h=56,stroke='var(--green)',fill='rgba(61,255,154,.12)'}={}){
  const vals = (values||[]).map(Number).filter(v=>isFinite(v));
  if(vals.length < 2) return `<div class="muted" style="padding:12px 0">Not enough data</div>`;
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  const pts = vals.map((v,i)=>{
    const x = (i/(vals.length-1)) * (w-8) + 4;
    const y = h - 4 - ((v - min) / span) * (h-12);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
  const area = `4,${h-4} ` + pts + ` ${w-4},${h-4}`;
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" style="display:block;background:rgba(0,0,0,.2);border:1px solid var(--border)">
    <polyline fill="${fill}" stroke="none" points="${area}"/>
    <polyline fill="none" stroke="${stroke}" stroke-width="2" points="${pts}"/>
  </svg>`;
}

async function showHealth(){
  setHeroVisible(false);
  setBottomTab('more');
  await loadNetworks();
  let h={};
  try{ h = await api('/api/public/health?window=48'); }catch(e){ h={error:e.message}; }
  const series = h.series || [];
  const blockTimes = series.map(x=>x.block_time).filter(v=>v!=null);
  const diffs = series.map(x=>x.difficulty_float || 0);
  const age = h.tip_age_seconds;
  const ageTxt = age==null ? '—' : (age>=3600 ? (age/3600).toFixed(1)+'h' : age>=60 ? Math.round(age/60)+'m' : age+'s');
  const statusBadge = h.status==='ok' ? 'ok' : (h.status==='slow' ? 'warn' : 'warn');
  const statusLabel = h.status==='ok' ? 'HEALTHY' : (h.status==='slow' ? 'SLOW' : (h.status==='stalled' ? 'STALLED' : 'UNKNOWN'));
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/public'},{label:'Network health'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/public'">← Home</button>
      <button class="chipbtn" onclick="showHealth()">Refresh</button>
      <button class="chipbtn" onclick="location.hash='#/run'">Run a node</button>
    </div>
    <div class="card detail">
      <div class="badge ${statusBadge}">${statusLabel}</div>
      <span class="badge blue" style="margin-left:6px">v${esc(String(h.version||'—'))}</span>
      <h2 style="margin:8px 0 6px">Network health</h2>
      <p class="muted" style="margin:0 0 12px">Live seed / public chain. Tip age and rolling charts — no ads, no trackers.</p>
      <div class="stats">
        <div class="stat"><div class="k">Height</div><div class="v">${h.height??'—'}</div><div class="s">tip</div></div>
        <div class="stat"><div class="k">Tip age</div><div class="v" style="font-size:1rem">${esc(ageTxt)}</div><div class="s">target ${h.target_block_time||60}s</div></div>
        <div class="stat"><div class="k">Avg block</div><div class="v" style="font-size:1rem">${h.avg_block_time!=null?(h.avg_block_time/60).toFixed(1)+'m': '—'}</div><div class="s">last ${h.window||'—'} blocks</div></div>
        <div class="stat"><div class="k">Mempool</div><div class="v">${h.mempool??'—'}</div><div class="s">pending</div></div>
      </div>
      <div class="kv" style="margin-top:12px">
        <div class="k">Difficulty</div><div>${esc(String(h.difficulty_label||'—'))}</div>
        <div class="k">Next work</div><div>${esc(String(h.next_difficulty_label||'—'))} · ~${esc(String(h.expected_hashes_next!=null?Math.round(h.expected_hashes_next):'—'))} hashes</div>
        <div class="k">Stall relief</div><div>after ${esc(String(h.stall_seconds||7200))}s tip age</div>
        <div class="k">Retarget safety</div><div>from height ${esc(String(h.retarget_safety_height||300))} (max 2× up)</div>
      </div>
    </div>
    <div class="card detail" style="margin-top:12px">
      <h3 style="margin-top:0">Block time (seconds)</h3>
      <p class="muted">Lower is faster. Spikes mean sparse hashrate — normal for a young CPU chain.</p>
      ${sparkline(blockTimes, {stroke:'var(--cyan,#5eb8ff)', fill:'rgba(94,184,255,.12)'})}
      <div class="muted" style="font-size:.75rem;margin-top:6px">min ${blockTimes.length?Math.min(...blockTimes):'—'}s · max ${blockTimes.length?Math.max(...blockTimes):'—'}s · n=${blockTimes.length}</div>
    </div>
    <div class="card detail" style="margin-top:12px">
      <h3 style="margin-top:0">Difficulty (smooth work index)</h3>
      <p class="muted">Continuous d-value (not nibble jumps). Safety rules soften upward spikes after h${esc(String(h.retarget_safety_height||300))}.</p>
      ${sparkline(diffs, {stroke:'var(--green)', fill:'rgba(61,255,154,.12)'})}
      <div class="muted" style="font-size:.75rem;margin-top:6px">latest ${diffs.length?Number(diffs[diffs.length-1]).toFixed(3):'—'}</div>
    </div>
    <div class="card detail" style="margin-top:12px">
      <h3 style="margin-top:0">Ops note</h3>
      <p class="muted" style="margin:0">Seed self-heals: auto-mine, 90s template refresh, 2h stall relief. Optional monitor:
      <span class="mono">scripts/howl-health-check.sh</span> (exit 1 if tip age &gt; 2h).</p>
    </div>
  </div>`;
}

async function showRunNode(){
  setHeroVisible(false);
  setBottomTab('more');
  await loadNetworks();
  let height='?', protocol='0.6-smooth-diff', version='0.6.0', dLabel='—', smoothH=120;
  try{
    const s=await api('/api/public/summary');
    if(s.online || s.height!=null){
      height=s.height;
      protocol=s.protocol||protocol;
      version=s.version||version;
      dLabel=s.difficulty_label||s.difficulty||dLabel;
      smoothH=s.smooth_diff_activation_height||120;
    }
  }catch(e){}
  const clone = `git clone ${REPO}.git
cd howlcoin
python3 -m pip install -r requirements.txt`;
  const goCmd = `cd howlcoin
python3 -m howl go`;
  const fullCmd = `git clone ${REPO}.git && cd howlcoin && python3 -m pip install -r requirements.txt && python3 -m howl go`;
  const upgradeCmd = `cd howlcoin
git pull origin main
# free ports if "Address already in use":
#   kill $(lsof -t -iTCP:42069 -sTCP:LISTEN) 2>/dev/null
#   kill $(lsof -t -iTCP:42070 -sTCP:LISTEN) 2>/dev/null
python3 -m howl go`;
  const desktopCmd = `cd howlcoin
./scripts/install-desktop-launcher.sh
# then double-click "Howlcoin Mine" on your Desktop`;
  const nodeOnlyCmd = `python3 -m howl node --public --auto-mine --open`;
  const mineOnceCmd = `python3 -m howl mine
# or continuous (without full node UI):
python3 -m howl mine --continuous`;
  const statusCmd = `python3 -m howl status
python3 -m howl wallet`;
  const beginnerCd = `cd ~/Desktop/howlcoin
# if your folder is elsewhere, use that path instead`;
  const everydayBundle = `cd ~/Desktop/howlcoin
python3 -m howl wallet
python3 -m howl status`;
  // Everyday settings rows for beginners (label, cmd, note)
  const everydayRows = [
    { label: 'Address + balance', cmd: 'python3 -m howl wallet', note: 'Safe first check — your H… address & HOWL balance' },
    { label: 'Chain status', cmd: 'python3 -m howl status', note: 'Height, difficulty, tip, mempool' },
    { label: 'Balance only', cmd: 'python3 -m howl balance', note: 'Quick balance for primary address' },
    { label: 'Software version', cmd: 'python3 -m howl --version', note: 'Must be v0.6+ for the public chain' },
    { label: 'Recovery phrase', cmd: 'python3 -m howl mnemonic', note: '⚠ Secrets — only when alone; backs up the wallet' },
    { label: 'Phrase via wallet', cmd: 'python3 -m howl wallet --show-mnemonic', note: '⚠ Same as mnemonic (dangerous if shared)' },
    { label: 'Private key', cmd: 'python3 -m howl wallet --show-keys', note: '⚠ Full control of funds — never paste online' },
    { label: 'JSON dump', cmd: 'python3 -m howl export', note: 'Tip + summary as JSON for debugging' },
    { label: 'Wallet folder', cmd: 'ls -la ~/.howlcoin/', note: 'wallet.json · chain.json live here' },
  ];
  const everydayTable = everydayRows.map(r => `
    <tr>
      <td style="padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:top;font-weight:600;color:var(--text);white-space:nowrap">${esc(r.label)}</td>
      <td style="padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:top">
        <code class="mono" style="font-size:.78rem;word-break:break-all">${esc(r.cmd)}</code>
        <div class="muted" style="font-size:.78rem;margin-top:4px">${esc(r.note)}</div>
      </td>
      <td style="padding:10px 4px;border-bottom:1px solid var(--border);vertical-align:top;width:72px">
        <button type="button" class="chipbtn" style="margin:0;padding:6px 10px;font-size:.72rem" onclick="copyText(${JSON.stringify(r.cmd)}, this)">Copy</button>
      </td>
    </tr>`).join('');
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/public'},{label:'Run a node'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/public'">← Home</button></div>
    <div class="card detail" style="margin-top:4px">
      <div style="display:flex;flex-wrap:wrap;gap:8px;align-items:center">
        <span class="badge ok">v${esc(String(version))} required</span>
        <span class="badge" style="background:rgba(192,132,252,.15);color:#c084fc;border:1px solid rgba(192,132,252,.35)">smooth difficulty</span>
      </div>
      <h2 style="margin:8px 0 6px;font-size:1.25rem">Run a node · connect &amp; mine</h2>
      <p class="muted" style="margin:0 0 12px">
        Live chain height <b>${esc(String(height))}</b> · protocol <span class="mono">${esc(String(protocol))}</span>
        · diff <b>${esc(String(dLabel))}</b>.
        From height <b>${esc(String(smoothH))}</b>, Howlcoin uses <b>v0.6 smooth difficulty</b>
        (continuous work target + 2h stall relief). Older nodes cannot follow the public tip — upgrade first.
      </p>
      <div class="kv">
        <div class="k">Public seed</div><div class="mono">${esc(SEED)}${copyBtn(SEED)}</div>
        <div class="k">Software</div><div>Howlcoin <b>v0.6+</b> (smooth diff hard fork)</div>
        <div class="k">Dashboard</div><div class="mono">http://127.0.0.1:42070/</div>
        <div class="k">Explorer</div><div><a href="https://howlscan.org/">https://howlscan.org/</a></div>
        <div class="k">Source</div><div><a href="${REPO}" target="_blank" rel="noopener">${esc(REPO)}</a></div>
      </div>
    </div>

    <div class="card detail" style="margin-top:14px;border-color:rgba(61,255,154,.35)">
      <h3 style="margin-top:0">⚡ Fastest path — one command</h3>
      <p class="muted" style="margin:0 0 10px">
        <code>howl go</code> connects the public seed, starts <b>mine forever</b>, opens the local dashboard,
        and frees ports if an old node is stuck. Leave Terminal open while mining.
      </p>
      ${cmdBox('New machine: clone + install + go', fullCmd)}
      ${cmdBox('Already cloned: connect & mine forever', goCmd)}
      <p style="margin:10px 0 0">
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl go', this)">Copy: howl go</button>
        <button class="chipbtn" style="margin:4px" onclick="copyText(${JSON.stringify(fullCmd)}, this)">Copy full setup</button>
      </p>
    </div>

    <div class="card detail" style="margin-top:14px;border-color:rgba(77,163,255,.35)">
      <h3 style="margin-top:0">⚙ Everyday settings (Terminal) — for beginners</h3>
      <p class="muted" style="margin:0 0 10px">
        After you install Howlcoin, open <b>Terminal</b>, go into the project folder, then run these anytime.
        This is your <b>local mining wallet</b> (not the browser wallet at howlscan.org/app unless you imported the same phrase).
      </p>
      ${cmdBox('Always start here (change folder if needed)', beginnerCd)}
      ${cmdBox('Safe daily check: wallet + chain', everydayBundle)}
      <p style="margin:8px 0 12px">
        <button class="chipbtn" style="margin:4px" onclick="copyText(${JSON.stringify(everydayBundle)}, this)">Copy daily check</button>
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl wallet', this)">Copy wallet</button>
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl status', this)">Copy status</button>
      </p>
      <div class="table-wrap" style="overflow-x:auto">
        <table style="width:100%;border-collapse:collapse;font-size:.88rem">
          <thead>
            <tr>
              <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600">What you want</th>
              <th style="text-align:left;padding:8px;border-bottom:1px solid var(--border);color:var(--muted);font-weight:600">Command</th>
              <th style="border-bottom:1px solid var(--border)"></th>
            </tr>
          </thead>
          <tbody>${everydayTable}</tbody>
        </table>
      </div>
      <div class="kv" style="margin-top:14px">
        <div class="k">Data folder</div><div class="mono">~/.howlcoin/</div>
        <div class="k">Dashboard UI</div><div class="mono">http://127.0.0.1:42070/</div>
        <div class="k">Browser wallet</div><div><a href="https://howlscan.org/app" target="_blank" rel="noopener">howlscan.org/app</a> (separate unless same seed)</div>
      </div>
      <p class="muted" style="margin:12px 0 0;font-size:.82rem;line-height:1.45">
        <b style="color:var(--amber,#ffb020)">Safety:</b> never share your recovery phrase or private key.
        Never paste them into websites, Discord, or Telegram. Anyone with those words controls your HOWL.
      </p>
    </div>

    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">⬆ Upgrade an existing node (v0.6)</h3>
      <p class="muted">If you mined before smooth difficulty, pull main and restart with <code>howl go</code>.</p>
      ${cmdBox('git pull + restart (recommended)', upgradeCmd)}
      <p class="muted" style="margin-bottom:0">
        Error <span class="mono">Address already in use</span>? The upgrade command comments show how to free
        ports <span class="mono">42069</span> / <span class="mono">42070</span>. <code>howl go</code> also tries to free them automatically.
      </p>
    </div>

    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">🖥 Mac Desktop (double-click)</h3>
      <p class="muted">Install a Desktop app shortcut that does the same as <code>howl go</code>.</p>
      ${cmdBox('Install “Howlcoin Mine” on Desktop', desktopCmd)}
      <p class="muted" style="margin-bottom:0">Then double-click <b>Howlcoin Mine</b> on your Desktop. Keep the Terminal window open while mining.</p>
    </div>

    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">Step by step</h3>
      ${cmdBox('1) Install Howlcoin', clone)}
      ${cmdBox('2) Connect public seed + mine forever + open dashboard', goCmd)}
      <p class="muted">Equivalent flags:</p>
      ${cmdBox('Node with flags (same as go)', nodeOnlyCmd)}
      <p class="muted">Dashboard: <span class="mono">http://127.0.0.1:42070/</span> — use <b>⚡ Connect seed &amp; mine forever</b> if mining did not auto-start.</p>
      ${cmdBox('3) Check height & wallet (everyday settings)', statusCmd)}
      <p class="muted">More beginner commands: see <b>Everyday settings</b> above.</p>
      ${cmdBox('4) Mine without full dashboard (optional)', mineOnceCmd)}
    </div>

    <div class="card detail" style="margin-top:14px;border-color:rgba(77,163,255,.3)">
      <h3 style="margin-top:0">📡 Communicate with other nodes (P2P)</h3>
      <p class="muted" style="margin:0 0 10px">
        Howlcoin nodes talk over <b>TCP port 42069</b> using simple JSON messages.
        They share the same genesis, sync longer chains, and relay new blocks + transactions.
        You do <b>not</b> chat manually — “communicate” means <b>connect as a peer</b>.
      </p>
      <div class="kv">
        <div class="k">Public seed</div><div class="mono">${esc(SEED)}${copyBtn(SEED)}</div>
        <div class="k">P2P port</div><div class="mono">42069</div>
        <div class="k">What peers share</div><div>Blocks · mempool txs · height / tip (hello)</div>
        <div class="k">Must match</div><div>Same genesis + software rules (v0.6+)</div>
      </div>

      <h4 style="margin:16px 0 8px;font-size:.9rem;color:var(--text)">1) Join the public network (easiest)</h4>
      <p class="muted" style="margin:0 0 8px">This dials the public seed and keeps you in the mesh.</p>
      ${cmdBox('Connect + mine + dashboard', goCmd)}
      <p style="margin:8px 0">
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl go', this)">Copy howl go</button>
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl node --public --auto-mine --open', this)">Copy node --public</button>
      </p>

      <h4 style="margin:16px 0 8px;font-size:.9rem;color:var(--text)">2) Add a friend’s node (extra peer)</h4>
      <p class="muted" style="margin:0 0 8px">
        Both of you run a node. One shares their public IP (or LAN IP) and has port <b>42069</b> open.
        Example: friend is at <span class="mono">203.0.113.10</span> → connect to <span class="mono">203.0.113.10:42069</span>.
      </p>
      ${cmdBox('Start node and also dial a friend', `python3 -m howl node --public --auto-mine --open --connect ${SEED} --connect FRIEND_IP:42069`)}
      ${cmdBox('While your node is running — add peer from another Terminal', `curl -sS -X POST http://127.0.0.1:42070/api/connect \\
  -H 'Content-Type: application/json' \\
  -d '{"peer":"FRIEND_IP:42069"}'`)}
      <p class="muted" style="margin:8px 0 0">
        Or open the <b>local dashboard</b> → <span class="mono">http://127.0.0.1:42070/</span> → <b>Connect peer</b>
        (paste <span class="mono">host:42069</span>).
      </p>

      <h4 style="margin:16px 0 8px;font-size:.9rem;color:var(--text)">3) See who you’re talking to</h4>
      ${cmdBox('Saved peer list on disk', `python3 -m howl peers
# also: cat ~/.howlcoin/peers.json`)}
      <p class="muted" style="margin:8px 0 0">
        Live peers (height, tip) show in the dashboard under <b>Peers</b> while <code>howl go</code> / <code>howl node</code> is running.
      </p>

      <h4 style="margin:16px 0 8px;font-size:.9rem;color:var(--text)">4) Let others reach you (optional)</h4>
      <ul class="muted" style="margin:0;padding-left:1.2rem;line-height:1.55">
        <li>Your node already <b>listens</b> on <span class="mono">0.0.0.0:42069</span> when running.</li>
        <li>To accept inbound peers from the internet: forward <b>TCP 42069</b> on your router to this computer, and share <span class="mono">YOUR_PUBLIC_IP:42069</span>.</li>
        <li>On the same Wi‑Fi, friends can use your LAN IP (e.g. <span class="mono">192.168.1.20:42069</span>).</li>
        <li>Home NATs often block inbound — that’s OK: you can still <b>dial out</b> to the public seed and friends who are reachable.</li>
      </ul>

      <h4 style="margin:16px 0 8px;font-size:.9rem;color:var(--text)">What gets exchanged automatically</h4>
      <ul class="muted" style="margin:0;padding-left:1.2rem;line-height:1.55">
        <li><b>hello</b> — coin, version, height, tip, genesis (must match)</li>
        <li><b>get_blocks / blocks</b> — download or send chain history to catch up</li>
        <li><b>inv</b> — “I have a new tip”</li>
        <li><b>block / tx</b> — relay newly mined blocks and mempool transfers</li>
        <li><b>ping / pong</b> — keep the connection alive</li>
      </ul>
      <p class="muted" style="margin:12px 0 0;font-size:.82rem">
        There is no private chat channel — all protocol traffic is chain sync + gossip.
        Wrong genesis or old software (pre‑v0.6) → peer disconnects.
      </p>
    </div>

    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">What v0.6 changed</h3>
      <ul class="muted" style="margin:0;padding-left:1.2rem;line-height:1.55">
        <li><b>Smooth difficulty</b> from height ${esc(String(smoothH))} — continuous work target (no more 16× nibble jumps)</li>
        <li><b>Stall relief</b> — if blocks are &gt;2 hours apart, difficulty can drop so CPUs can catch up</li>
        <li><b>howl go</b> — public seed + auto-mine + browser dashboard in one command</li>
        <li>Nodes below <b>v0.6</b> will not accept post-fork blocks — always <code>git pull</code> before mining</li>
      </ul>
    </div>

    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">Useful links</h3>
      <p>
        <a class="chipbtn" style="display:inline-block;text-decoration:none;margin:4px" href="${REPO}" target="_blank" rel="noopener">Open GitHub repo</a>
        <a class="chipbtn" style="display:inline-block;text-decoration:none;margin:4px" href="${REPO}/archive/refs/heads/main.zip" target="_blank" rel="noopener">Download ZIP</a>
        <a class="chipbtn" style="display:inline-block;text-decoration:none;margin:4px" href="/whitepaper" target="_blank" rel="noopener">White paper</a>
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl go', this)">Copy howl go</button>
        <button class="chipbtn" style="margin:4px" onclick="copyText('${SEED}', this)">Copy seed</button>
      </p>
      <p class="muted" style="margin-bottom:0">
        After connect, your node downloads blocks until height matches Howlscan.
        Mining rewards go to your <b>local</b> wallet (<code>python3 -m howl wallet</code> / <code>mnemonic</code> to back up).
      </p>
    </div>
  </div>`;
}

function doSearch(){
  const q=($('#q')&&$('#q').value||'').trim();
  if(!q) return loadHome();
  if(/^\d+$/.test(q)) { location.hash=`#/${net}/block/${q}`; return route(); }
  if(q.startsWith('H') && q.length>20){ location.hash=`#/${net}/address/${encodeURIComponent(q)}`; return route(); }
  // try as block hash then tx
  location.hash=`#/${net}/block/${encodeURIComponent(q)}`;
  route().catch(()=>{ location.hash=`#/${net}/tx/${encodeURIComponent(q)}`; return route(); })
    .catch(()=>{ app().innerHTML=`<div class="main"><div class="card detail err">Not found: <span class="mono">${esc(q)}</span></div></div>`; });
}

async function route(){
  toggleDrawer(false);
  const h=(location.hash||'').replace(/^#\/?/,'');
  const parts=h.split('/').filter(Boolean);
  if(parts[0] && networks.length && networks.find(n=>n.id===parts[0])){
    net=parts[0];
  }
  renderNav();
  setBottomTab(activeTabFromRoute(parts));
  // scroll to top on navigation (mobile)
  try{ window.scrollTo({top:0, behavior:'instant' in window ? 'instant' : 'auto'}); }catch(e){ window.scrollTo(0,0); }
  try{
    if(parts.length>=3 && parts[1]==='block') return await showBlock(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='tx') return await showTx(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='address') return await showAddr(decodeURIComponent(parts[2]));
    if(parts.length>=1 && (parts[0]==='run' || parts[0]==='node' || parts[0]==='sync')) return await showRunNode();
    if(parts.length>=1 && (parts[0]==='health' || parts[0]==='status' || parts[0]==='charts')) return await showHealth();
    if(parts.length>=2 && parts[1]==='richlist') return await showRichlist();
    if(parts.length>=2 && parts[1]==='mempool') return await showMempool();
    if(parts.length>=2 && parts[0]==='block') return await showBlock(decodeURIComponent(parts[1]));
    if(parts.length>=1 && parts[0]==='richlist') return await showRichlist();
    if(parts.length>=1 && parts[0]==='mempool') return await showMempool();
    return await loadHome();
  }catch(e){
    app().innerHTML=`<div class="main"><div class="card detail err">${esc(e.message)}</div></div>`;
  }
}
function isHomeHash(){
  const h=(location.hash||'').replace(/^#\/?/,'').split('/').filter(Boolean);
  if(!h.length) return true;
  if(h.length===1 && networks.find(n=>n.id===h[0])) return true;
  return false;
}
function refreshData(){
  // Manual refresh of numbers only — never remounts the banner animation
  if(isHomeHash()) loadHome().catch(()=>{});
  else route().catch(()=>{});
}
window.addEventListener('hashchange', ()=>route());
ensureBanner();
loadNetworks().then(route);
// Background data refresh only (banner stays mounted, animation uninterrupted)
setInterval(()=>{ if(isHomeHash()) loadHome().catch(()=>{}); }, 20000);
// Tip ticker: flash when height/tip changes
let __lastTipKey = '';
setInterval(async ()=>{
  if(!isHomeHash()) return;
  try{
    const s = await api('/api/public/summary');
    const key = (s.height||'') + ':' + (s.tip||'');
    const el = document.getElementById('tipTickerHash');
    const box = document.getElementById('tipTicker');
    if(el && s.tip) el.textContent = short(s.tip, 18);
    if(box && __lastTipKey && key !== __lastTipKey){
      box.style.borderColor = 'var(--green)';
      box.style.boxShadow = '0 0 16px rgba(61,255,154,.35)';
      setTimeout(()=>{ box.style.borderColor = ''; box.style.boxShadow = ''; }, 1200);
    }
    __lastTipKey = key;
  }catch(e){}
}, 8000);
</script>
</body>
</html>
"""



class ExplorerServer:
    def __init__(
        self,
        hub: ExplorerHub,
        host: str = "127.0.0.1",
        port: int = 42080,
    ):
        self.hub = hub
        self.host = host
        self.port = port

    def make_handler(self):
        hub = self.hub

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _json(self, code: int, obj: Any):
                body = json.dumps(obj, default=str).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _bytes(self, code: int, data: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Access-Control-Allow-Origin", "*")
                # ES modules need correct CORS; allow caching of static wallet assets
                if "javascript" in ctype or "manifest" in ctype:
                    self.send_header("Cache-Control", "public, max-age=300")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                qs = urllib.parse.parse_qs(parsed.query)

                # RFC 9116 security.txt (Cloudflare / security researchers)
                if path in (
                    "/.well-known/security.txt",
                    "/security.txt",
                ):
                    sec = ASSETS_DIR / "security.txt"
                    if sec.is_file():
                        return self._bytes(
                            200,
                            sec.read_bytes(),
                            "text/plain; charset=utf-8",
                        )
                    return self._json(404, {"error": "security.txt not found"})

                # robots.txt — AI training bots blocked; normal search allowed
                if path == "/robots.txt":
                    rob = ASSETS_DIR / "robots.txt"
                    if rob.is_file():
                        return self._bytes(
                            200,
                            rob.read_bytes(),
                            "text/plain; charset=utf-8",
                        )
                    return self._json(404, {"error": "robots.txt not found"})

                if path in ("/", "/index.html"):
                    return self._bytes(200, EXPLORER_HTML.encode(), "text/html; charset=utf-8")

                if path in ("/whitepaper", "/whitepaper.html"):
                    wp = ASSETS_DIR / "whitepaper.html"
                    if not wp.is_file():
                        return self._json(404, {"error": "whitepaper not found"})
                    return self._bytes(200, wp.read_bytes(), "text/html; charset=utf-8")

                if path in ("/wallet", "/wallet.html"):
                    wh = ASSETS_DIR / "wallet.html"
                    if not wh.is_file():
                        return self._json(404, {"error": "wallet guide not found"})
                    return self._bytes(200, wh.read_bytes(), "text/html; charset=utf-8")

                # Public non-custodial wallet (syncs to public chain via API)
                if path in ("/app", "/app/", "/wallet/app", "/wallet/app/"):
                    app = ASSETS_DIR / "public-wallet.html"
                    if not app.is_file():
                        return self._json(404, {"error": "public wallet not found"})
                    # no-cache so SOL balance / wallet UI fixes land immediately
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                    self.send_header("Pragma", "no-cache")
                    self.send_header("Access-Control-Allow-Origin", "*")
                    data = app.read_bytes()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                    return

                # Token / listing identity page (for humans + aggregators)
                if path in ("/token", "/token/", "/contract", "/contracts"):
                    chain = hub.get("public")
                    info = howl_token_info(chain)
                    rows = []
                    for k, label in (
                        ("name", "Name"),
                        ("symbol", "Symbol / Ticker"),
                        ("type", "Type"),
                        ("platform", "Platform"),
                        ("algorithm", "Algorithm"),
                        ("decimals", "Decimals"),
                        ("genesis_hash", "Genesis hash (chain ID)"),
                        ("explorer", "Block explorer"),
                        ("website", "Website"),
                        ("whitepaper", "White paper"),
                        ("github", "Source code"),
                        ("wallet", "Web wallet"),
                        ("seed_node", "P2P seed"),
                    ):
                        val = info.get(k)
                        if val is None or val == "":
                            continue
                        rows.append(
                            f"<tr><th>{html_lib.escape(str(label))}</th>"
                            f"<td class='mono'>{html_lib.escape(str(val))}</td></tr>"
                        )
                    contract_block = (
                        "<p><b>Native contract address:</b> "
                        "<span class='mono'>N/A — native L1 coin (not ERC-20)</span></p>"
                    )
                    if info.get("contracts"):
                        clines = []
                        for c in info["contracts"]:
                            clines.append(
                                f"<li><b>{html_lib.escape(c.get('chain',''))}</b> "
                                f"({html_lib.escape(c.get('standard',''))}): "
                                f"<span class='mono'>{html_lib.escape(c.get('address',''))}</span> "
                                f"— <a href='{html_lib.escape(c.get('explorer',''))}'>explorer</a></li>"
                            )
                        contract_block += (
                            "<p><b>Wrapped token contracts</b> (optional):</p><ul>"
                            + "".join(clines)
                            + "</ul>"
                        )
                    else:
                        contract_block += (
                            "<p class='muted'>No wrapped ERC-20 / BEP-20 / SPL HOWL is published yet. "
                            "When you deploy one, set HOWL_ERC20_CONTRACT / HOWL_BEP20_CONTRACT / "
                            "HOWL_SPL_MINT on the server.</p>"
                        )
                    page = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="utf-8"/><meta name="viewport" content="width=device-width,initial-scale=1"/>
<meta name="theme-color" content="#0c0f14" id="themeColorMeta"/>
<title>HOWL Token Info — Howlcoin</title>
<link rel="icon" href="/assets/howlcoin-logo-meme-pup-coin.jpg"/>
<script>
(function(){{
  try{{
    var t=localStorage.getItem('howlscan_theme_v1')||localStorage.getItem('howl_theme_v1')||'dark';
    if(['light','dark','neo','bones'].indexOf(t)<0) t='dark';
    document.documentElement.setAttribute('data-theme', t);
  }}catch(e){{}}
}})();
</script>
<link rel="stylesheet" href="/assets/howl-site-theme.css"/>
<style>
*{{box-sizing:border-box}}
body{{font-family:system-ui,sans-serif;background:var(--bg);color:var(--text);margin:0;padding:24px;line-height:1.5}}
a{{color:var(--green)}} .mono{{font-family:ui-monospace,Menlo,monospace;font-size:.85rem;word-break:break-all}}
.topbar{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:0 auto 16px;max-width:720px}}
.topbar a{{color:var(--text);text-decoration:none;border:1px solid var(--border);background:var(--chip);
  padding:8px 12px;font-weight:600;font-size:.85rem}}
.card{{max-width:720px;margin:0 auto;background:var(--panel);border:1px solid var(--border);padding:20px}}
h1{{margin:0 0 8px;font-size:1.4rem}} h1 span{{color:var(--green)}}
.muted{{color:var(--muted);font-size:.9rem}}
table{{width:100%;border-collapse:collapse;margin:16px 0}}
th,td{{text-align:left;padding:10px 8px;border-bottom:1px solid var(--border);vertical-align:top}}
th{{color:var(--muted);font-size:.75rem;text-transform:uppercase;letter-spacing:.06em;width:34%}}
.badge{{display:inline-block;padding:4px 10px;background:var(--ok-bg);color:var(--green);font-size:.75rem;font-weight:700}}
.note{{margin-top:16px;padding:12px;background:var(--note-bg);border:1px solid var(--note-border);font-size:.88rem;color:var(--note-text)}}
</style></head><body>
<div class="topbar">
  <a href="/">Explorer</a>
  <a href="/whitepaper">White paper</a>
  <a href="/wallet">Wallet</a>
  <div style="flex:1"></div>
  <select class="howl-theme-select" id="themeSelect" title="Appearance" aria-label="Appearance" onchange="HowlTheme.set(this.value)">
    <option value="dark">Dark</option>
    <option value="light">Light</option>
    <option value="neo">Neo</option>
    <option value="bones">Bones</option>
  </select>
</div>
<div class="card">
  <div class="badge">OFFICIAL · LISTING INFO</div>
  <h1>Howl<span>coin</span> (HOWL)</h1>
  <p class="muted">Identifiers for CoinMarketCap, CoinCodex, CoinGecko, and wallets.</p>
  {contract_block}
  <table>{''.join(rows)}</table>
  <div class="note">{html_lib.escape(info.get("contract_note") or "")}</div>
  <p class="muted" style="margin-top:16px">
    Machine-readable: <a href="/token.json">/token.json</a> ·
    <a href="/api/public/token-info">/api/public/token-info</a> ·
    <a href="/">Howlscan</a> · <a href="/whitepaper">White paper</a>
  </p>
</div>
<script src="/assets/howl-site-theme.js"></script>
</body></html>"""
                    return self._bytes(200, page.encode("utf-8"), "text/html; charset=utf-8")

                if path in ("/manifest.webmanifest", "/manifest.json"):
                    man = ASSETS_DIR / "wallet-manifest.webmanifest"
                    if man.is_file():
                        return self._bytes(
                            200, man.read_bytes(), "application/manifest+json"
                        )

                if path == "/sw.js":
                    sw = ASSETS_DIR / "wallet-sw.js"
                    if sw.is_file():
                        self.send_response(200)
                        self.send_header("Content-Type", "application/javascript")
                        self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                        self.send_header("Access-Control-Allow-Origin", "*")
                        data = sw.read_bytes()
                        self.send_header("Content-Length", str(len(data)))
                        self.end_headers()
                        self.wfile.write(data)
                        return

                if path in (
                    "/assets/howl-native-bridge.js",
                    "/howl-native-bridge.js",
                    "/native-bridge.js",
                ):
                    br = ASSETS_DIR / "howl-native-bridge.js"
                    if br.is_file():
                        return self._bytes(
                            200,
                            br.read_bytes(),
                            "application/javascript; charset=utf-8",
                        )

                if path.startswith("/assets/"):
                    name = path[len("/assets/") :]
                    if ".." in name:
                        return self._json(400, {"error": "bad path"})
                    f = ASSETS_DIR / name
                    if not f.is_file():
                        return self._json(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    if name.endswith(".mjs"):
                        ctype = "text/javascript; charset=utf-8"
                    return self._bytes(200, f.read_bytes(), ctype)

                # NFT media files (uploaded from wallet mint flow)
                if path.startswith("/media/"):
                    name = path[len("/media/") :]
                    if ".." in name or "/" in name or not name:
                        return self._json(400, {"error": "bad path"})
                    candidates = []
                    pub = hub.paths.get("public")
                    if pub:
                        candidates.append(Path(pub) / "media" / name)
                    candidates.append(MEDIA_DIR / name)
                    f = next((p for p in candidates if p.is_file()), None)
                    if not f:
                        return self._json(404, {"error": "media not found"})
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    self.send_response(200)
                    self.send_header("Content-Type", ctype)
                    self.send_header("Content-Length", str(f.stat().st_size))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.end_headers()
                    self.wfile.write(f.read_bytes())
                    return

                if path == "/api/networks":
                    return self._json(200, {"networks": hub.list_networks()})

                if path in ("/api/public/health", "/api/health"):
                    try:
                        limit = int((qs.get("window") or qs.get("limit") or ["40"])[0])
                    except (TypeError, ValueError):
                        limit = 40
                    chain = hub.get("public")
                    if not chain:
                        return self._json(503, {"error": "public chain offline"})
                    try:
                        chain.reload_from_disk()
                    except Exception:
                        pass
                    try:
                        return self._json(200, chain.network_health(window=limit))
                    except Exception as e:
                        return self._json(500, {"error": str(e)})

                if path in ("/api/public/fees", "/api/fees"):
                    return self._json(
                        200,
                        {
                            "min_fee": format_howl(MIN_TX_FEE_HOWLIES),
                            "min_fee_howlies": MIN_TX_FEE_HOWLIES,
                            "default_fee": format_howl(DEFAULT_TX_FEE_HOWLIES),
                            "default_fee_howlies": DEFAULT_TX_FEE_HOWLIES,
                            "default_fee_howl": DEFAULT_TX_FEE_HOWLIES / 100_000_000,
                            "note": "Fees are paid to the miner who confirms the transaction",
                        },
                    )

                if path in (
                    "/api/public/token",
                    "/api/public/token-info",
                    "/api/token",
                    "/token.json",
                ):
                    chain = hub.get("public")
                    return self._json(200, howl_token_info(chain))

                # Market prices for portfolio total (USD / BTC)
                if path in ("/api/public/prices", "/api/prices"):
                    force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
                    return self._json(200, fetch_market_prices(force=force))

                # WalletConnect config (projectId is a public client id)
                if path in (
                    "/api/public/walletconnect",
                    "/api/walletconnect",
                    "/api/public/wc",
                ):
                    pid = HOWL_WC_PROJECT_ID
                    return self._json(
                        200,
                        {
                            "enabled": bool(pid),
                            "projectId": pid,
                            "metadata": {
                                "name": "Howlcoin Wallet",
                                "description": "Howlcoin multi-chain wallet",
                                "url": HOWL_SITE,
                                "icons": [
                                    f"{HOWL_SITE}/assets/howlcoin-logo-meme-pup-coin.jpg"
                                ],
                            },
                            "deepLink": f"{HOWL_SITE}/app",
                            "setup": "Create a free project at https://cloud.reown.com and set HOWL_WC_PROJECT_ID on the server",
                            "chains": [
                                "eip155:1",
                                "eip155:10",
                                "eip155:8453",
                                "eip155:56",
                                "eip155:43114",
                            ],
                        },
                    )

                # Solana balance / history proxy (public RPCs block browser Origin: howlscan.org)
                if path in ("/api/public/sol/balance", "/api/sol/balance"):
                    addr = (qs.get("address") or qs.get("addr") or [""])[0].strip()
                    if not addr or len(addr) < 32:
                        return self._json(400, {"error": "address required"})
                    try:
                        res = solana_rpc_call(
                            "getBalance", [addr, {"commitment": "confirmed"}]
                        )
                        lamports = 0
                        if isinstance(res, dict):
                            lamports = int(res.get("value") or 0)
                        else:
                            lamports = int(res or 0)
                        return self._json(
                            200,
                            {
                                "address": addr,
                                "lamports": lamports,
                                "sol": lamports / 1e9,
                                "source": "howlscan-proxy",
                            },
                        )
                    except Exception as e:
                        return self._json(502, {"error": f"sol balance failed: {e}"})

                if path in ("/api/public/sol/signatures", "/api/sol/signatures"):
                    addr = (qs.get("address") or qs.get("addr") or [""])[0].strip()
                    try:
                        limit = int((qs.get("limit") or ["15"])[0])
                    except ValueError:
                        limit = 15
                    limit = max(1, min(40, limit))
                    if not addr:
                        return self._json(400, {"error": "address required"})
                    try:
                        sigs = solana_rpc_call(
                            "getSignaturesForAddress", [addr, {"limit": limit}]
                        )
                        return self._json(
                            200, {"address": addr, "signatures": sigs or []}
                        )
                    except Exception as e:
                        return self._json(502, {"error": f"sol signatures failed: {e}"})

                if path in ("/api/public/sol/tx", "/api/sol/tx"):
                    sig = (qs.get("sig") or qs.get("signature") or [""])[0].strip()
                    if not sig:
                        return self._json(400, {"error": "signature required"})
                    try:
                        tx = solana_rpc_call(
                            "getTransaction",
                            [
                                sig,
                                {
                                    "encoding": "jsonParsed",
                                    "maxSupportedTransactionVersion": 0,
                                    "commitment": "confirmed",
                                },
                            ],
                        )
                        return self._json(200, {"signature": sig, "transaction": tx})
                    except Exception as e:
                        return self._json(502, {"error": f"sol tx failed: {e}"})

                if path in (
                    "/api/public/sol/token-balance",
                    "/api/sol/token-balance",
                ):
                    owner = (qs.get("owner") or qs.get("address") or [""])[0].strip()
                    mint = (qs.get("mint") or [""])[0].strip()
                    if not owner or not mint:
                        return self._json(400, {"error": "owner and mint required"})
                    try:
                        res = solana_rpc_call(
                            "getTokenAccountsByOwner",
                            [
                                owner,
                                {"mint": mint},
                                {"encoding": "jsonParsed", "commitment": "confirmed"},
                            ],
                        )
                        total = 0.0
                        for acc in (res or {}).get("value") or []:
                            info = (
                                ((acc.get("account") or {}).get("data") or {})
                                .get("parsed")
                                or {}
                            ).get("info") or {}
                            ta = info.get("tokenAmount") or {}
                            if ta.get("uiAmount") is not None:
                                total += float(ta["uiAmount"])
                        return self._json(
                            200,
                            {
                                "owner": owner,
                                "mint": mint,
                                "uiAmount": total,
                                "source": "howlscan-proxy",
                            },
                        )
                    except Exception as e:
                        return self._json(502, {"error": f"sol token balance failed: {e}"})

                if path in ("/api/public/search", "/api/search"):
                    q = (qs.get("q") or qs.get("query") or [""])[0]
                    try:
                        limit = int((qs.get("limit") or ["12"])[0])
                    except ValueError:
                        limit = 12
                    try:
                        results = web_search(q, limit=limit)
                        return self._json(
                            200,
                            {
                                "engine": "Howl Search",
                                "query": q,
                                "count": len(results),
                                "results": results,
                                "in_app": True,
                            },
                        )
                    except Exception as e:
                        return self._json(502, {"error": f"search failed: {e}", "query": q})

                if path in ("/api/public/discover", "/api/discover"):
                    force = (qs.get("refresh") or qs.get("force") or ["0"])[0] in (
                        "1",
                        "true",
                        "yes",
                    )
                    try:
                        return self._json(200, discover_feed(force=force))
                    except Exception as e:
                        return self._json(502, {"error": f"discover failed: {e}"})

                if path in ("/api/public/reader", "/api/reader"):
                    u = (qs.get("url") or [""])[0].strip()
                    if not u:
                        return self._json(400, {"error": "url required"})
                    try:
                        return self._json(200, fetch_reader(u))
                    except Exception as e:
                        return self._json(
                            502,
                            {
                                "error": f"reader failed: {e}",
                                "url": u,
                                "prefer_reader": prefers_reader(u),
                            },
                        )

                if path in ("/api/public/embed-check", "/api/embed-check"):
                    u = (qs.get("url") or [""])[0].strip()
                    return self._json(
                        200,
                        {
                            "url": u,
                            "prefer_reader": prefers_reader(u),
                            "reason": "known frame-blocker"
                            if prefers_reader(u)
                            else "try_iframe",
                        },
                    )

                # Howl Swap bridge (Phase A: SOL/USDC → native HOWL)
                if path in ("/api/public/bridge", "/api/public/bridge/config", "/api/bridge"):
                    try:
                        from .bridge import bridge_config

                        return self._json(200, bridge_config())
                    except Exception as e:
                        return self._json(500, {"error": str(e)})

                if path in ("/api/public/bridge/quote", "/api/bridge/quote"):
                    try:
                        from .bridge import quote_howl

                        asset = (qs.get("asset") or ["sol"])[0]
                        amount = float((qs.get("amount") or ["0"])[0])
                        return self._json(200, quote_howl(asset, amount))
                    except Exception as e:
                        return self._json(400, {"error": str(e)})

                if path in ("/api/public/bridge/orders", "/api/bridge/orders"):
                    try:
                        from .bridge import list_orders

                        howl = (qs.get("howl") or qs.get("address") or [""])[0]
                        pub = hub.paths.get("public")
                        orders = list_orders(pub, howl_address=howl)[:30]
                        return self._json(200, {"count": len(orders), "orders": orders})
                    except Exception as e:
                        return self._json(500, {"error": str(e)})

                if path.startswith("/api/public/bridge/order/") or path.startswith(
                    "/api/bridge/order/"
                ):
                    # GET single order — handled below for GET only
                    parts = path.strip("/").split("/")
                    # api/public/bridge/order/<id>
                    oid = parts[-1] if parts else ""
                    if oid and oid not in ("order", "orders"):
                        try:
                            from .bridge import get_order

                            pub = hub.paths.get("public")
                            o = get_order(oid, pub)
                            if not o:
                                return self._json(404, {"error": "order not found"})
                            return self._json(200, o)
                        except Exception as e:
                            return self._json(500, {"error": str(e)})

                # /api/<net>/...
                parts = path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "api":
                    net = parts[1]
                    if net not in hub.paths:
                        return self._json(404, {"error": f"unknown network {net}"})
                    chain = hub.get(net)
                    rest = parts[2:] if len(parts) > 2 else []

                    if not rest or rest == ["summary"]:
                        nets = {n["id"]: n for n in hub.list_networks()}
                        info = nets.get(net, {"id": net, "online": False})
                        if chain:
                            s = chain.summary()
                            info = {**info, **s, "online": True, "label": info.get("label", net)}
                        return self._json(200, info)

                    if not chain:
                        return self._json(404, {"error": "chain offline", "network": net})

                    if rest[0] == "blocks":
                        limit = int(qs.get("limit", ["25"])[0])
                        return self._json(200, {"network": net, "blocks": chain.recent_blocks(limit)})

                    if rest[0] == "txs":
                        limit = int(qs.get("limit", ["25"])[0])
                        return self._json(
                            200,
                            {"network": net, "transactions": chain.recent_transactions(limit)},
                        )

                    if rest[0] == "mempool":
                        try:
                            chain.purge_invalid_mempool(save=True)
                        except Exception:
                            pass
                        return self._json(
                            200,
                            {
                                "network": net,
                                "count": len(chain.mempool),
                                "transactions": chain.mempool_list(),
                            },
                        )

                    if rest[0] == "richlist":
                        limit = int(qs.get("limit", ["50"])[0])
                        return self._json(
                            200,
                            {"network": net, "richlist": chain.richlist(limit)},
                        )

                    if rest[0] == "block" and len(rest) >= 2:
                        key = urllib.parse.unquote("/".join(rest[1:]))
                        b = chain.get_block(key)
                        if not b:
                            return self._json(404, {"error": "block not found"})
                        return self._json(200, {"network": net, "block": b})

                    if rest[0] == "tx" and len(rest) >= 2:
                        key = urllib.parse.unquote("/".join(rest[1:]))
                        t = chain.find_tx(key)
                        if not t:
                            return self._json(404, {"error": "tx not found"})
                        return self._json(200, {"network": net, **t})

                    if rest[0] == "address" and len(rest) >= 2:
                        addr = urllib.parse.unquote(rest[1])
                        if not is_valid_address(addr) and addr != "HOWL_GENESIS_BURN":
                            # still allow lookup of known strings
                            pass
                        return self._json(200, {"network": net, **chain.address_history(addr)})

                    if rest[0] == "nfts":
                        owner = (qs.get("owner") or [None])[0]
                        limit = int(qs.get("limit", ["100"])[0])
                        include_hist = (qs.get("history") or ["0"])[0] in (
                            "1",
                            "true",
                            "yes",
                        )
                        # all=1 or no owner → full gallery (dashboard)
                        nfts = chain.list_nfts(
                            owner=owner,
                            limit=limit,
                            include_history=include_hist,
                        )
                        return self._json(
                            200,
                            {
                                "network": net,
                                "nfts": nfts,
                                "count": len(chain.nfts),
                                "returned": len(nfts),
                            },
                        )

                    if rest[0] == "nft-events" or rest[0] == "nft_events":
                        limit = int(qs.get("limit", ["100"])[0])
                        return self._json(
                            200,
                            {
                                "network": net,
                                "events": chain.nft_events(limit=limit),
                                "count": len(chain.nfts),
                            },
                        )

                    if rest[0] == "nft" and len(rest) >= 2:
                        nid = urllib.parse.unquote(rest[1])
                        n = chain.get_nft(nid, include_history=True)
                        if not n:
                            return self._json(404, {"error": "nft not found"})
                        return self._json(200, {"network": net, "nft": n})

                    if rest[0] == "oracle":
                        if len(rest) >= 2:
                            key = urllib.parse.unquote(rest[1])
                            row = chain.oracle_get(key)
                            if not row:
                                return self._json(404, {"error": "oracle key not found"})
                            return self._json(200, {"network": net, "entry": row})
                        limit = int(qs.get("limit", ["100"])[0])
                        return self._json(
                            200,
                            {
                                "network": net,
                                "feed": chain.oracle_feed(limit=limit),
                                "count": len(chain.oracle),
                            },
                        )

                    # On-chain names: howl.name.<slug>
                    if rest[0] in ("names", "name"):
                        if rest[0] == "name" and len(rest) >= 2:
                            q = urllib.parse.unquote(rest[1]).strip()
                            row = chain.resolve_name(q)
                            if not row:
                                return self._json(404, {"error": "name not found"})
                            return self._json(200, {"network": net, "name": row})
                        # ?q= or ?address=
                        q = (qs.get("q") or qs.get("name") or [None])[0]
                        addr = (qs.get("address") or [None])[0]
                        if q:
                            row = chain.resolve_name(q)
                            if not row:
                                return self._json(404, {"error": "name not found"})
                            return self._json(200, {"network": net, "name": row})
                        if addr:
                            nm = chain.name_for_address(addr)
                            return self._json(
                                200,
                                {
                                    "network": net,
                                    "address": addr,
                                    "name": nm,
                                    "name_display": f"@{nm}" if nm else None,
                                },
                            )
                        limit = int(qs.get("limit", ["100"])[0])
                        rows = chain.list_names(limit=limit)
                        return self._json(
                            200,
                            {
                                "network": net,
                                "names": rows,
                                "count": len(rows),
                                "note": "Register via oracle key howl.name.<slug> (3–16 a-z0-9_)",
                            },
                        )

                    if rest[0] in ("contracts", "contract"):
                        if rest[0] == "contract" and len(rest) >= 2:
                            cid = urllib.parse.unquote(rest[1])
                            row = chain.get_contract(cid)
                            if not row:
                                return self._json(404, {"error": "contract not found"})
                            return self._json(200, {"network": net, "contract": row})
                        owner = (qs.get("owner") or [None])[0]
                        kind = (qs.get("kind") or [None])[0]
                        limit = int(qs.get("limit", ["100"])[0])
                        rows = chain.list_contracts(
                            owner=owner, kind=kind, limit=limit
                        )
                        return self._json(
                            200,
                            {
                                "network": net,
                                "contracts": rows,
                                "count": len(chain.contracts),
                                "returned": len(rows),
                                "kinds": list(getattr(chain, "CONTRACT_KINDS", ())),
                            },
                        )

                return self._json(404, {"error": "not found"})

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_POST(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode() or "{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "invalid json"})

                # Howl Swap bridge POST
                if path in ("/api/public/bridge/order", "/api/bridge/order"):
                    try:
                        from .bridge import create_order

                        pub = hub.paths.get("public")
                        order = create_order(
                            howl_address=str(body.get("howl_address") or body.get("howl") or ""),
                            asset=str(body.get("asset") or "sol"),
                            amount_in=float(body.get("amount") or body.get("amount_in") or 0),
                            sol_from=str(body.get("sol_from") or body.get("from") or ""),
                            dd=pub,
                        )
                        return self._json(200, order)
                    except Exception as e:
                        return self._json(400, {"error": str(e)})

                if path.startswith("/api/public/bridge/order/") or path.startswith(
                    "/api/bridge/order/"
                ):
                    parts = path.strip("/").split("/")
                    # .../order/<id>/tx  or .../order/<id>
                    if len(parts) >= 5 and parts[-1] == "tx":
                        oid = parts[-2]
                        try:
                            from .bridge import attach_deposit_tx

                            pub = hub.paths.get("public")
                            o = attach_deposit_tx(
                                oid, str(body.get("deposit_tx") or body.get("tx") or ""), pub
                            )
                            if not o:
                                return self._json(404, {"error": "order not found"})
                            return self._json(200, o)
                        except Exception as e:
                            return self._json(400, {"error": str(e)})
                    if len(parts) >= 4:
                        oid = parts[-1]
                        # admin mark paid / complete
                        action = str(body.get("action") or "")
                        if action in ("mark_paid", "complete", "fail"):
                            try:
                                from .bridge import admin_secret_ok, update_order

                                if not admin_secret_ok(str(body.get("secret") or "")):
                                    return self._json(403, {"error": "forbidden"})
                                patch: Dict[str, Any] = {}
                                if action == "mark_paid":
                                    patch = {
                                        "status": "paid",
                                        "deposit_tx": body.get("deposit_tx")
                                        or body.get("tx")
                                        or "",
                                    }
                                elif action == "complete":
                                    patch = {
                                        "status": "completed",
                                        "howl_txid": body.get("howl_txid") or "",
                                        "deposit_tx": body.get("deposit_tx") or "",
                                    }
                                elif action == "fail":
                                    patch = {
                                        "status": "failed",
                                        "error": body.get("error") or "failed",
                                    }
                                pub = hub.paths.get("public")
                                o = update_order(oid, patch, pub)
                                if not o:
                                    return self._json(404, {"error": "order not found"})
                                return self._json(200, o)
                            except Exception as e:
                                return self._json(400, {"error": str(e)})

                # NFT media upload (base64 image → permanent /media/ URL)
                if path in ("/api/public/nft-media", "/api/nft-media"):
                    try:
                        img = body.get("image") or body.get("image_b64") or body.get("data") or ""
                        mime = str(body.get("mime") or body.get("content_type") or "image/jpeg")
                        if not img:
                            return self._json(400, {"error": "image required (base64 or data URL)"})
                        pub = hub.paths.get("public")
                        base = Path(pub) / "media" if pub else MEDIA_DIR
                        meta = _save_nft_media(str(img), mime=mime, base_dir=base)
                        return self._json(200, meta)
                    except ValueError as e:
                        return self._json(400, {"error": str(e)})
                    except Exception as e:
                        return self._json(500, {"error": f"upload failed: {e}"})

                # Generic Solana JSON-RPC proxy (POST body: {method, params})
                if path in ("/api/public/sol/rpc", "/api/sol/rpc"):
                    method = str(body.get("method") or "")
                    params = body.get("params")
                    if not method:
                        return self._json(400, {"error": "method required"})
                    if params is None:
                        params = []
                    if not isinstance(params, list):
                        return self._json(400, {"error": "params must be array"})
                    # allowlist methods used by the wallet
                    allowed = {
                        "getBalance",
                        "getSignaturesForAddress",
                        "getTransaction",
                        "getTokenAccountsByOwner",
                        "getAccountInfo",
                        "getHealth",
                        "getSlot",
                        "getLatestBlockhash",
                        "sendTransaction",
                        "getTokenLargestAccounts",
                    }
                    if method not in allowed:
                        return self._json(400, {"error": f"method not allowed: {method}"})
                    try:
                        result = solana_rpc_call(method, params)
                        return self._json(200, {"result": result})
                    except Exception as e:
                        return self._json(502, {"error": str(e)})

                # Browser wallets broadcast signed txs → live seed node mempool + P2P
                if path in ("/api/public/broadcast", "/api/broadcast"):
                    tx = body.get("tx") if isinstance(body.get("tx"), dict) else body
                    if not isinstance(tx, dict):
                        return self._json(400, {"error": "tx object required"})
                    try:
                        req = urllib.request.Request(
                            f"{NODE_RPC}/api/broadcast",
                            data=json.dumps({"tx": tx}).encode(),
                            headers={"Content-Type": "application/json"},
                            method="POST",
                        )
                        with urllib.request.urlopen(req, timeout=15) as resp:
                            data = json.loads(resp.read().decode())
                        return self._json(200, data)
                    except urllib.error.HTTPError as e:
                        try:
                            err = json.loads(e.read().decode())
                            return self._json(e.code, err)
                        except Exception:
                            return self._json(e.code, {"error": str(e.reason)})
                    except Exception as e:
                        # Fallback: inject into explorer's on-disk chain (may not relay P2P)
                        try:
                            chain = hub.get("public")
                            if not chain:
                                return self._json(
                                    503,
                                    {
                                        "error": (
                                            f"seed RPC unreachable ({e}); public chain offline"
                                        )
                                    },
                                )
                            ok, msg = chain.add_to_mempool(tx)
                            if not ok:
                                return self._json(400, {"error": msg})
                            return self._json(
                                200,
                                {
                                    "ok": True,
                                    "txid": msg,
                                    "warning": "queued on disk only; seed RPC was offline",
                                },
                            )
                        except Exception as e2:
                            return self._json(503, {"error": f"broadcast failed: {e}; {e2}"})

                return self._json(404, {"error": "not found"})

        return Handler

    def serve_forever(self) -> None:
        httpd = ThreadingHTTPServer((self.host, self.port), self.make_handler())
        print(f"Howlcoin Explorer → http://{self.host}:{self.port}/")
        for n in self.hub.list_networks():
            status = f"height {n['height']}" if n.get("online") else "offline"
            print(f"  · {n['id']}: {status} ({n['path']})")
        httpd.serve_forever()


def default_networks(
    public_dir: Optional[Path] = None,
    telegram_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    """Build network map (public Howlcoin ledger only)."""
    import os

    pub = Path(os.environ.get("HOWL_PUBLIC_DATA", public_dir or DEFAULT_PUBLIC))
    return {"public": pub}


def main(
    host: str = "127.0.0.1",
    port: int = 42080,
    public_dir: Optional[str] = None,
    telegram_dir: Optional[str] = None,
) -> None:
    nets = default_networks(Path(public_dir) if public_dir else None)
    hub = ExplorerHub(nets)
    ExplorerServer(hub, host=host, port=port).serve_forever()
