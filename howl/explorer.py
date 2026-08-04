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
import threading
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
                    "User-Agent": "Howlscan/0.6.4 (+https://howlscan.org)",
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
_chart_cache: Dict[str, Any] = {}  # key -> {ts, data}
_markets_board_cache: Dict[str, Any] = {"ts": 0.0, "data": None}
_coin_profile_cache: Dict[str, Any] = {}  # id -> {ts, data}
_chart_samples_lock = threading.Lock()

# Howl Charts product note (public-facing — Howlcoin only, no third-party brands)
_HOWL_CHARTS_NOTE = "Howl Charts · Howlcoin product"
_HOWL_CHART_ID = "howlcoin"

# External majors for Howl Charts market coverage (+ HOWL index is injected)
_MARKETS_COIN_IDS = (
    "bitcoin,ethereum,solana,binancecoin,ripple,cardano,dogecoin,tron,"
    "avalanche-2,chainlink,polkadot,litecoin,bitcoin-cash,near,aptos,sui,"
    "toncoin,stellar,cosmos,uniswap,aave,tezos,matic-network,shiba-inu,"
    "wrapped-bitcoin,leo-token,hyperliquid"
)

_MARKETS_META = {
    "bitcoin": ("BTC", "Bitcoin"),
    "ethereum": ("ETH", "Ethereum"),
    "solana": ("SOL", "Solana"),
    "binancecoin": ("BNB", "BNB"),
    "ripple": ("XRP", "XRP"),
    "cardano": ("ADA", "Cardano"),
    "dogecoin": ("DOGE", "Dogecoin"),
    "tron": ("TRX", "TRON"),
    "avalanche-2": ("AVAX", "Avalanche"),
    "chainlink": ("LINK", "Chainlink"),
    "polkadot": ("DOT", "Polkadot"),
    "litecoin": ("LTC", "Litecoin"),
    "bitcoin-cash": ("BCH", "Bitcoin Cash"),
    "near": ("NEAR", "NEAR"),
    "aptos": ("APT", "Aptos"),
    "sui": ("SUI", "Sui"),
    "toncoin": ("TON", "Toncoin"),
    "stellar": ("XLM", "Stellar"),
    "cosmos": ("ATOM", "Cosmos"),
    "uniswap": ("UNI", "Uniswap"),
    "aave": ("AAVE", "Aave"),
    "tezos": ("XTZ", "Tezos"),
    "matic-network": ("MATIC", "Polygon"),
    "shiba-inu": ("SHIB", "Shiba Inu"),
    "wrapped-bitcoin": ("WBTC", "Wrapped Bitcoin"),
    "leo-token": ("LEO", "LEO"),
    "hyperliquid": ("HYPE", "Hyperliquid"),
}

# Yahoo Finance symbols for multi-year / lifetime series (CoinGecko free API
# no longer allows days=max — error 10012 time-range limit).
_YAHOO_SYMBOLS = {
    "bitcoin": "BTC-USD",
    "ethereum": "ETH-USD",
    "solana": "SOL-USD",
    "binancecoin": "BNB-USD",
    "ripple": "XRP-USD",
    "cardano": "ADA-USD",
    "dogecoin": "DOGE-USD",
    "tron": "TRX-USD",
    "avalanche-2": "AVAX-USD",
    "chainlink": "LINK-USD",
    "polkadot": "DOT-USD",
    "litecoin": "LTC-USD",
    "bitcoin-cash": "BCH-USD",
    "near": "NEAR-USD",
    "aptos": "APT-USD",
    "sui": "SUI-USD",
    "toncoin": "TON-USD",
    "stellar": "XLM-USD",
    "cosmos": "ATOM-USD",
    "uniswap": "UNI-USD",
    "aave": "AAVE-USD",
    "tezos": "XTZ-USD",
    "matic-network": "MATIC-USD",
    "shiba-inu": "SHIB-USD",
    "wrapped-bitcoin": "WBTC-USD",
    "leo-token": "LEO-USD",
    "hyperliquid": "HYPE-USD",
}


def _markets_allowed_ids() -> set:
    """Assets Howl Charts can price from on-chain sources (+ legacy meta ids)."""
    allowed = set(_CHAINLINK_FEEDS.keys()) if "_CHAINLINK_FEEDS" in globals() else set()
    allowed |= {_HOWL_CHART_ID, "howl"}
    # keep portfolio CG ids for fetch_market_prices only via separate allow list
    return allowed


def _charts_allowed_ids() -> set:
    return set(_CHAINLINK_FEEDS.keys()) | {_HOWL_CHART_ID, "howl"}


def _howl_charts_root() -> Path:
    root = Path(
        os.environ.get("HOWL_PUBLIC_DATA")
        or os.environ.get("HOWL_DATA_DIR")
        or str(DEFAULT_DATA_DIR)
    ).expanduser()
    try:
        root.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return root


def _howl_charts_data_path() -> Path:
    """Legacy HOWL-only samples file (migrated into multi-asset store)."""
    return _howl_charts_root() / "howl_charts_howl_index.json"


def _howl_charts_samples_path() -> Path:
    """Own multi-asset price history — Howl Charts product DB (not a market API)."""
    return _howl_charts_root() / "howl_charts_samples.json"


# Ethereum Chainlink USD proxy feeds (on-chain oracles — not CoinGecko/Yahoo).
# answer has 8 decimals for these aggregators.
_CHAINLINK_ETH_RPCS = (
    os.environ.get("HOWL_ETH_RPC", "").strip(),
    "https://ethereum.publicnode.com",
    "https://rpc.ankr.com/eth",
    "https://1rpc.io/eth",
    "https://cloudflare-eth.com",
)
_CHAINLINK_FEEDS: Dict[str, Tuple[str, str, str, int]] = {
    # id: (proxy_address, symbol, name, decimals)
    "bitcoin": ("0xF4030086522a5bEEa4988F8cA5B36dbC97BeE88c", "BTC", "Bitcoin", 8),
    "ethereum": ("0x5f4eC3Df9cbd43714FE2740f5E3616155c5b8419", "ETH", "Ethereum", 8),
    "solana": ("0x4ffC43a60e009B551865A93d232E33Fce9f01507", "SOL", "Solana", 8),
    "binancecoin": ("0x14e613AC84a31f709eadbdF89C6CC390fDc9540A", "BNB", "BNB", 8),
    "chainlink": ("0x2c1d072e956AFFC0D435Cb7AC38EF18d24d9127c", "LINK", "Chainlink", 8),
    "avalanche-2": ("0xFF3EEb22B5E3dE6e705b44749C2559d704923FD7", "AVAX", "Avalanche", 8),
    "matic-network": ("0x7bAC85A8a13A4BcD8abb3eB7d6b4d632c5a57676", "MATIC", "Polygon", 8),
    "uniswap": ("0x553303d460EE0afB37EdFf9bE42922D8FF63220e", "UNI", "Uniswap", 8),
    "aave": ("0x547a514d5e3769680Ce22B2361c10Ea13619e8a9", "AAVE", "Aave", 8),
    # Note: only verified AggregatorV3 proxies — add more feeds as we confirm on-chain.
}


def _eth_json_rpc(method: str, params: list, timeout: int = 14) -> Any:
    body = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    ).encode("utf-8")
    last_err: Optional[Exception] = None
    for rpc in _CHAINLINK_ETH_RPCS:
        if not rpc:
            continue
        try:
            raw = _http_post(
                rpc,
                body,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": "Howlscan/0.6.4 (+https://howlscan.org)",
                    "Accept": "application/json",
                },
                timeout=timeout,
            )
            j = json.loads(raw.decode("utf-8", errors="ignore"))
            if isinstance(j, dict) and j.get("error"):
                last_err = RuntimeError(str(j["error"]))
                continue
            return j.get("result") if isinstance(j, dict) else None
        except Exception as e:
            last_err = e
            continue
    raise RuntimeError(f"eth rpc failed: {last_err}")


def _chainlink_latest(feed: str) -> Dict[str, Any]:
    """
    Read Chainlink AggregatorV3 latestRoundData() on Ethereum.
    selector 0xfeaf968c → (roundId, answer, startedAt, updatedAt, answeredInRound)
    """
    addr = feed
    # latestRoundData()
    res = _eth_json_rpc(
        "eth_call",
        [{"to": addr, "data": "0xfeaf968c"}, "latest"],
        timeout=12,
    )
    if not isinstance(res, str) or not res.startswith("0x") or len(res) < 2 + 64 * 5:
        raise RuntimeError("empty chainlink result")
    b = bytes.fromhex(res[2:])
    answer = int.from_bytes(b[32:64], "big", signed=True)
    updated = int.from_bytes(b[96:128], "big", signed=False)
    return {"answer": answer, "updated": updated}


def _load_all_chart_samples() -> Dict[str, List[Dict[str, Any]]]:
    path = _howl_charts_samples_path()
    store: Dict[str, List[Dict[str, Any]]] = {}
    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if not isinstance(v, list):
                        continue
                    pts: List[Dict[str, Any]] = []
                    for row in v:
                        if not isinstance(row, dict):
                            continue
                        try:
                            pts.append({"t": int(row["t"]), "p": float(row["p"])})
                        except (KeyError, TypeError, ValueError):
                            continue
                    store[str(k)] = pts
        except Exception:
            store = {}
    # migrate legacy HOWL-only file once
    legacy = _howl_charts_data_path()
    if _HOWL_CHART_ID not in store and legacy.exists():
        try:
            raw = json.loads(legacy.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                pts = []
                for row in raw:
                    if not isinstance(row, dict):
                        continue
                    try:
                        pts.append({"t": int(row["t"]), "p": float(row["p"])})
                    except (KeyError, TypeError, ValueError):
                        continue
                if pts:
                    store[_HOWL_CHART_ID] = pts
        except Exception:
            pass
    return store


def _save_all_chart_samples(store: Dict[str, List[Dict[str, Any]]]) -> None:
    path = _howl_charts_samples_path()
    try:
        path.write_text(json.dumps(store), encoding="utf-8")
    except OSError:
        pass


def _record_price_sample(
    cid: str, usd: float, now: float, min_gap: int = 900, force: bool = False
) -> bool:
    """Append sparse samples into Howl Charts' own history store. Returns True if written."""
    with _chart_samples_lock:
        store = _load_all_chart_samples()
        samples = list(store.get(cid) or [])
        if not force and samples:
            last_t = float(samples[-1].get("t") or 0)
            if now - last_t < min_gap:
                return False
        samples.append({"t": int(now), "p": float(usd)})
        if len(samples) > 20000:
            samples = samples[-20000:]
        store[cid] = samples
        _save_all_chart_samples(store)
        return True


def _load_price_samples(cid: str) -> List[Dict[str, Any]]:
    return list(_load_all_chart_samples().get(cid) or [])


def _series_from_samples(
    cid: str, days: str, usd: float, now: float
) -> List[Dict[str, Any]]:
    samples = _load_price_samples(cid)
    if days == "max":
        cutoff = 0
    else:
        try:
            n = int(days)
        except ValueError:
            n = 7
        cutoff = int(now) - n * 86400
    pts = [p for p in samples if p["t"] >= cutoff]
    if not pts or pts[-1]["t"] < int(now) - 30:
        pts.append({"t": int(now), "p": float(usd)})
    if len(pts) < 2:
        span = 86400 if days == "1" else (7 * 86400 if days != "max" else 30 * 86400)
        pts = [
            {"t": int(now) - span, "p": float(usd)},
            {"t": int(now), "p": float(usd)},
        ]
    return pts


def _change_from_samples(cid: str, usd: float, now: float, window_s: int = 86400) -> Optional[float]:
    samples = _load_price_samples(cid)
    target = int(now) - window_s
    past = None
    for s in samples:
        if s["t"] <= target:
            past = s["p"]
        else:
            break
    if past is None or not past:
        return None
    try:
        return round((float(usd) - float(past)) / float(past) * 100.0, 3)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def howl_swap_index(record: bool = True) -> Dict[str, Any]:
    """
    First-party HOWL USD index from Howl Swap (bridge) rates.
    1 USDC → howl_per_usdc HOWL  ⇒  usd_per_howl = 1 / howl_per_usdc.
    """
    now = time.time()
    try:
        from .bridge import bridge_config

        cfg = bridge_config()
        usdc = next((a for a in (cfg.get("assets") or []) if a.get("id") == "usdc"), None)
        sol = next((a for a in (cfg.get("assets") or []) if a.get("id") == "sol"), None)
        howl_per_usdc = float((usdc or {}).get("howl_per_unit") or 10.0)
        howl_per_sol = float((sol or {}).get("howl_per_unit") or 100_000.0)
        enabled = bool(cfg.get("enabled"))
    except Exception:
        howl_per_usdc, howl_per_sol, enabled = 10.0, 100_000.0, False
    usd = (1.0 / howl_per_usdc) if howl_per_usdc > 0 else None
    out = {
        "id": _HOWL_CHART_ID,
        "symbol": "HOWL",
        "name": "Howlcoin",
        "usd": usd,
        "howl_per_usdc": howl_per_usdc,
        "howl_per_sol": howl_per_sol,
        "bridge_enabled": enabled,
        "index": "howl-swap",
        "product": "Howl Charts",
        "updated": int(now),
        "note": _HOWL_CHARTS_NOTE,
    }
    if record and usd is not None:
        try:
            _record_price_sample(_HOWL_CHART_ID, float(usd), now)
        except Exception:
            pass
    return out


def fetch_onchain_spot(
    cid: str, record: bool = True, force_record: bool = False, min_gap: int = 900
) -> Dict[str, Any]:
    """
    Live USD from blockchain data only:
      - howlcoin → Howl Swap index (our chain/product rate)
      - majors → Chainlink AggregatorV3 on Ethereum (on-chain oracle)
    No CoinGecko / Yahoo price APIs.
    """
    now = time.time()
    key = (cid or "").strip().lower()
    if key in ("howl", _HOWL_CHART_ID):
        out = howl_swap_index(record=False)
        if record and out.get("usd") is not None:
            try:
                wrote = _record_price_sample(
                    _HOWL_CHART_ID, float(out["usd"]), now, min_gap=min_gap, force=force_record
                )
                out["recorded"] = wrote
            except Exception as e:
                out["record_error"] = str(e)
        return out
    meta = _CHAINLINK_FEEDS.get(key)
    if not meta:
        raise RuntimeError(f"no on-chain feed for {key}")
    addr, sym, name, dec = meta
    raw = _chainlink_latest(addr)
    usd = float(raw["answer"]) / float(10**dec)
    recorded = False
    if record:
        try:
            recorded = _record_price_sample(
                key, usd, now, min_gap=min_gap, force=force_record
            )
        except Exception:
            recorded = False
    return {
        "id": key,
        "symbol": sym,
        "name": name,
        "usd": usd,
        "feed": addr,
        "oracle": "onchain",
        "chain": "ethereum",
        "updated_onchain": int(raw.get("updated") or now),
        "updated": int(now),
        "product": "Howl Charts",
        "source": "onchain",
        "recorded": recorded,
        "note": _HOWL_CHARTS_NOTE,
    }


def sample_howl_charts(
    force: bool = False, min_gap: int = 300
) -> Dict[str, Any]:
    """
    One sampling pass for the 24/7 Howl Charts systemd sampler.
    Reads on-chain spots (parallel) then records under a file lock.
    """
    now = time.time()
    ids = [_HOWL_CHART_ID] + list(_CHAINLINK_FEEDS.keys())
    ok: List[Dict[str, Any]] = []
    errors: List[str] = []
    spots: Dict[str, Dict[str, Any]] = {}

    def _fetch(cid: str) -> Tuple[str, Optional[Dict[str, Any]], Optional[str]]:
        try:
            # Fetch only — record serially below to avoid lost writes
            return cid, fetch_onchain_spot(cid, record=False), None
        except Exception as e:
            return cid, None, str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for cid, spot, err in pool.map(_fetch, ids):
            if err or not spot:
                errors.append(f"{cid}: {err or 'empty'}")
                continue
            spots[cid] = spot

    recorded_n = 0
    for cid, spot in spots.items():
        usd = spot.get("usd")
        wrote = False
        if usd is not None:
            try:
                wrote = _record_price_sample(
                    cid if cid != "howl" else _HOWL_CHART_ID,
                    float(usd),
                    now,
                    min_gap=min_gap,
                    force=force,
                )
            except Exception as e:
                errors.append(f"{cid} record: {e}")
                wrote = False
        if wrote:
            recorded_n += 1
        ok.append(
            {
                "id": cid,
                "symbol": spot.get("symbol"),
                "usd": usd,
                "recorded": wrote,
                "oracle": spot.get("oracle") or spot.get("index") or spot.get("source"),
            }
        )

    path = str(_howl_charts_samples_path())
    store = _load_all_chart_samples()
    counts = {k: len(v) for k, v in store.items()}
    return {
        "ok": True,
        "product": "Howl Charts",
        "sampled": len(ok),
        "recorded": recorded_n,
        "assets": ok,
        "errors": errors,
        "sample_counts": counts,
        "path": path,
        "updated": int(now),
        "note": _HOWL_CHARTS_NOTE + " · 24/7 sampler",
    }


def fetch_markets_board(force: bool = False) -> Dict[str, Any]:
    """
    Howl Charts live board from on-chain sources only:
    HOWL (Howl Swap) + Chainlink feeds. Charts history is our own samples.
    """
    now = time.time()
    # Client polls Howl Charts every 30s — keep board cache ≤ that window
    if (
        not force
        and _markets_board_cache.get("data")
        and (now - float(_markets_board_cache.get("ts") or 0)) < 30
    ):
        out = dict(_markets_board_cache["data"])  # type: ignore[arg-type]
        out["cached"] = True
        return out

    coins: List[Dict[str, Any]] = []
    errors: List[str] = []

    # HOWL first
    try:
        howl = howl_swap_index(record=True)
        coins.append(
            {
                "id": _HOWL_CHART_ID,
                "symbol": "HOWL",
                "name": "Howlcoin",
                "usd": howl.get("usd"),
                "change_24h": _change_from_samples(
                    _HOWL_CHART_ID, float(howl["usd"]), now
                )
                if howl.get("usd") is not None
                else None,
                "market_cap": None,
                "vol_24h": None,
                "product": True,
                "index": "howl-swap",
                "oracle": "howl-swap",
                "badge": "Howlcoin",
            }
        )
    except Exception as e:
        errors.append(f"howl: {e}")

    # Parallel Chainlink reads
    def _one(cid: str) -> Optional[Dict[str, Any]]:
        try:
            spot = fetch_onchain_spot(cid, record=True)
            usd = spot.get("usd")
            chg = (
                _change_from_samples(cid, float(usd), now)
                if usd is not None
                else None
            )
            return {
                "id": cid,
                "symbol": spot.get("symbol"),
                "name": spot.get("name"),
                "usd": usd,
                "change_24h": chg,
                "market_cap": None,
                "vol_24h": None,
                "oracle": "onchain",
                "feed": spot.get("feed"),
            }
        except Exception as e:
            errors.append(f"{cid}: {e}")
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futs = [pool.submit(_one, cid) for cid in _CHAINLINK_FEEDS.keys()]
        for fut in concurrent.futures.as_completed(futs):
            row = fut.result()
            if row:
                coins.append(row)

    # HOWL pinned; rest by usd desc
    howl_rows = [c for c in coins if c.get("id") == _HOWL_CHART_ID]
    rest = [c for c in coins if c.get("id") != _HOWL_CHART_ID]
    rest.sort(key=lambda c: (-(c.get("usd") or 0), c.get("symbol") or ""))
    coins = howl_rows + rest

    out = {
        "coins": coins,
        "count": len(coins),
        "updated": int(now),
        "source": "howl-charts",
        "feeds": ["howl-swap", "onchain"],
        "product": "Howl Charts",
        "note": _HOWL_CHARTS_NOTE + " · on-chain spots + Howl Swap",
        "cached": False,
    }
    if errors and not coins:
        out["error"] = "; ".join(errors[:6])
    elif errors:
        out["warnings"] = errors[:8]
    if coins:
        _markets_board_cache["ts"] = now
        _markets_board_cache["data"] = {**out, "cached": True}
    elif _markets_board_cache.get("data"):
        stale = dict(_markets_board_cache["data"])  # type: ignore[arg-type]
        stale["stale"] = True
        stale["error"] = "; ".join(errors[:6]) if errors else "empty board"
        return stale
    return out


def fetch_coin_profile(coin_id: str = "bitcoin", force: bool = False) -> Dict[str, Any]:
    """Live price + sample ATH/ATL from on-chain spots + Howl Charts history."""
    cid = (coin_id or "bitcoin").strip().lower()[:64]
    if cid in ("howl", _HOWL_CHART_ID):
        cid = _HOWL_CHART_ID
    if cid not in _charts_allowed_ids():
        cid = _HOWL_CHART_ID
    now = time.time()
    hit = _coin_profile_cache.get(cid)
    if (
        not force
        and hit
        and (now - float(hit.get("ts") or 0)) < 30
        and hit.get("data")
    ):
        out = dict(hit["data"])
        out["cached"] = True
        return out

    try:
        spot = fetch_onchain_spot(cid, record=True)
        usd = spot.get("usd")
        samples = _load_price_samples(cid)
        prices = [float(s["p"]) for s in samples if s.get("p") is not None]
        if usd is not None:
            prices.append(float(usd))
        ath = max(prices) if prices else usd
        atl = min(prices) if prices else usd
        day_ago = int(now) - 86400
        day_prices = [float(s["p"]) for s in samples if int(s.get("t") or 0) >= day_ago]
        if usd is not None:
            day_prices.append(float(usd))
        out = {
            "id": cid,
            "symbol": spot.get("symbol") or cid[:6].upper(),
            "name": spot.get("name") or cid,
            "usd": usd,
            "change_24h": (
                _change_from_samples(cid, float(usd), now) if usd is not None else None
            ),
            "change_7d": (
                _change_from_samples(cid, float(usd), now, 7 * 86400)
                if usd is not None
                else None
            ),
            "change_30d": (
                _change_from_samples(cid, float(usd), now, 30 * 86400)
                if usd is not None
                else None
            ),
            "change_1y": (
                _change_from_samples(cid, float(usd), now, 365 * 86400)
                if usd is not None
                else None
            ),
            "ath": ath,
            "ath_date": None,
            "ath_change_pct": (
                round((float(usd) - float(ath)) / float(ath) * 100.0, 3)
                if usd is not None and ath
                else None
            ),
            "atl": atl,
            "atl_date": None,
            "atl_change_pct": (
                round((float(usd) - float(atl)) / float(atl) * 100.0, 3)
                if usd is not None and atl
                else None
            ),
            "market_cap": None,
            "vol_24h": None,
            "high_24h": max(day_prices) if day_prices else usd,
            "low_24h": min(day_prices) if day_prices else usd,
            "oracle": spot.get("oracle") or spot.get("index") or spot.get("source"),
            "feed": spot.get("feed"),
            "index": spot.get("index"),
            "howl_per_usdc": spot.get("howl_per_usdc"),
            "bridge_enabled": spot.get("bridge_enabled"),
            "product": "Howl Charts",
            "updated": int(now),
            "source": "howl-charts",
            "note": _HOWL_CHARTS_NOTE,
            "cached": False,
        }
        _coin_profile_cache[cid] = {"ts": now, "data": {**out, "cached": True}}
        return out
    except Exception as e:
        if hit and hit.get("data"):
            stale = dict(hit["data"])
            stale["stale"] = True
            stale["error"] = str(e)
            return stale
        return {
            "id": cid,
            "error": str(e),
            "source": "none",
            "product": "Howl Charts",
            "updated": int(now),
            "note": _HOWL_CHARTS_NOTE,
        }


def _downsample_points(points: List[Dict[str, Any]], target: int = 180) -> List[Dict[str, Any]]:
    if len(points) <= target + 20:
        return points
    step = max(1, len(points) // target)
    out = points[::step]
    # always keep the last point for "live" close
    if out and points and out[-1]["t"] != points[-1]["t"]:
        out.append(points[-1])
    return out


def _chart_payload(
    cid: str,
    d: str,
    points: List[Dict[str, Any]],
    source: str,
    now: float,
) -> Dict[str, Any]:
    points = _downsample_points(points)
    first = points[0]["p"] if points else 0.0
    last = points[-1]["p"] if points else 0.0
    chg = ((last - first) / first * 100.0) if first else 0.0
    if cid == _HOWL_CHART_ID:
        sym, name = "HOWL", "Howlcoin"
    elif cid in _CHAINLINK_FEEDS:
        _addr, sym, name, _dec = _CHAINLINK_FEEDS[cid]
    else:
        sym, name = _MARKETS_META.get(cid, (cid[:6].upper(), cid))
    return {
        "id": cid,
        "symbol": sym,
        "name": name,
        "days": d,
        "range": "lifetime" if d == "max" else f"{d}d",
        "points": points,
        "count": len(points),
        "open": first,
        "close": last,
        "change_pct": round(chg, 3),
        "high": max((x["p"] for x in points), default=0.0),
        "low": min((x["p"] for x in points), default=0.0),
        "updated": int(now),
        "source": source,
        "product": "Howl Charts",
        "note": _HOWL_CHARTS_NOTE,
        "cached": False,
    }


def fetch_market_chart(
    coin_id: str = "bitcoin", days: str = "7", force: bool = False
) -> Dict[str, Any]:
    """
    Howl Charts canvas series — no CoinGecko/Yahoo.
    Live tick from on-chain (Chainlink / Howl Swap); history from our samples.
    """
    cid = (coin_id or "bitcoin").strip().lower()[:64]
    if cid in ("howl", _HOWL_CHART_ID):
        cid = _HOWL_CHART_ID
    d = (days or "7").strip()
    if d not in ("1", "7", "14", "30", "90", "180", "365", "max"):
        d = "7"
    if cid not in _charts_allowed_ids():
        cid = _HOWL_CHART_ID
    key = f"{cid}:{d}"
    now = time.time()
    # Short ranges refresh with the 30s Howl Charts UI poll; longer ranges cache more
    ttl = 30 if d in ("1", "7", "14", "30") else 120
    hit = _chart_cache.get(key)
    if (
        not force
        and hit
        and (now - float(hit.get("ts") or 0)) < ttl
        and hit.get("data")
        and (hit["data"].get("points") or [])
    ):
        out = dict(hit["data"])
        out["cached"] = True
        return out

    try:
        spot = fetch_onchain_spot(cid, record=True)
        usd = float(spot["usd"]) if spot.get("usd") is not None else None
        if usd is None:
            raise RuntimeError("no on-chain spot")
        points = _series_from_samples(cid, d, usd, now)
        source = "howl-swap" if cid == _HOWL_CHART_ID else "howl-charts"
        out = _chart_payload(cid, d, points, source, now)
        if cid == _HOWL_CHART_ID:
            out["index"] = "howl-swap"
            out["howl_per_usdc"] = spot.get("howl_per_usdc")
        else:
            out["oracle"] = "onchain"
            out["feed"] = spot.get("feed")
        # Lifetime for our product = full Howl Charts sample history
        if d == "max":
            out["range"] = "lifetime"
        _chart_cache[key] = {"ts": now, "data": {**out, "cached": True}}
        return out
    except Exception as e:
        if hit and hit.get("data") and (hit["data"].get("points") or []):
            stale = dict(hit["data"])
            stale["stale"] = True
            stale["error"] = str(e)
            return stale
        return {
            "id": cid,
            "days": d,
            "points": [],
            "error": str(e),
            "source": "none",
            "product": "Howl Charts",
            "updated": int(now),
            "note": _HOWL_CHARTS_NOTE,
        }


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
                    "User-Agent": "Howlscan/0.6.4 (+https://howlscan.org)",
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
                    "User-Agent": "HowlSearch/0.6.4 (+https://howlscan.org)",
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
                    "User-Agent": "HowlSearch/0.6.4 (+https://howlscan.org)",
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
                "User-Agent": "HowlDiscover/0.6.4",
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
<meta name="description" id="metaDesc" content="Howlscan — Howlcoin block explorer. Blocks, Play pots, culture NFTs, @names, network status."/>
<meta property="og:site_name" content="Howlscan"/>
<meta property="og:type" content="website" id="ogType"/>
<meta property="og:title" content="Howlscan — Howlcoin Block Explorer" id="ogTitle"/>
<meta property="og:description" content="Public Howlcoin explorer: chain health, Play board, culture gallery, @name profiles." id="ogDesc"/>
<meta property="og:url" content="https://howlscan.org/" id="ogUrl"/>
<meta property="og:image" content="https://howlscan.org/assets/howlcoin-logo-meme-pup-coin.jpg" id="ogImage"/>
<meta name="twitter:card" content="summary"/>
<meta name="twitter:title" content="Howlscan — Howlcoin Block Explorer" id="twTitle"/>
<meta name="twitter:description" content="Public Howlcoin explorer: chain health, Play, culture, @names." id="twDesc"/>
<title>Howlscan — Howlcoin Block Explorer</title>
<link rel="icon" href="/assets/howlcoin-logo-meme-pup-coin.jpg"/>
<link rel="canonical" href="https://howlscan.org/" id="canonicalLink"/>
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
    <button class="chipbtn" onclick="location.hash='#/play'">Play</button>
    <button class="chipbtn" onclick="location.hash='#/culture'">Culture</button>
    <button class="chipbtn" onclick="location.hash='#/contracts'">Contracts</button>
    <button class="chipbtn" onclick="location.hash='#/charts'">Charts</button>
    <button class="chipbtn" onclick="location.hash='#/health'">Network</button>
    <button class="chipbtn" onclick="location.hash='#/api'">API</button>
    <button class="chipbtn" onclick="location.hash='#/'+net+'/richlist'">Richlist</button>
    <button class="chipbtn" onclick="location.hash='#/'+net+'/mempool'">Mempool</button>
    <a class="chipbtn" href="/app" style="text-decoration:none;display:inline-flex;align-items:center;color:var(--green)">Wallet</a>
    <button class="chipbtn" style="border-color:var(--primary-border);color:var(--green)" onclick="location.hash='#/run'">Run a node</button>
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
  <button class="ditem primary" type="button" onclick="navTo('#/play')">🎮 Play</button>
  <button class="ditem" type="button" onclick="navTo('#/culture')">🖼 Culture</button>
  <button class="ditem" type="button" onclick="navTo('#/contracts')">📜 Contracts</button>
  <button class="ditem" type="button" onclick="navTo('#/charts')">📈 Charts</button>
  <button class="ditem" type="button" onclick="navTo('#/health')">💓 Network</button>
  <button class="ditem" type="button" onclick="navTo('#/api')">🔌 API docs</button>
  <button class="ditem" type="button" onclick="navTo('#/'+net+'/richlist')">🏆 Richlist</button>
  <button class="ditem" type="button" onclick="navTo('#/'+net+'/mempool')">⏳ Mempool</button>
  <button class="ditem" type="button" onclick="navTo('#/'+net+'/block/0')">🌱 Genesis</button>
  <h4 style="margin-top:16px">Get started</h4>
  <button class="ditem primary" type="button" onclick="navTo('#/run')">🐺 Run a node</button>
  <a class="ditem" href="/app">👛 Wallet</a>
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
    <input id="q" placeholder="Height, hash, tx, H…, @name, contract id" enterkeyhint="search" autocomplete="off"
      onkeydown="if(event.key==='Enter')doSearch()"/>
    <button type="button" onclick="doSearch()">Search</button>
  </div>
</div>
<div id="app"></div>
<div class="footer">
  <div>Howlscan · Scrypt PoW · not financial advice ·
    <a href="#/public">Home</a> ·
    <a href="#/play">Play</a> ·
    <a href="#/culture">Culture</a> ·
    <a href="#/contracts">Contracts</a> ·
    <a href="#/charts">Charts</a> ·
    <a href="#/health">Network</a> ·
    <a href="#/api">API</a> ·
    <a href="/app">Wallet</a> ·
    <a href="#/run">Run a node</a> ·
    <a href="#/public/richlist">Richlist</a> ·
    <a href="#/public/mempool">Mempool</a>
  </div>
  <div>API <span class="mono">/api/networks</span> · seed <span class="mono">147.182.223.204:42069</span> ·
    <a href="https://github.com/happyoils710/howlcoin" target="_blank" rel="noopener">Code</a>
  </div>
</div>
<nav class="bottom-nav" id="bottom-nav" aria-label="Primary">
  <button type="button" class="bnav-item" data-tab="home" onclick="goHome()"><span class="ico">⌂</span>Home</button>
  <button type="button" class="bnav-item" data-tab="play" onclick="location.hash='#/play'"><span class="ico">🎮</span>Play</button>
  <button type="button" class="bnav-item" data-tab="culture" onclick="location.hash='#/culture'"><span class="ico">🖼</span>Culture</button>
  <button type="button" class="bnav-item" data-tab="health" onclick="location.hash='#/health'"><span class="ico">💓</span>Net</button>
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

/** Current SPA route key for live refresh */
let __routeKey = '';
let __liveRefreshOn = true;

function setPageMeta(title, description, path){
  const t = title || 'Howlscan — Howlcoin Block Explorer';
  const d = description || 'Howlscan — public Howlcoin explorer.';
  try{
    document.title = t;
    const set = (id, val, attr)=>{
      const el = document.getElementById(id);
      if(!el) return;
      if(attr) el.setAttribute(attr, val);
      else el.setAttribute('content', val);
    };
    set('metaDesc', d);
    set('ogTitle', t);
    set('ogDesc', d);
    set('twTitle', t);
    set('twDesc', d);
    const url = 'https://howlscan.org/' + (path || location.hash || '');
    set('ogUrl', url);
    const can = document.getElementById('canonicalLink');
    if(can) can.setAttribute('href', url.split('#')[0] + (path && path.indexOf('#')===0 ? path : (location.hash||'')));
  }catch(e){}
}

function groupBlockTxs(txs){
  const groups = {
    mine: [],
    culture: [], // howl, name, nft, oracle culture
    contracts: [],
    transfers: [],
    other: [],
  };
  for(const t of (txs||[])){
    const m = txTypeMeta(t);
    const k = m.kind;
    if(k === 'mine') groups.mine.push(t);
    else if(k === 'howl' || k === 'name' || k === 'nft' || k === 'bond' || k === 'oracle') groups.culture.push(t);
    else if(k === 'contract') groups.contracts.push(t);
    else if(k === 'xfer') groups.transfers.push(t);
    else groups.other.push(t);
  }
  return groups;
}

function renderTxGroupCard(title, badge, list){
  if(!list || !list.length) return '';
  return `<div class="card" style="margin-top:12px">
    <h3>${esc(title)} <span class="badge ${badge}">${list.length}</span></h3>
    <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>Flow</th><th>Amount</th></tr></thead>
        <tbody>
          ${list.map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${t.txid?linkTx(t.txid):'—'}</td>
            <td>${txTypeBadge(t)}</td>
            <td class="mono" onclick="event.stopPropagation()">${txFlowHtml(t)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="mobile-only mlist">
      ${list.map(t=>{
        const m = txTypeMeta(t);
        return `<div class="mrow" onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
          <div class="ml"><div class="mt">${esc(m.label)}</div><div class="ms mono">${txFlowHtml(t)}</div></div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div></div>
        </div>`;
      }).join('')}
    </div>
  </div>`;
}
function linkContract(id){
  if(!id) return '—';
  return `<a class="mono" href="#/contract/${encodeURIComponent(id)}">${esc(short(id,14))}</a>`;
}
/** Human labels for L1 tx types */
function txTypeMeta(t){
  const ty = (t && t.type) || 'transfer';
  const key = String(t && (t.oracle_key || t.key) || '');
  if(ty === 'coinbase') return { label: 'Mining reward', badge: 'ok', kind: 'mine' };
  if(ty === 'nft_mint') return { label: 'NFT mint', badge: 'blue', kind: 'nft' };
  if(ty === 'nft_transfer') return { label: 'NFT send', badge: 'blue', kind: 'nft' };
  if(ty === 'contract_deploy'){
    const k = (t.contract_kind || t.kind || 'contract');
    return { label: 'Deploy · ' + k, badge: 'ok', kind: 'contract' };
  }
  if(ty === 'contract_call'){
    const m = t.method || 'call';
    return { label: 'Contract · ' + m, badge: 'blue', kind: 'contract' };
  }
  if(ty === 'oracle'){
    if(key === 'howl.howl' || key.startsWith('howl.howl.')) return { label: 'Howl', badge: 'ok', kind: 'howl' };
    if(key.startsWith('howl.name.')) return { label: 'Name claim', badge: 'ok', kind: 'name' };
    if(key.startsWith('howl.bond.')) return { label: 'Bark bond howl', badge: 'blue', kind: 'bond' };
    return { label: 'Oracle post', badge: 'blue', kind: 'oracle' };
  }
  if(ty === 'transfer') return { label: 'Transfer', badge: 'blue', kind: 'xfer' };
  return { label: ty, badge: 'blue', kind: 'other' };
}
function txTypeBadge(t){
  const m = txTypeMeta(t);
  return `<span class="badge ${m.badge}">${esc(m.label)}</span>`;
}
function txFlowHtml(t){
  const ty = (t && t.type) || 'transfer';
  if(ty === 'coinbase') return `new coins → ${linkAddr(t.to)}`;
  if(ty === 'nft_mint') return `mint ${esc(short(t.nft_id||t.name||'', 12))} → ${linkAddr(t.to||t.from)}`;
  if(ty === 'nft_transfer') return `${linkAddr(t.from)} → ${linkAddr(t.to)} · ${esc(short(t.nft_id||'',10))}`;
  if(ty === 'contract_deploy') return `${linkAddr(t.from)} deploys ${linkContract(t.contract_id)}`;
  if(ty === 'contract_call') return `${linkAddr(t.from)} → ${linkContract(t.contract_id)} · ${esc(t.method||'call')}`;
  if(ty === 'oracle'){
    const key = String(t.oracle_key || '');
    const val = String(t.oracle_value || t.value || '').slice(0, 40);
    if(key.startsWith('howl.name.')) return `${linkAddr(t.from)} claims @${esc(key.slice(10))}`;
    if(key === 'howl.howl' || key.startsWith('howl.howl.')) return `${linkAddr(t.from)} howls “${esc(val)}${val.length>=40?'…':''}”`;
    return `${linkAddr(t.from)} · ${esc(short(key,16))}`;
  }
  return `${linkAddr(t.from)} → ${linkAddr(t.to)}`;
}
function txDetailRows(t, d){
  const ty = (t && t.type) || 'transfer';
  const meta = txTypeMeta(t);
  let rows = `
    <div class="k">Status</div><div>${d.confirmed?('Block '+linkBlock(d.block_height)):'Unconfirmed · waiting for miner'}</div>
    <div class="k">Type</div><div>${txTypeBadge(t)} <span class="muted mono">${esc(ty)}</span></div>
    <div class="k">Fee</div><div>${fmtAmt(t.fee||0)} <span class="muted">→ miner who includes this tx</span></div>
    <div class="k">Nonce</div><div>${t.nonce??'—'}</div>`;
  if(ty === 'coinbase'){
    rows += `
      <div class="k">Source</div><div>Mining reward (new HOWL created)</div>
      <div class="k">Miner</div><div>${linkAddr(t.to)}</div>
      <div class="k">Reward</div><div class="amount">${fmtAmt(t.amount)}</div>`;
  } else if(ty === 'nft_mint' || ty === 'nft_transfer'){
    rows += `
      <div class="k">From</div><div>${linkAddr(t.from)}</div>
      <div class="k">To</div><div>${linkAddr(t.to)}</div>
      <div class="k">NFT</div><div class="mono">${esc(t.nft_id||'—')}${t.nft_id?copyBtn(t.nft_id):''}</div>
      <div class="k">Name</div><div>${esc(t.name||'—')}</div>
      ${t.uri?`<div class="k">URI</div><div class="mono" style="word-break:break-all">${esc(t.uri)}</div>`:''}
      <div class="k">Amount</div><div class="amount">${fmtAmt(t.amount||0)}</div>
      <div class="k">Memo</div><div>${esc(t.memo||'—')}</div>`;
  } else if(ty === 'contract_deploy' || ty === 'contract_call'){
    rows += `
      <div class="k">From</div><div>${linkAddr(t.from)}</div>
      <div class="k">Contract</div><div>${linkContract(t.contract_id)} ${t.contract_id?copyBtn(t.contract_id):''}</div>
      <div class="k">Kind / method</div><div>${esc(t.contract_kind||t.kind||'—')} · ${esc(t.method||(ty==='contract_deploy'?'deploy':'call'))}</div>
      <div class="k">Name</div><div>${esc(t.name||'—')}</div>
      <div class="k">Fund / value</div><div class="amount">${fmtAmt(t.amount||0)}</div>
      ${t.unlock_height!=null?`<div class="k">Unlock height</div><div>#${esc(String(t.unlock_height))}</div>`:''}
      <div class="k">Memo</div><div>${esc(t.memo||'—')}</div>`;
  } else if(ty === 'oracle'){
    rows += `
      <div class="k">Reporter</div><div>${linkAddr(t.from)}</div>
      <div class="k">Oracle key</div><div class="mono">${esc(t.oracle_key||'—')}</div>
      <div class="k">Value</div><div style="word-break:break-word">${esc(String(t.oracle_value||''))}</div>
      <div class="k">Source chain</div><div>${esc(t.source_chain||'howlcoin')}</div>
      <div class="k">Memo</div><div>${esc(t.memo||'—')}</div>`;
  } else {
    rows += `
      <div class="k">From</div><div>${linkAddr(t.from)}</div>
      <div class="k">To</div><div>${linkAddr(t.to)}</div>
      <div class="k">Amount</div><div class="amount">${fmtAmt(t.amount)}</div>
      <div class="k">Memo</div><div>${esc(t.memo||'—')}</div>`;
  }
  return rows;
}

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
  if(parts[0]==='play' || parts[0]==='city') return 'play';
  if(parts[0]==='culture' || parts[0]==='nfts' || parts[0]==='gallery') return 'culture';
  if(parts[0]==='health' || parts[0]==='status' || parts[0]==='charts') return 'health';
  if(parts[0]==='api' || parts[0]==='docs') return 'more';
  if(parts[0]==='run' || parts[0]==='node' || parts[0]==='sync') return 'more';
  if(parts[1]==='richlist' || parts[0]==='richlist') return 'more';
  if(parts[1]==='mempool' || parts[0]==='mempool') return 'more';
  if(parts[0]==='name' || (parts[1]==='name')) return 'play';
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
  const [blocks, txs, statusJ]=await Promise.all([
    api(`/api/${net}/blocks?limit=15`),
    api(`/api/${net}/txs?limit=15`),
    api(`/api/public/status?window=20`).catch(()=>({})),
  ]);
  const bl = blocks.blocks||[];
  const tl = txs.transactions||[];
  const cul = statusJ.culture || {};
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
        · <a href="#/health">Network</a>
        · <a href="#/play">Play</a>
        · <a href="#/culture">Culture</a>
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
  <div class="main" style="padding-bottom:4px;padding-top:0">
    <div class="card" style="padding:12px 14px">
      <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:8px">
        <b style="font-size:.95rem">Culture pulse</b>
        <span class="muted" style="font-size:.78rem">on-chain · live</span>
      </div>
      <div class="stats" style="margin:0;padding:0">
        <div class="stat" style="cursor:pointer" onclick="location.hash='#/play'">
          <div class="k">Open pots</div><div class="v">${cul.packpots_open??0}</div><div class="s">${cul.packpots??0} total · Play</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='#/play'">
          <div class="k">Howls</div><div class="v">${cul.howls??0}</div><div class="s">posts</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='#/play'">
          <div class="k">Names</div><div class="v">${cul.names??0}</div><div class="s">@handles</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='#/culture'">
          <div class="k">NFTs</div><div class="v">${cul.nfts??0}</div><div class="s">${cul.tipjars??0} tip jars</div></div>
      </div>
    </div>
  </div>
  <div class="main" style="padding-bottom:8px">
    <div class="quick-row">
      <button class="chipbtn" style="border-color:rgba(61,255,154,.45);color:var(--green)" onclick="location.hash='#/city'">Howl City ●</button>
      <button class="chipbtn" onclick="location.hash='#/play'">Play board</button>
      <button class="chipbtn" onclick="location.hash='#/culture'">Culture gallery</button>
      <button class="chipbtn" onclick="location.hash='#/charts'">Charts</button>
      <button class="chipbtn" onclick="location.hash='#/health'">Network status</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/block/${s.height}'">Latest #${s.height}</button>
      <button class="chipbtn" onclick="location.hash='#/run'">Run a node</button>
    </div>
  </div>
  <div class="main" style="padding-top:0;padding-bottom:8px" id="homeCityPulse">
    <div class="card" style="border-color:rgba(61,255,154,.28)">
      <h3 style="margin:0;padding:12px 14px;border-bottom:1px solid var(--border)">Howl City <a class="more" href="#/city">live feed →</a></h3>
      <div class="mlist" id="homeCityList"><div class="mrow"><div class="muted">Loading city…</div></div></div>
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
            <td>${txTypeBadge(t)}</td>
            <td class="mono" onclick="event.stopPropagation()">${txFlowHtml(t)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${t.confirmed?`<span class="badge ok" onclick="event.stopPropagation();location.hash='#/${net}/block/${t.block_height}'">#${t.block_height}</span>`:`<span class="badge warn">waiting for miner</span>`}</td>
          </tr>`).join('') || '<tr><td colspan="5" class="muted" style="padding:16px">No transactions yet</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${tl.map(t=>{
          const m = txTypeMeta(t);
          return `<div class="mrow" onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
          <div class="ml">
            <div class="mt">${esc(m.label)} <span class="badge ${t.confirmed?'ok':'warn'}" style="margin-left:4px">${t.confirmed?'#'+t.block_height:'waiting'}</span></div>
            <div class="ms mono">${txFlowHtml(t)}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div></div>
        </div>`;
        }).join('')||'<div class="mrow"><div class="muted">No transactions yet</div></div>'}
      </div>
    </div>
  </div>`;
  // Howl City pulse on home (async so blocks/txs paint first)
  fillHomeCityList();
}

async function fillHomeCityList(){
  const el = document.getElementById('homeCityList');
  if(!el) return;
  try{
    const j = await api(`/api/${net}/city?limit=10`);
    const events = j.events || [];
    if(!events.length){
      el.innerHTML = '<div class="mrow"><div class="muted">Quiet city — <a href="#/city">open live feed</a></div></div>';
      return;
    }
    el.innerHTML = events.map(cityEventRow).join('');
  }catch(e){
    el.innerHTML = '<div class="mrow"><div class="muted">City feed unavailable · <a href="#/city">retry</a></div></div>';
  }
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
  const g = groupBlockTxs(txs);
  const cultureN = g.culture.length + g.contracts.length;
  setPageMeta(
    'Block #'+h+' · Howlscan',
    'Howlcoin block #'+h+' — '+txs.length+' txs, '+cultureN+' culture/contract events.',
    '#/'+net+'/block/'+h
  );
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
      <div class="stat"><div class="k">Txs</div><div class="v">${txs.length}</div><div class="s">${g.transfers.length} transfer · ${cultureN} culture</div></div>
      <div class="stat"><div class="k">Reward</div><div class="v" style="font-size:.95rem">${fmtAmt(cb&&cb.amount)}</div><div class="s">miner ${cb&&cb.to?esc(short(cb.to,10)):'—'}</div></div>
    </div>
    <div class="card detail">
      <div class="badge blue">Block</div>
      <span class="badge ok" style="margin-left:6px">Verified on Howlcoin</span>
      ${cultureN?`<span class="badge ok" style="margin-left:6px">${cultureN} culture</span>`:''}
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
        <div class="k">Breakdown</div><div>mine ${g.mine.length} · culture ${g.culture.length} · contracts ${g.contracts.length} · transfers ${g.transfers.length}</div>
      </div>
    </div>
    ${renderTxGroupCard('Mining reward', 'ok', g.mine)}
    ${renderTxGroupCard('Culture · howls · names · NFTs', 'ok', g.culture)}
    ${renderTxGroupCard('Contracts', 'blue', g.contracts)}
    ${renderTxGroupCard('Transfers', 'blue', g.transfers)}
    ${renderTxGroupCard('Other', 'blue', g.other)}
    ${!txs.length?`<div class="card" style="margin-top:12px"><p class="muted" style="padding:16px">No transactions in this block</p></div>`:''}
  </div>`;
}

async function showTx(id){
  setHeroVisible(false);
  setBottomTab('home');
  await loadNetworks();
  const d=await api(`/api/${net}/tx/${encodeURIComponent(id)}`);
  const t=d.tx || {};
  const meta = txTypeMeta(t);
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label: meta.label || 'Transaction'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      ${d.confirmed?`<button class="chipbtn" onclick="location.hash='#/${net}/block/${d.block_height}'">Block #${d.block_height}</button>`:
        `<button class="chipbtn" onclick="location.hash='#/${net}/mempool'">Mempool</button>`}
      ${t.contract_id?`<button class="chipbtn" onclick="location.hash='#/contract/${encodeURIComponent(t.contract_id)}'">Contract</button>`:''}
    </div>
    <div class="card detail" style="margin-top:4px">
      <div class="badge ${d.confirmed?'ok':'warn'}">${d.confirmed?'Confirmed':'Waiting for miner'}</div>
      ${txTypeBadge(t)}
      <h2 style="margin:8px 0 4px;font-size:1.25rem">${esc(meta.label)}</h2>
      <div class="mono">${esc(t.txid||id)}${copyBtn(t.txid||id)}</div>
      ${!d.confirmed?`<p class="muted" style="margin:10px 0 0;font-size:.88rem">In mempool — a miner must include this tx in a block (~${60}s target).</p>`:''}
      <div class="kv" style="margin-top:12px">
        ${txDetailRows(t, d)}
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
  let onName = null;
  try{
    const nm = await api(`/api/${net}/names?address=${encodeURIComponent(d.address||addr)}`);
    onName = nm.name || nm.name_display || null;
    if(onName && String(onName).startsWith('@')) onName = String(onName).slice(1);
  }catch(e){}
  const known = {
    'HOWL_GENESIS_BURN': 'Genesis burn',
  };
  const tag = known[d.address] || (onName ? '@'+onName : (String(d.address||'').startsWith('H') ? 'Howlcoin address' : 'Address'));
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
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Richlist',href:'#/'+net+'/richlist'},{label:onName?('@'+onName):'Address'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/richlist'">Richlist</button>
      ${onName?`<button class="chipbtn" onclick="location.hash='#/name/${encodeURIComponent(onName)}'">@${esc(onName)} profile</button>`:''}
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
      <h2 style="margin:8px 0 4px;font-size:1.25rem">${onName?('@'+esc(onName)):'Wallet'}</h2>
      <div class="mono">${esc(d.address)}${copyBtn(d.address)}</div>
      ${spark}
      <div class="kv" style="margin-top:12px">
        <div class="k">Balance</div><div class="amount" style="font-size:1.25rem">${esc(d.balance_fmt)}</div>
        <div class="k">Nonce</div><div>${d.nonce}</div>
        <div class="k">Shown txs</div><div>${d.tx_count}</div>
        ${onName?`<div class="k">Name</div><div>${linkName(onName)}</div>`:''}
      </div>
    </div>
    <div id="addrCulture" class="main cols" style="padding:12px 0 0;margin:0"><div class="card"><p class="muted" style="padding:12px">Loading culture…</p></div></div>
    <div class="card" style="margin-top:12px">
      <h3>History</h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Type</th><th>Txid</th><th>Amount</th><th>Block</th></tr></thead>
        <tbody>
          ${hist.map(t=>`<tr onclick="${t.txid?`location.hash='#/${net}/tx/${encodeURIComponent(t.txid)}'`:''}">
            <td>${txTypeBadge(t)} ${t.direction?`<span class="muted">${esc(t.direction)}</span>`:''}</td>
            <td>${t.txid?linkTx(t.txid):'—'}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${t.block_height!=null?linkBlock(t.block_height):'—'}</td>
          </tr>`).join('')||'<tr><td colspan="4" class="muted" style="padding:16px">No transactions</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${hist.map(t=>{
          const m = txTypeMeta(t);
          return `<div class="mrow" onclick="${t.txid?`location.hash='#/${net}/tx/${encodeURIComponent(t.txid)}'`:''}">
          <div class="ml">
            <div class="mt">${txTypeBadge(t)}${t.block_height!=null?' · #'+t.block_height:''}</div>
            <div class="ms mono">${t.txid?esc(short(t.txid,14)):'—'} · ${esc(m.label)}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div></div>
        </div>`;
        }).join('')||'<div class="mrow"><div class="muted">No transactions</div></div>'}
      </div>
    </div>
  </div>`;
  // async culture enrichment
  try{
    const a = d.address || addr;
    const [nfts, pots, tips, howls] = await Promise.all([
      api(`/api/${net}/nfts?owner=${encodeURIComponent(a)}&limit=12`).then(j=>j.nfts||[]).catch(()=>[]),
      api(`/api/${net}/contracts?kind=packpot&limit=40`).then(j=>(j.contracts||[]).filter(c=>c.owner===a||c.last_joiner===a)).catch(()=>[]),
      api(`/api/${net}/contracts?kind=tipjar&limit=40`).then(j=>(j.contracts||[]).filter(c=>c.owner===a)).catch(()=>[]),
      api(`/api/${net}/howls?limit=40`).then(j=>(j.howls||[]).filter(h=>(h.from||h.reporter)===a).slice(0,8)).catch(()=>[]),
    ]);
    const el = document.getElementById('addrCulture');
    if(el){
      el.innerHTML = `
      <div class="card">
        <h3>Culture · NFTs (${nfts.length})</h3>
        <div class="mlist">${nfts.length?nfts.map(n=>`<div class="mrow"><div class="ml"><div class="mt">${esc(n.name||'NFT')}</div><div class="ms mono">#${esc(String(n.mint_height??'—'))}</div></div></div>`).join(''):'<div class="mrow"><div class="muted">No NFTs</div></div>'}</div>
      </div>
      <div class="card">
        <h3>Contracts · pots/tips</h3>
        <div class="mlist">${[...pots,...tips].length?[...pots,...tips].map(c=>`<div class="mrow" onclick="location.hash='#/contract/${encodeURIComponent(c.contract_id||'')}'" style="cursor:pointer">
          <div class="ml"><div class="mt">${esc(c.name||c.kind)} <span class="badge blue">${esc(c.kind||'')}</span></div>
          <div class="ms">${esc(c.balance_fmt||'')}</div></div>
        </div>`).join(''):'<div class="mrow"><div class="muted">No contracts</div></div>'}</div>
        <h3 style="margin-top:12px">Howls</h3>
        <div class="mlist">${howls.length?howls.map(e=>`<div class="mrow"><div class="ml"><div class="mt">🐺 ${esc(String(e.message||e.value||''))}</div><div class="ms">#${esc(String(e.height??'—'))}</div></div></div>`).join(''):'<div class="mrow"><div class="muted">No howls</div></div>'}</div>
      </div>`;
    }
  }catch(e){}
}

async function showRichlist(){
  setHeroVisible(false);
  setBottomTab('more');
  await loadNetworks();
  const d=await api(`/api/${net}/richlist?limit=50`);
  const rows = d.richlist||[];
  let nameMap = {};
  try{
    const nj = await api(`/api/${net}/names?limit=200`);
    for(const r of (nj.names||[])){
      if(r.address && r.name) nameMap[r.address] = r.name;
    }
  }catch(e){}
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Richlist'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/play'">Play</button></div>
    <div class="card" style="margin-top:4px">
      <h3>Top addresses</h3>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>#</th><th>Address</th><th>Name</th><th>Balance</th></tr></thead>
        <tbody>
          ${rows.map(r=>{
            const nm = nameMap[r.address];
            return `<tr onclick="location.hash='#/${net}/address/${encodeURIComponent(r.address)}'">
            <td>${r.rank}</td>
            <td onclick="event.stopPropagation()">${linkAddr(r.address)}</td>
            <td onclick="event.stopPropagation()">${nm?linkName(nm):'<span class="muted">—</span>'}</td>
            <td class="amount">${esc(r.balance_fmt)}</td>
          </tr>`;
          }).join('')||'<tr><td colspan="4" class="muted" style="padding:16px">No balances</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${rows.map(r=>{
          const nm = nameMap[r.address];
          return `<div class="mrow" onclick="location.hash='#/${net}/address/${encodeURIComponent(r.address)}'">
          <div class="ml">
            <div class="mt">#${r.rank} ${nm?'<span style="color:var(--green)">@'+esc(nm)+'</span>':'<span class="mono" style="font-weight:500">'+esc(short(r.address,12))+'</span>'}</div>
            <div class="ms mono">${esc(short(r.address,18))}</div>
          </div>
          <div class="mr"><div class="ma">${esc(r.balance_fmt)}</div></div>
        </div>`;
        }).join('')||'<div class="mrow"><div class="muted">No balances</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showMempool(){
  setHeroVisible(false);
  setBottomTab('more');
  await loadNetworks();
  const d=await api(`/api/${net}/mempool`);
  const rows = d.transactions||[];
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Mempool'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="showMempool()">↻ Refresh</button></div>
    <div class="card" style="margin-top:4px">
      <h3>Mempool <span class="badge warn">${d.count||0} waiting for miner</span></h3>
      <p class="muted" style="padding:0 14px 8px;font-size:.85rem">Pending howls, joins, mints, and transfers — confirmed when a miner includes them.</p>
      <div class="desktop-only table-wrap">
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>Flow</th><th>Amount</th><th>Fee</th></tr></thead>
        <tbody>
          ${rows.map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${linkTx(t.txid)}</td>
            <td>${txTypeBadge(t)}</td>
            <td class="mono" onclick="event.stopPropagation()">${txFlowHtml(t)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${fmtAmt(t.fee||0)}</td>
          </tr>`).join('')||'<tr><td colspan="5" class="muted" style="padding:16px">Mempool empty — chain is quiet</td></tr>'}
        </tbody>
      </table>
      </div>
      <div class="mobile-only mlist">
        ${rows.map(t=>{
          const m = txTypeMeta(t);
          return `<div class="mrow" onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
          <div class="ml">
            <div class="mt">${esc(m.label)} <span class="badge warn">waiting</span></div>
            <div class="ms mono">${txFlowHtml(t)}</div>
          </div>
          <div class="mr"><div class="ma">${fmtAmt(t.amount)}</div><div class="ms">fee ${fmtAmt(t.fee||0)}</div></div>
        </div>`;
        }).join('')||'<div class="mrow"><div class="muted">Mempool empty</div></div>'}
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
  setBottomTab('health');
  await loadNetworks();
  let h={};
  try{ h = await api('/api/public/status?window=48'); }catch(e){
    try{ h = await api('/api/public/health?window=48'); }catch(e2){ h={error:e2.message}; }
  }
  setPageMeta(
    'Network status · Howlscan',
    `Howlcoin network: height ${h.height??'—'}, tip age ${h.tip_age_seconds??'—'}s, status ${h.status||'—'}.`,
    '#/health'
  );
  const series = h.series || [];
  const blockTimes = series.map(x=>x.block_time).filter(v=>v!=null);
  const diffs = series.map(x=>x.difficulty_float || 0);
  const age = h.tip_age_seconds;
  const ageTxt = age==null ? '—' : (age>=3600 ? (age/3600).toFixed(1)+'h' : age>=60 ? Math.round(age/60)+'m' : age+'s');
  const statusBadge = h.status==='ok' ? 'ok' : (h.status==='slow' ? 'warn' : 'warn');
  const statusLabel = h.status==='ok' ? 'HEALTHY' : (h.status==='slow' ? 'SLOW' : (h.status==='stalled' ? 'STALLED' : 'UNKNOWN'));
  const cul = h.culture || {};
  const samp = h.charts_sampler || {};
  const hps = h.est_network_hashrate;
  const hpsTxt = hps!=null ? (hps>=1e6 ? (hps/1e6).toFixed(2)+' MH/s' : hps>=1e3 ? (hps/1e3).toFixed(1)+' kH/s' : Math.round(hps)+' H/s') : '—';
  const seedHps = h.seed_hashrate;
  const seedTxt = seedHps!=null ? Math.round(seedHps)+' H/s' : '—';
  const sampAge = samp.sample_age_seconds;
  const sampAgeTxt = sampAge==null ? (samp.exists ? '—' : 'not installed') :
    (sampAge<120 ? sampAge+'s' : sampAge<3600 ? Math.round(sampAge/60)+'m' : (sampAge/3600).toFixed(1)+'h');
  app().innerHTML=`<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/public'},{label:'Network status'}])}
    <div class="page-actions"><button class="back" onclick="location.hash='#/public'">← Home</button>
      <button class="chipbtn" onclick="showHealth()">Refresh</button>
      <button class="chipbtn" onclick="location.hash='#/run'">Run a node</button>
      <button class="chipbtn" onclick="location.hash='#/app'">Open wallet</button>
    </div>
    <div class="card detail">
      <div class="badge ${statusBadge}">${statusLabel}</div>
      <span class="badge blue" style="margin-left:6px">v${esc(String(h.version||'—'))}</span>
      <h2 style="margin:8px 0 6px">Network status</h2>
      <p class="muted" style="margin:0 0 12px">Ops theater — live L1 metrics for Howlcoin. No ads, no trackers.</p>
      <div class="stats">
        <div class="stat"><div class="k">Height</div><div class="v">${h.height??'—'}</div><div class="s">tip</div></div>
        <div class="stat"><div class="k">Tip age</div><div class="v" style="font-size:1rem">${esc(ageTxt)}</div><div class="s">target ${h.target_block_time||60}s</div></div>
        <div class="stat"><div class="k">Est. hashrate</div><div class="v" style="font-size:.95rem">${esc(hpsTxt)}</div><div class="s">from work / block time</div></div>
        <div class="stat"><div class="k">Mempool</div><div class="v">${h.mempool??'—'}</div><div class="s">pending</div></div>
      </div>
      <div class="kv" style="margin-top:12px">
        <div class="k">Difficulty</div><div>${esc(String(h.difficulty_label||'—'))}</div>
        <div class="k">Next work</div><div>${esc(String(h.next_difficulty_label||'—'))} · ~${esc(String(h.expected_hashes_next!=null?Math.round(h.expected_hashes_next):'—'))} hashes</div>
        <div class="k">Avg block</div><div>${h.avg_block_time!=null?(h.avg_block_time).toFixed(0)+'s':'—'} · window ${h.window||'—'}</div>
        <div class="k">Seed mine</div><div>${esc(seedTxt)}${h.seed_mining?' · active':''}</div>
        <div class="k">Addresses</div><div>${esc(String(h.addresses??'—'))} · circ ${esc(String(h.circulating||'—'))}</div>
        <div class="k">Charts sampler</div><div>${samp.exists===false?'offline':(esc(String(samp.assets??'—'))+' assets · '+esc(String(samp.points??'—'))+' pts · age '+esc(sampAgeTxt))}</div>
      </div>
    </div>
    <div class="card detail" style="margin-top:12px">
      <h3 style="margin-top:0">Culture on-chain</h3>
      <p class="muted" style="margin:0 0 10px">Live counts from the public ledger — pots, howls, names, NFTs.</p>
      <div class="stats">
        <div class="stat"><div class="k">Open pots</div><div class="v">${cul.packpots_open??0}</div><div class="s">${cul.packpots??0} total</div></div>
        <div class="stat"><div class="k">Howls</div><div class="v">${cul.howls??0}</div><div class="s">posts</div></div>
        <div class="stat"><div class="k">Names</div><div class="v">${cul.names??0}</div><div class="s">@handles</div></div>
        <div class="stat"><div class="k">NFTs</div><div class="v">${cul.nfts??0}</div><div class="s">${cul.tipjars??0} tip jars</div></div>
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
      <p class="muted" style="margin:0">Seed self-heals: auto-mine, 90s template refresh, 2h stall relief. Monitors:
      <span class="mono">scripts/howl-health-check.sh</span> ·
      <span class="mono">scripts/howl-ops-bootstrap.sh</span> · API
      <span class="mono">/api/public/status</span></p>
    </div>
  </div>`;
}

async function showRunNode(){
  setHeroVisible(false);
  setBottomTab('more');
  await loadNetworks();
  let height='?', protocol='0.6-smooth-diff', version='0.6.4', dLabel='—', smoothH=120;
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

function fmtAmtShort(howlies){
  if(howlies==null) return '—';
  const n = Number(howlies) / 1e8;
  if(!isFinite(n)) return String(howlies);
  if(n >= 1e6) return (n/1e6).toFixed(2)+'M HOWL';
  if(n >= 1e3) return (n/1e3).toFixed(2)+'k HOWL';
  return n.toFixed(n >= 1 ? 2 : 4) + ' HOWL';
}
function linkName(slug){
  if(!slug) return '—';
  const s = String(slug).replace(/^@/,'');
  return `<a href="#/name/${encodeURIComponent(s)}">@${esc(s)}</a>`;
}
function sparkPts(points, w, h){
  const vals = (points||[]).map(p=>Number(p.p)).filter(v=>isFinite(v));
  if(vals.length < 2) return '';
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = (max - min) || 1;
  return vals.map((v,i)=>{
    const x = (i/(vals.length-1)) * (w-8) + 4;
    const y = h - 4 - ((v - min) / span) * (h-12);
    return x.toFixed(1)+','+y.toFixed(1);
  }).join(' ');
}

function cityKindEmoji(k){
  const m = {block:'⬛', howl:'🐺', name:'@', nft:'🖼', pot:'🍯', tip:'☕', bond:'🔗', mine:'⛏', transfer:'⇄', contract:'📜', oracle:'📡', other:'·'};
  return m[k] || '·';
}
function cityEventRow(ev){
  const pending = ev.pending ? ' <span class="badge warn">waiting</span>' : '';
  const h = ev.pending ? 'mempool' : ('#' + (ev.height ?? '—'));
  const when = ev.timestamp ? ago(ev.timestamp) : (ev.pending ? 'now' : '—');
  let body = esc(ev.label || ev.kind || 'event');
  if(ev.kind === 'howl' && ev.message) body = '🐺 “' + esc(String(ev.message).slice(0,60)) + (String(ev.message).length>60?'…':'') + '”';
  if(ev.kind === 'name' && ev.message) body = 'claimed ' + linkName(ev.message);
  if(ev.kind === 'block') body = 'Block #' + esc(String(ev.height??'—')) + (ev.to ? ' · miner ' + linkAddr(ev.to) : '');
  if(ev.kind === 'pot' && ev.meta && ev.meta.contract_id) body = esc(ev.label) + ' · ' + linkContract(ev.meta.contract_id);
  if(ev.kind === 'tip' && ev.meta && ev.meta.contract_id) body = esc(ev.label) + ' · ' + linkContract(ev.meta.contract_id);
  if(ev.kind === 'nft' && ev.message) body = esc(ev.label) + ' · ' + esc(String(ev.message).slice(0,40));
  const who = ev.from ? linkAddr(ev.from) : (ev.to && ev.kind==='mine' ? linkAddr(ev.to) : '');
  const amt = (ev.amount!=null && Number(ev.amount)>0) ? fmtAmt(ev.amount) : '';
  const click = ev.txid ? `location.hash='#/${net}/tx/${encodeURIComponent(ev.txid)}'` :
    (ev.kind==='block' && ev.height!=null ? `location.hash='#/${net}/block/${ev.height}'` : '');
  return `<div class="mrow"${click?` onclick="${click}" style="cursor:pointer"`:''}>
    <div class="ml">
      <div class="mt">${cityKindEmoji(ev.kind)} ${body}${pending}</div>
      <div class="ms">${who?who+' · ':''}${esc(h)} · ${esc(when)}${amt?' · <span class="amount">'+amt+'</span>':''}</div>
    </div>
  </div>`;
}

async function showHowlCity(filterKind){
  setHeroVisible(false);
  setBottomTab('play');
  await loadNetworks();
  const fk = String(filterKind || '').toLowerCase().replace(/[^a-z]/g,'');
  const kindsQ = fk ? ('&kinds=' + encodeURIComponent(fk)) : '';
  let events = [], cul = {}, height = '—';
  try{
    const j = await api(`/api/${net}/city?limit=60${kindsQ}`);
    events = j.events || [];
    cul = j.culture || {};
    height = j.height != null ? j.height : '—';
  }catch(e){}
  setPageMeta(
    'Howl City · live feed · Howlscan',
    `Watch Howlcoin breathe: blocks, howls, pots, names, NFTs. Height ${height}.`,
    '#/city'
  );
  const filters = [
    ['', 'All'],
    ['howl', 'Howls'],
    ['pot', 'Pots'],
    ['name', 'Names'],
    ['nft', 'NFTs'],
    ['block', 'Blocks'],
    ['tip', 'Tips'],
  ];
  const cityHash = (k)=> k ? `#/city/${k}` : '#/city';
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Howl City'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/play'">Play board</button>
      <button class="chipbtn" onclick="copyText('https://howlscan.org/#/city', this)">Share city</button>
      <a class="chipbtn" href="/app?howl=1" style="text-decoration:none;color:var(--green)">Howl in wallet</a>
      <button class="chipbtn" onclick="showHowlCity(${JSON.stringify(fk)})">↻</button>
      <span class="muted" style="font-size:.72rem;align-self:center">● live · 15s</span>
    </div>
    <div class="card detail" style="border-color:rgba(61,255,154,.35)">
      <div class="badge ok">HOWL CITY</div>
      <span class="badge ok" style="margin-left:6px">● live</span>
      <h2 style="margin:8px 0 6px">The chain is howling</h2>
      <p class="muted" style="margin:0 0 10px">Live L1 feed — blocks, howls, pack pots, @names, NFTs. No wallet needed to watch. Join the action in the <a href="/app">app</a>.</p>
      <div class="stats" style="margin:0">
        <div class="stat" style="cursor:pointer" onclick="location.hash='#/health'"><div class="k">Height</div><div class="v">${esc(String(height))}</div><div class="s">tip</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='${cityHash('pot')}'"><div class="k">Open pots</div><div class="v">${cul.packpots_open??0}</div><div class="s">${cul.packpots??0} total</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='${cityHash('howl')}'"><div class="k">Howls</div><div class="v">${cul.howls??0}</div><div class="s">posts</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='${cityHash('name')}'"><div class="k">Names</div><div class="v">${cul.names??0}</div><div class="s">@handles</div></div>
        <div class="stat" style="cursor:pointer" onclick="location.hash='#/culture'"><div class="k">NFTs</div><div class="v">${cul.nfts??0}</div><div class="s">culture</div></div>
      </div>
      <div class="quick-row" style="margin-top:12px">
        ${filters.map(([k,lab])=>`<button class="chipbtn ${k===fk?'active':''}" onclick="location.hash='${cityHash(k)}'">${esc(lab)}</button>`).join('')}
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>Live feed <span class="badge warn">${events.length}</span></h3>
      <div class="mlist" id="cityFeed">
        ${events.length?events.map(cityEventRow).join(''):'<div class="mrow"><div class="muted">Quiet city — mine a block, post a howl, or open a pot.</div></div>'}
      </div>
    </div>
    <div class="card detail" style="margin-top:12px">
      <h3 style="margin-top:0">Get in the city</h3>
      <p class="muted" style="margin:0 0 10px">Claim an @name, howl, join a pot, mint culture — all on Howlcoin L1.</p>
      <div class="quick-row">
        <a class="chipbtn" href="/app" style="text-decoration:none;color:var(--green)">Open wallet</a>
        <a class="chipbtn" href="/app?howl=1" style="text-decoration:none;color:var(--cyan)">Post a howl</a>
        <button class="chipbtn" onclick="location.hash='#/play'">Play board</button>
        <button class="chipbtn" onclick="location.hash='#/run'">Run a node</button>
        <button class="chipbtn" onclick="copyText('https://howlscan.org/#/city', this)">Copy city link</button>
      </div>
    </div>
  </div>`;
}

async function showPlayBoard(){
  setHeroVisible(false);
  setBottomTab('play');
  await loadNetworks();
  let pots=[], howls=[], tips=[], names=[], st={}, city=[];
  try{
    [pots, howls, tips, names, st, city] = await Promise.all([
      api(`/api/${net}/contracts?kind=packpot&limit=30`).then(j=>j.contracts||[]).catch(()=>[]),
      api(`/api/${net}/howls?limit=25`).then(j=>j.howls||[]).catch(()=>[]),
      api(`/api/${net}/contracts?kind=tipjar&limit=20`).then(j=>j.contracts||[]).catch(()=>[]),
      api(`/api/${net}/names?limit=20`).then(j=>j.names||[]).catch(()=>[]),
      api('/api/public/status?window=12').catch(()=>({})),
      api(`/api/${net}/city?limit=12`).then(j=>j.events||[]).catch(()=>[]),
    ]);
  }catch(e){}
  const cul = st.culture || {};
  const hNow = st.height != null ? Number(st.height) : null;
  const openPots = pots.filter(c => (c.status||'active')==='active');
  setPageMeta(
    'Play board · Howlscan',
    `Howlcoin Play: ${cul.packpots_open??openPots.length} open pots, ${cul.howls??howls.length} howls, ${cul.names??names.length} names.`,
    '#/play'
  );
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Play'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" style="border-color:var(--green);color:var(--green)" onclick="location.hash='#/city'">Howl City ●</button>
      <button class="chipbtn" onclick="location.hash='#/culture'">Culture</button>
      <button class="chipbtn" onclick="location.hash='#/app'">Open wallet to act</button>
      <button class="chipbtn" onclick="showPlayBoard()">↻ Refresh</button>
      <span class="muted" style="font-size:.72rem;align-self:center" id="liveHint">Live · 15s</span>
    </div>
    ${city.length?`<div class="card" style="margin-bottom:12px;border-color:rgba(61,255,154,.3)">
      <h3 style="margin:0;padding:12px 14px;border-bottom:1px solid var(--border)">City pulse <a class="more" href="#/city">full feed →</a></h3>
      <div class="mlist">${city.slice(0,8).map(cityEventRow).join('')}</div>
    </div>`:''}
    <div class="card detail">
      <div class="badge ok">PLAY</div>
      <span class="badge ok" style="margin-left:6px" id="playLiveDot">● live</span>
      <h2 style="margin:8px 0 6px">Howlcoin Play board</h2>
      <p class="muted" style="margin:0 0 10px">Public view of on-chain pots, howls, tip jars, and names. Auto-refreshes. To join, howl, or tip — use the <a href="/app">wallet</a>.</p>
      <div class="stats" style="margin:0">
        <div class="stat"><div class="k">Open pots</div><div class="v">${cul.packpots_open??openPots.length}</div><div class="s">of ${cul.packpots??pots.length}</div></div>
        <div class="stat"><div class="k">Howls</div><div class="v">${cul.howls??howls.length}</div><div class="s">posts</div></div>
        <div class="stat"><div class="k">Names</div><div class="v">${cul.names??names.length}</div><div class="s">@handles</div></div>
        <div class="stat"><div class="k">Tip jars</div><div class="v">${cul.tipjars??tips.length}</div><div class="s">active</div></div>
      </div>
    </div>
    <div class="main cols" style="padding:12px 0 0;margin:0">
      <div class="card">
        <h3>Pack pots <a class="more" href="/app#play">act in wallet →</a></h3>
        <div class="mlist">
          ${openPots.length?openPots.map(c=>{
            const uh = Number(c.unlock_height||0);
            const left = hNow!=null && uh ? uh - hNow : null;
            const open = left==null || left > 0;
            return `<div class="mrow">
              <div class="ml">
                <div class="mt">${esc(c.name||'Pack pot')} <span class="badge ${open?'ok':'warn'}">${open?'open':'claim'}</span></div>
                <div class="ms">unlock #${esc(String(c.unlock_height||'—'))}${left!=null && left>0?` · ~${left} blocks`:''} · joins ${esc(String(c.join_count||0))}</div>
                <div class="ms mono">last ${c.last_joiner?linkAddr(c.last_joiner):'—'}</div>
              </div>
              <div class="mr"><div class="ma">${esc(c.balance_fmt||'0')}</div></div>
            </div>`;
          }).join(''):`<div class="mrow"><div class="muted">No open pots yet — deploy one in the wallet Play hub.</div></div>`}
        </div>
      </div>
      <div class="card">
        <h3>Howl feed</h3>
        <div class="mlist">
          ${howls.length?howls.map(e=>`<div class="mrow">
            <div class="ml">
              <div class="mt">🐺 ${esc(String(e.message||e.value||''))}</div>
              <div class="ms">${e.from||e.reporter?linkAddr(e.from||e.reporter):'—'} · ${e.pending?'mempool':'#'+esc(String(e.height??'—'))}</div>
            </div>
          </div>`).join(''):`<div class="mrow"><div class="muted">No howls yet.</div></div>`}
        </div>
      </div>
    </div>
    <div class="main cols" style="padding:12px 0 0;margin:0">
      <div class="card">
        <h3>Tip jars</h3>
        <div class="mlist">
          ${(tips.filter(t=>(t.status||'active')==='active')).length?
            tips.filter(t=>(t.status||'active')==='active').map(c=>`<div class="mrow">
              <div class="ml">
                <div class="mt">${esc(c.name||'Tip jar')}</div>
                <div class="ms">owner ${c.owner?linkAddr(c.owner):'—'}</div>
              </div>
              <div class="mr"><div class="ma">${esc(c.balance_fmt||'0')}</div></div>
            </div>`).join(''):`<div class="mrow"><div class="muted">No tip jars yet.</div></div>`}
        </div>
      </div>
      <div class="card">
        <h3>Names directory</h3>
        <div class="mlist">
          ${names.length?names.map(r=>`<div class="mrow" onclick="location.hash='#/name/${encodeURIComponent(r.name||'')}'" style="cursor:pointer">
            <div class="ml">
              <div class="mt">${linkName(r.name)}</div>
              <div class="ms mono">${esc(short(r.address,14))} · #${esc(String(r.height??'—'))}</div>
            </div>
            <div class="mr"><div class="ms">profile →</div></div>
          </div>`).join(''):`<div class="mrow"><div class="muted">No names claimed yet.</div></div>`}
        </div>
      </div>
    </div>
  </div>`;
}

async function showCultureGallery(){
  setHeroVisible(false);
  setBottomTab('culture');
  await loadNetworks();
  let nfts=[], events=[], cul={};
  try{
    const [nj, ej, st] = await Promise.all([
      api(`/api/${net}/nfts?limit=48`).catch(()=>({nfts:[]})),
      api(`/api/${net}/nft-events?limit=30`).catch(()=>({events:[]})),
      api('/api/public/status').catch(()=>({})),
    ]);
    nfts = nj.nfts || [];
    events = ej.events || [];
    cul = st.culture || {};
  }catch(e){}
  setPageMeta(
    'Culture gallery · Howlscan',
    `${cul.nfts??nfts.length} Howlcoin NFTs on-chain — mint-from-howl and photo mints.`,
    '#/culture'
  );
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Culture'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/play'">Play</button>
      <button class="chipbtn" onclick="location.hash='#/app'">Mint in wallet</button>
      <button class="chipbtn" onclick="showCultureGallery()">↻ Refresh</button>
      <span class="muted" style="font-size:.72rem;align-self:center">Live · 30s</span>
    </div>
    <div class="card detail">
      <div class="badge blue">CULTURE</div>
      <span class="badge ok" style="margin-left:6px">● live</span>
      <h2 style="margin:8px 0 6px">Howlcoin NFT gallery</h2>
      <p class="muted" style="margin:0 0 10px">${cul.nfts??nfts.length} NFTs on-chain · including mint-from-howl collectibles. Mint in the <a href="/app">wallet</a>.</p>
    </div>
    <div class="main" style="padding:12px 0 0;margin:0">
      <div class="card">
        <h3>Gallery</h3>
        <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(140px,1fr));gap:10px;padding:12px">
          ${nfts.length?nfts.map(n=>{
            const uri = n.uri || '';
            const isHowl = String(uri).startsWith('howl://');
            const img = (!isHowl && uri && (uri.startsWith('http')||uri.startsWith('/'))) ? `<img src="${esc(uri)}" alt="" style="width:100%;height:100px;object-fit:cover;border-radius:8px;background:#111" onerror="this.style.display='none'"/>` :
              `<div style="height:100px;border-radius:8px;background:rgba(61,255,154,.08);border:1px solid var(--border);display:flex;align-items:center;justify-content:center;font-size:1.6rem">🐺</div>`;
            return `<div style="border:1px solid var(--border);border-radius:12px;padding:8px;background:var(--panel2);cursor:pointer" onclick="location.hash='#/${net}/address/${encodeURIComponent(n.owner||'')}'">
              ${img}
              <div style="font-weight:700;font-size:.82rem;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(n.name||'NFT')}</div>
              <div class="muted mono" style="font-size:.68rem">#${esc(String(n.mint_height??n.last_height??'—'))} · ${esc(short(n.owner,10))}</div>
            </div>`;
          }).join(''):`<div class="muted" style="padding:16px;grid-column:1/-1">No NFTs yet — mint from a howl in the wallet.</div>`}
        </div>
      </div>
      <div class="card" style="margin-top:12px">
        <h3>Recent NFT activity</h3>
        <div class="mlist">
          ${events.length?events.map(ev=>`<div class="mrow">
            <div class="ml">
              <div class="mt"><span class="badge ok">${esc(ev.kind||ev.type||'event')}</span> ${esc(ev.name||ev.nft_id||'')}</div>
              <div class="ms">#${esc(String(ev.height??'—'))} · ${ev.owner||ev.to?linkAddr(ev.owner||ev.to):'—'}</div>
            </div>
          </div>`).join(''):`<div class="mrow"><div class="muted">No NFT events yet.</div></div>`}
        </div>
      </div>
    </div>
  </div>`;
}

async function showNameProfile(slug){
  setHeroVisible(false);
  setBottomTab('play');
  await loadNetworks();
  const s = String(slug||'').replace(/^@/,'').replace(/\.howl$/i,'').trim().toLowerCase();
  let row=null, addr='', bal=null, pots=[], tips=[], nfts=[], howls=[];
  try{
    const j = await api(`/api/${net}/name/${encodeURIComponent(s)}`);
    row = j.name || j;
    addr = row.address || '';
  }catch(e){
    app().innerHTML=`<div class="main" style="padding-top:12px"><div class="card detail err">Name @${esc(s)} not found. <a href="#/play">Play board</a></div></div>`;
    return;
  }
  if(addr){
    try{
      const [aj, pj, tj, nj, hj] = await Promise.all([
        api(`/api/${net}/address/${encodeURIComponent(addr)}`).catch(()=>({})),
        api(`/api/${net}/contracts?kind=packpot&limit=40`).catch(()=>({contracts:[]})),
        api(`/api/${net}/contracts?kind=tipjar&limit=40`).catch(()=>({contracts:[]})),
        api(`/api/${net}/nfts?owner=${encodeURIComponent(addr)}&limit=24`).catch(()=>({nfts:[]})),
        api(`/api/${net}/howls?limit=50`).catch(()=>({howls:[]})),
      ]);
      bal = aj;
      pots = (pj.contracts||[]).filter(c=>c.owner===addr || c.last_joiner===addr);
      tips = (tj.contracts||[]).filter(c=>c.owner===addr);
      nfts = nj.nfts||[];
      howls = (hj.howls||[]).filter(h=>(h.from||h.reporter)===addr).slice(0,12);
    }catch(e){}
  }
  const shareUrl = 'https://howlscan.org/@' + encodeURIComponent(s);
  setPageMeta(
    `@${s} · Howlscan`,
    `Howlcoin @${s}${addr?' · '+addr.slice(0,12)+'…':''} · balance ${bal&&bal.balance_fmt||'—'} · ${nfts.length} NFTs · ${howls.length} howls.`,
    '@' + s
  );
  // Keep pretty URL when possible
  try{
    if(location.pathname !== '/@' + s && !(location.pathname||'').startsWith('/api')){
      history.replaceState(null, '', '/@' + s + (location.hash || '#/name/' + encodeURIComponent(s)));
    }
  }catch(e){}
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'City',href:'#/city'},{label:'Play',href:'#/play'},{label:'@'+s}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/city'">← City</button>
      <button class="chipbtn" onclick="copyText(${JSON.stringify(shareUrl)}, this)">Share @${esc(s)}</button>
      <button class="chipbtn" onclick="copyText(${JSON.stringify(addr)}, this)">Copy address</button>
      <a class="chipbtn" href="/app?to=${encodeURIComponent('@'+s)}" style="text-decoration:none;color:var(--green)">Send HOWL</a>
      <a class="chipbtn" href="/app?howl=1" style="text-decoration:none;color:var(--cyan)">Howl</a>
    </div>
    <div class="card detail">
      <div class="badge ok">@${esc(s)}</div>
      <h2 style="margin:8px 0 6px">@${esc(s)}</h2>
      <div class="mono">${esc(addr)}${copyBtn(addr)}</div>
      <div class="stats" style="margin-top:12px">
        <div class="stat"><div class="k">Balance</div><div class="v" style="font-size:.95rem">${esc(bal&&bal.balance_fmt||'—')}</div><div class="s">HOWL</div></div>
        <div class="stat"><div class="k">NFTs</div><div class="v">${nfts.length}</div><div class="s">owned</div></div>
        <div class="stat"><div class="k">Pots</div><div class="v">${pots.length}</div><div class="s">related</div></div>
        <div class="stat"><div class="k">Howls</div><div class="v">${howls.length}</div><div class="s">recent</div></div>
      </div>
      <div class="kv" style="margin-top:10px">
        <div class="k">Registered</div><div>#${esc(String(row.height??'—'))}${row.txid? ' · '+linkTx(row.txid):''}</div>
        <div class="k">Address page</div><div>${linkAddr(addr)}</div>
      </div>
    </div>
    <div class="main cols" style="padding:12px 0 0;margin:0">
      <div class="card">
        <h3>Howls</h3>
        <div class="mlist">
          ${howls.length?howls.map(e=>`<div class="mrow"><div class="ml"><div class="mt">🐺 ${esc(String(e.message||e.value||''))}</div><div class="ms">#${esc(String(e.height??'—'))}</div></div></div>`).join(''):`<div class="mrow"><div class="muted">No howls from this name yet.</div></div>`}
        </div>
      </div>
      <div class="card">
        <h3>Tip jars &amp; pots</h3>
        <div class="mlist">
          ${[...tips,...pots].length?[...tips,...pots].map(c=>`<div class="mrow">
            <div class="ml"><div class="mt">${esc(c.name||c.kind)} <span class="badge blue">${esc(c.kind||'')}</span></div>
            <div class="ms">${esc(c.balance_fmt||'')} · ${esc(c.status||'')}</div></div>
          </div>`).join(''):`<div class="mrow"><div class="muted">No contracts.</div></div>`}
        </div>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>NFTs owned</h3>
      <div class="mlist">
        ${nfts.length?nfts.map(n=>`<div class="mrow">
          <div class="ml"><div class="mt">${esc(n.name||'NFT')}</div>
          <div class="ms mono">#${esc(String(n.mint_height??'—'))} · ${esc(short(n.nft_id,12))}</div></div>
        </div>`).join(''):`<div class="mrow"><div class="muted">No NFTs.</div></div>`}
      </div>
    </div>
  </div>`;
}

async function showChartsBoard(){
  setHeroVisible(false);
  setBottomTab('health');
  await loadNetworks();
  let markets={coins:[]}, chart={points:[]}, howlChart={points:[]};
  try{
    [markets, chart, howlChart] = await Promise.all([
      api('/api/public/markets').catch(()=>({coins:[]})),
      api('/api/public/chart?id=bitcoin&days=7').catch(()=>({points:[]})),
      api('/api/public/chart?id=howlcoin&days=7').catch(()=>({points:[]})),
    ]);
  }catch(e){}
  const coins = markets.coins || [];
  const btcPts = sparkPts(chart.points, 320, 80);
  const howlPts = sparkPts(howlChart.points, 320, 80);
  const howlPx = howlChart.close!=null?Number(howlChart.close):(coins.find(c=>c.id==='howlcoin')?Number(coins.find(c=>c.id==='howlcoin').usd):null);
  setPageMeta(
    'Howl Charts · Howlscan',
    `Howlcoin markets board${howlPx!=null?': HOWL ~ $'+howlPx.toPrecision(4):''}. On-chain spots + Howl Swap index.`,
    '#/charts'
  );
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Charts'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/health'">Network</button>
      <a class="chipbtn" href="/app" style="text-decoration:none">Wallet charts</a>
      <button class="chipbtn" onclick="showChartsBoard()">↻ Refresh</button>
      <span class="muted" style="font-size:.72rem;align-self:center">Live · 45s</span>
    </div>
    <div class="card detail">
      <div class="badge ok">HOWL CHARTS</div>
      <h2 style="margin:8px 0 6px">Markets · Howlcoin product</h2>
      <p class="muted" style="margin:0 0 10px">${esc(markets.note||'On-chain spots + Howl Swap index for HOWL. Built by Howlcoin.')}</p>
    </div>
    <div class="main cols" style="padding:12px 0 0;margin:0">
      <div class="card">
        <h3>HOWL · Howl Swap index</h3>
        <div style="padding:8px 12px">
          <div style="font-size:1.4rem;font-weight:800;color:var(--green)">${howlChart.close!=null?('$'+Number(howlChart.close).toPrecision(4)): (coins.find(c=>c.id==='howlcoin')?('$'+Number(coins.find(c=>c.id==='howlcoin').usd).toPrecision(4)):'—')}</div>
          <div class="muted" style="font-size:.78rem;margin:4px 0 8px">${esc(howlChart.range||'7d')} · ${howlChart.count||0} pts · ${esc(howlChart.source||'howl-swap')}</div>
          ${howlPts?`<svg viewBox="0 0 320 80" width="100%" height="80" style="display:block;background:rgba(0,0,0,.2);border:1px solid var(--border)"><polyline fill="none" stroke="var(--green)" stroke-width="2" points="${howlPts}"/></svg>`:`<div class="muted">Not enough samples yet — sampler builds history over time.</div>`}
        </div>
      </div>
      <div class="card">
        <h3>BTC reference (7d)</h3>
        <div style="padding:8px 12px">
          <div style="font-size:1.4rem;font-weight:800">${chart.close!=null?('$'+Number(chart.close).toLocaleString(undefined,{maximumFractionDigits:0})):'—'}</div>
          <div class="muted" style="font-size:.78rem;margin:4px 0 8px">${chart.change_pct!=null?((chart.change_pct>=0?'+':'')+Number(chart.change_pct).toFixed(2)+'%'):'—'} · on-chain oracle feed</div>
          ${btcPts?`<svg viewBox="0 0 320 80" width="100%" height="80" style="display:block;background:rgba(0,0,0,.2);border:1px solid var(--border)"><polyline fill="none" stroke="var(--cyan,#5eb8ff)" stroke-width="2" points="${btcPts}"/></svg>`:`<div class="muted">Chart unavailable</div>`}
        </div>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>Live board</h3>
      <div class="desktop-only table-wrap">
        <table>
          <thead><tr><th>Asset</th><th>Price</th><th>24h</th><th>Source</th></tr></thead>
          <tbody>
            ${coins.map(c=>`<tr>
              <td><b>${esc(c.symbol||'')}</b> <span class="muted">${esc(c.name||'')}</span>${c.product||c.id==='howlcoin'?' <span class="badge ok">HOWL</span>':''}</td>
              <td class="amount">${c.usd!=null?('$'+Number(c.usd).toLocaleString(undefined,{maximumSignificantDigits:6})):'—'}</td>
              <td>${c.change_24h!=null?((c.change_24h>=0?'+':'')+Number(c.change_24h).toFixed(2)+'%'):'—'}</td>
              <td class="muted">${esc(c.oracle||c.index||markets.source||'—')}</td>
            </tr>`).join('')||'<tr><td colspan="4" class="muted">No market data</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="mobile-only mlist">
        ${coins.map(c=>`<div class="mrow">
          <div class="ml"><div class="mt">${esc(c.symbol||'')} · ${esc(c.name||'')}</div>
          <div class="ms">${c.change_24h!=null?((c.change_24h>=0?'+':'')+Number(c.change_24h).toFixed(2)+'% 24h'):'—'}</div></div>
          <div class="mr"><div class="ma">${c.usd!=null?('$'+Number(c.usd).toLocaleString(undefined,{maximumSignificantDigits:5})):'—'}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No data</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showApiDocs(){
  setHeroVisible(false);
  setBottomTab('more');
  await loadNetworks();
  setPageMeta(
    'Howlscan API · Howlcoin',
    'Public JSON APIs for Howlcoin: blocks, txs, play, culture, charts, status.',
    '#/api'
  );
  const base = location.origin;
  const rows = [
    ['GET', '/api/networks', 'Known networks + tip heights'],
    ['GET', '/api/public/summary', 'Chain summary (height, tip, supply)'],
    ['GET', '/api/public/status?window=40', 'Ops theater: tip age, est. hashrate, culture, charts sampler'],
    ['GET', '/api/public/health?window=40', 'Alias of status (health + culture)'],
    ['GET', '/api/public/blocks?limit=25', 'Recent blocks'],
    ['GET', '/api/public/txs?limit=25', 'Recent transactions'],
    ['GET', '/api/public/mempool', 'Pending mempool'],
    ['GET', '/api/public/richlist?limit=50', 'Top balances'],
    ['GET', '/api/public/block/<height|hash>', 'Block detail'],
    ['GET', '/api/public/tx/<txid>', 'Transaction detail'],
    ['GET', '/api/public/address/<H…>', 'Address history'],
    ['GET', '/api/public/howls?limit=40', 'Social howl feed'],
    ['GET', '/api/public/city?limit=50', 'Howl City live feed (optional kinds=howl,pot,name,…)'],
    ['GET', '/api/public/names?limit=100', 'On-chain name directory'],
    ['GET', '/api/public/name/<slug>', 'Resolve @name'],
    ['GET', '/api/public/names?address=H…', 'Name for address'],
    ['GET', '/api/public/contracts?kind=packpot', 'Contracts (optional kind, status, owner)'],
    ['GET', '/api/public/contract/<id>', 'Contract detail'],
    ['GET', '/api/public/nfts?limit=48', 'NFT gallery (optional owner=)'],
    ['GET', '/api/public/nft/<id>', 'Single NFT'],
    ['GET', '/api/public/nft-events?limit=50', 'NFT mint/transfer events'],
    ['GET', '/api/public/markets', 'Howl Charts live board'],
    ['GET', '/api/public/chart?id=bitcoin&days=7', 'Price series (howlcoin|bitcoin|…)'],
    ['GET', '/api/public/coin?id=ethereum', 'Spot + sample ATH/ATL'],
    ['GET', '/api/public/fees', 'Min/default fees'],
    ['GET', '/api/public/token-info', 'Listing / token metadata'],
    ['POST', '/api/public/broadcast', 'Broadcast signed tx JSON {tx:…}'],
  ];
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'API'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="copyText(${JSON.stringify(base+'/api/public/status')}, this)">Copy status URL</button>
      <a class="chipbtn" href="/api/public/status" target="_blank" rel="noopener">Open status JSON</a>
    </div>
    <div class="card detail">
      <div class="badge ok">API</div>
      <h2 style="margin:8px 0 6px">Howlscan public API</h2>
      <p class="muted" style="margin:0 0 10px">JSON over HTTPS. CORS open for public read endpoints. Base: <span class="mono">${esc(base)}</span>. Network id for chain routes is usually <span class="mono">public</span>.</p>
      <p class="muted" style="margin:0;font-size:.85rem">SPA pages: <a href="#/city">#/city</a> · <a href="#/play">#/play</a> · <a href="#/culture">#/culture</a> · <a href="#/contracts">#/contracts</a> · <a href="#/charts">#/charts</a> · <a href="#/health">#/health</a> · <a href="#/name/howler">#/name/&lt;slug&gt;</a> · share <span class="mono">/@slug</span></p>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>Endpoints</h3>
      <div class="desktop-only table-wrap">
        <table>
          <thead><tr><th>Method</th><th>Path</th><th>Notes</th></tr></thead>
          <tbody>
            ${rows.map(([m,p,n])=>`<tr>
              <td><span class="badge ${m==='GET'?'ok':'blue'}">${m}</span></td>
              <td class="mono" style="font-size:.78rem">${esc(p)}</td>
              <td class="muted">${esc(n)}</td>
            </tr>`).join('')}
          </tbody>
        </table>
      </div>
      <div class="mobile-only mlist">
        ${rows.map(([m,p,n])=>`<div class="mrow">
          <div class="ml"><div class="mt"><span class="badge ${m==='GET'?'ok':'blue'}">${m}</span> <span class="mono" style="font-size:.72rem">${esc(p)}</span></div>
          <div class="ms">${esc(n)}</div></div>
        </div>`).join('')}
      </div>
    </div>
    <div class="card detail" style="margin-top:12px">
      <h3 style="margin-top:0">Quick curl</h3>
      ${cmdBox('Status', `curl -sS ${base}/api/public/status | python3 -m json.tool`)}
      ${cmdBox('Howls', `curl -sS '${base}/api/public/howls?limit=10'`)}
      ${cmdBox('Pack pots', `curl -sS '${base}/api/public/contracts?kind=packpot'`)}
    </div>
  </div>`;
}

async function showContractsBrowser(kindFilter){
  setHeroVisible(false);
  setBottomTab('play');
  await loadNetworks();
  const kind = (kindFilter || '').toLowerCase() || '';
  const qs = kind ? `?kind=${encodeURIComponent(kind)}&limit=80` : '?limit=80';
  let rows = [];
  try{
    const j = await api(`/api/${net}/contracts${qs}`);
    rows = j.contracts || [];
  }catch(e){}
  const kinds = ['','packpot','tipjar','barkbond','timelock','escrow'];
  setPageMeta(
    `Contracts${kind?' · '+kind:''} · Howlscan`,
    `Howl Script contracts on Howlcoin${kind?': '+kind:''}. ${rows.length} listed.`,
    `#/contracts${kind?'/'+kind:''}`
  );
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Contracts'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      <button class="chipbtn" onclick="location.hash='#/play'">Play</button>
      <a class="chipbtn" href="/app" style="text-decoration:none;color:var(--green)">Act in wallet</a>
    </div>
    <div class="card detail">
      <div class="badge blue">CONTRACTS</div>
      <h2 style="margin:8px 0 6px">Howl Script contracts</h2>
      <p class="muted" style="margin:0 0 10px">On-chain pots, tip jars, bonds, locks, escrow. Filter and open details. Deploy/join in the wallet.</p>
      <div class="quick-row" style="margin:0">
        ${kinds.map(k=>`<button class="chipbtn ${k===kind?'active':''}" onclick="location.hash='#/contracts${k?'/'+k:''}'">${k||'all'}</button>`).join('')}
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>${rows.length} contract${rows.length===1?'':'s'}</h3>
      <div class="desktop-only table-wrap">
        <table>
          <thead><tr><th>Name</th><th>Kind</th><th>Status</th><th>Balance</th><th>Owner</th><th>Id</th></tr></thead>
          <tbody>
            ${rows.map(c=>`<tr onclick="location.hash='#/contract/${encodeURIComponent(c.contract_id||'')}'" style="cursor:pointer">
              <td><b>${esc(c.name||'—')}</b></td>
              <td><span class="badge blue">${esc(c.kind||'')}</span></td>
              <td>${esc(c.status||'—')}</td>
              <td class="amount">${esc(c.balance_fmt||'0')}</td>
              <td onclick="event.stopPropagation()">${linkAddr(c.owner)}</td>
              <td class="mono" onclick="event.stopPropagation()">${linkContract(c.contract_id)}</td>
            </tr>`).join('')||'<tr><td colspan="6" class="muted" style="padding:16px">No contracts yet</td></tr>'}
          </tbody>
        </table>
      </div>
      <div class="mobile-only mlist">
        ${rows.map(c=>`<div class="mrow" onclick="location.hash='#/contract/${encodeURIComponent(c.contract_id||'')}'">
          <div class="ml">
            <div class="mt">${esc(c.name||'Contract')} <span class="badge blue">${esc(c.kind||'')}</span></div>
            <div class="ms">${esc(c.status||'')} · owner ${esc(short(c.owner,10))}</div>
          </div>
          <div class="mr"><div class="ma">${esc(c.balance_fmt||'0')}</div></div>
        </div>`).join('')||'<div class="mrow"><div class="muted">No contracts</div></div>'}
      </div>
    </div>
  </div>`;
}

async function showContractDetail(cid){
  setHeroVisible(false);
  setBottomTab('play');
  await loadNetworks();
  let c = null;
  try{
    const j = await api(`/api/${net}/contract/${encodeURIComponent(cid)}`);
    c = j.contract || j;
  }catch(e){
    app().innerHTML=`<div class="main" style="padding-top:12px"><div class="card detail err">Contract not found. <a href="#/contracts">Browser</a></div></div>`;
    return;
  }
  const hist = (c.history || []).slice().reverse().slice(0, 20);
  const hNow = null; // optional
  app().innerHTML = `<div class="main" style="padding-top:12px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:'Contracts',href:'#/contracts'},{label:c.name||'Contract'}])}
    <div class="page-actions">
      <button class="back" onclick="location.hash='#/contracts'">← Contracts</button>
      <button class="chipbtn" onclick="copyText(${JSON.stringify(String(c.contract_id||''))}, this)">Copy id</button>
      <a class="chipbtn" href="/app" style="text-decoration:none;color:var(--green)">Join / act in wallet</a>
    </div>
    <div class="card detail">
      <span class="badge blue">${esc(c.kind||'contract')}</span>
      <span class="badge ${(c.status||'')==='active'?'ok':'warn'}" style="margin-left:6px">${esc(c.status||'—')}</span>
      <h2 style="margin:8px 0 6px">${esc(c.name||'Contract')}</h2>
      <div class="mono">${esc(c.contract_id||'')}${copyBtn(c.contract_id||'')}</div>
      <div class="stats" style="margin-top:12px">
        <div class="stat"><div class="k">Balance</div><div class="v" style="font-size:1rem">${esc(c.balance_fmt||'0')}</div><div class="s">locked</div></div>
        <div class="stat"><div class="k">Unlock</div><div class="v" style="font-size:1rem">#${esc(String(c.unlock_height??'—'))}</div><div class="s">height</div></div>
        <div class="stat"><div class="k">Joins</div><div class="v">${esc(String(c.join_count??'—'))}</div><div class="s">pack pot</div></div>
      </div>
      <div class="kv" style="margin-top:12px">
        <div class="k">Owner</div><div>${linkAddr(c.owner)}</div>
        <div class="k">Last joiner</div><div>${c.last_joiner?linkAddr(c.last_joiner):'—'}</div>
        <div class="k">Counterparty</div><div>${c.counterparty?linkAddr(c.counterparty):'—'}</div>
        <div class="k">Arbiter</div><div>${c.arbiter?linkAddr(c.arbiter):'—'}</div>
        <div class="k">Bond phrase</div><div>${esc(c.bond_phrase||'—')}</div>
        <div class="k">Min join</div><div>${c.min_join!=null?fmtAmt(c.min_join):'—'}</div>
        <div class="k">Deploy height</div><div>#${esc(String(c.deploy_height??'—'))}</div>
      </div>
    </div>
    <div class="card" style="margin-top:12px">
      <h3>History</h3>
      <div class="mlist">
        ${hist.length?hist.map(ev=>`<div class="mrow">
          <div class="ml">
            <div class="mt">${esc(ev.method||ev.action||'event')}</div>
            <div class="ms">#${esc(String(ev.height??'—'))} · ${ev.from?linkAddr(ev.from):'—'} · ${ev.amount!=null?fmtAmt(ev.amount):''}</div>
          </div>
        </div>`).join(''):'<div class="mrow"><div class="muted">No history entries</div></div>'}
      </div>
    </div>
  </div>`;
}

function doSearch(){
  const q=($('#q')&&$('#q').value||'').trim();
  if(!q) return loadHome();
  if(/^\d+$/.test(q)) { location.hash=`#/${net}/block/${q}`; return route(); }
  // @name or name.howl
  const nameQ = q.replace(/^@/,'').replace(/\.howl$/i,'').trim().toLowerCase();
  if((q.startsWith('@') || q.toLowerCase().endsWith('.howl') || (/^[a-z0-9_]{3,16}$/.test(nameQ) && !/^[0-9]+$/.test(nameQ) && q.length < 24))
     && /^[a-z0-9_]{3,16}$/.test(nameQ)){
    location.hash = `#/name/${encodeURIComponent(nameQ)}`;
    return route();
  }
  if(q.startsWith('H') && q.length>20){ location.hash=`#/${net}/address/${encodeURIComponent(q)}`; return route(); }
  // contract id patterns
  if(/^(packpot|tipjar|barkbond|timelock|escrow|hc|contract)/i.test(q) || q.includes(':') && q.length > 12){
    location.hash = `#/contract/${encodeURIComponent(q)}`;
    return route();
  }
  // try block → tx → contract → nft
  location.hash=`#/${net}/block/${encodeURIComponent(q)}`;
  route().catch(()=>{ location.hash=`#/${net}/tx/${encodeURIComponent(q)}`; return route(); })
    .catch(async ()=>{
      try{
        const j = await api(`/api/${net}/contract/${encodeURIComponent(q)}`);
        if(j.contract || j.contract_id){ location.hash=`#/contract/${encodeURIComponent(q)}`; return route(); }
      }catch(e){}
      try{
        const j = await api(`/api/${net}/nft/${encodeURIComponent(q)}`);
        if(j.nft){ location.hash=`#/${net}/address/${encodeURIComponent(j.nft.owner||'')}`; return route(); }
      }catch(e){}
      app().innerHTML=`<div class="main"><div class="card detail err">Not found: <span class="mono">${esc(q)}</span><br><span class="muted">Try height, hash, tx, H… address, @name, or contract id</span></div></div>`;
    });
}

// Pretty paths → SPA hash (howlscan.org/@name, /city, /play)
(function bootstrapPrettyPath(){
  try{
    const p = (location.pathname || '/').replace(/\/+$/,'') || '/';
    const hasHash = !!(location.hash && location.hash.replace(/^#\/?/,'').length);
    if(hasHash) return;
    const at = p.match(/^\/@([a-zA-Z0-9_-]{1,32})$/i);
    if(at){
      location.replace('/@' + at[1].toLowerCase() + '#/name/' + encodeURIComponent(at[1].toLowerCase()));
      return;
    }
    if(p === '/city'){ location.replace('/#/city'); return; }
    if(p === '/play'){ location.replace('/#/play'); return; }
    if(p === '/culture' || p === '/nfts'){ location.replace('/#/culture'); return; }
    if(p === '/charts'){ location.replace('/#/charts'); return; }
  }catch(e){}
})();

async function route(){
  toggleDrawer(false);
  const h=(location.hash||'').replace(/^#\/?/,'');
  const parts=h.split('/').filter(Boolean);
  if(parts[0] && networks.length && networks.find(n=>n.id===parts[0])){
    net=parts[0];
  }
  renderNav();
  setBottomTab(activeTabFromRoute(parts));
  __routeKey = parts.join('/') || 'home';
  try{ window.scrollTo({top:0, behavior:'instant' in window ? 'instant' : 'auto'}); }catch(e){ window.scrollTo(0,0); }
  try{
    // Global product routes (network-agnostic → public chain)
    if(parts[0]==='city') return await showHowlCity(parts[1] || '');
    if(parts[0]==='play') return await showPlayBoard();
    if(parts[0]==='culture' || parts[0]==='nfts' || parts[0]==='gallery') return await showCultureGallery();
    if(parts[0]==='charts' || parts[0]==='markets') return await showChartsBoard();
    if(parts[0]==='api' || parts[0]==='docs') return await showApiDocs();
    if(parts[0]==='contracts') return await showContractsBrowser(parts[1] || '');
    if(parts[0]==='contract' && parts[1]) return await showContractDetail(decodeURIComponent(parts.slice(1).join('/')));
    if(parts[0]==='name' && parts[1]) return await showNameProfile(decodeURIComponent(parts[1]));
    if(parts.length>=3 && parts[1]==='name') return await showNameProfile(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='contract') return await showContractDetail(decodeURIComponent(parts.slice(2).join('/')));
    if(parts.length>=3 && parts[1]==='block') return await showBlock(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='tx') return await showTx(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='address') return await showAddr(decodeURIComponent(parts[2]));
    if(parts.length>=1 && (parts[0]==='run' || parts[0]==='node' || parts[0]==='sync')) return await showRunNode();
    if(parts.length>=1 && (parts[0]==='health' || parts[0]==='status')) return await showHealth();
    if(parts.length>=2 && parts[1]==='richlist') return await showRichlist();
    if(parts.length>=2 && parts[1]==='mempool') return await showMempool();
    if(parts.length>=2 && parts[0]==='block') return await showBlock(decodeURIComponent(parts[1]));
    if(parts.length>=1 && parts[0]==='richlist') return await showRichlist();
    if(parts.length>=1 && parts[0]==='mempool') return await showMempool();
    setPageMeta('Howlscan — Howlcoin Block Explorer', 'Public Howlcoin explorer: blocks, Play, culture NFTs, @names, network status.', '#/'+net);
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
function liveRouteKind(){
  const h=(location.hash||'').replace(/^#\/?/,'').split('/').filter(Boolean);
  if(!h.length || (h.length===1 && networks.find(n=>n.id===h[0]))) return 'home';
  if(h[0]==='city') return 'city';
  if(h[0]==='play') return 'play';
  if(h[0]==='culture' || h[0]==='nfts' || h[0]==='gallery') return 'culture';
  if(h[0]==='charts' || h[0]==='markets') return 'charts';
  if(h[0]==='health' || h[0]==='status') return 'health';
  if(h[0]==='mempool' || h[1]==='mempool') return 'mempool';
  if(h[0]==='contracts') return 'contracts';
  return '';
}
function refreshData(){
  softLiveRefresh(true);
}
function softLiveRefresh(force){
  if(!__liveRefreshOn && !force) return;
  const kind = liveRouteKind();
  if(!kind && !force) return;
  const keyBefore = __routeKey;
  const run = async ()=>{
    if(kind==='home') await loadHome();
    else if(kind==='city'){
      const hh=(location.hash||'').replace(/^#\/?/,'').split('/').filter(Boolean);
      await showHowlCity(hh[1]||'');
    }
    else if(kind==='play') await showPlayBoard();
    else if(kind==='culture') await showCultureGallery();
    else if(kind==='charts') await showChartsBoard();
    else if(kind==='health') await showHealth();
    else if(kind==='mempool') await showMempool();
    else if(kind==='contracts'){
      const hh=(location.hash||'').replace(/^#\/?/,'').split('/').filter(Boolean);
      await showContractsBrowser(hh[1]||'');
    } else if(force) await route();
  };
  run().catch(()=>{});
  void keyBefore;
}
window.addEventListener('hashchange', ()=>route());
ensureBanner();
loadNetworks().then(route);
// Live surfaces refresh every 15s
setInterval(()=>{ softLiveRefresh(false); }, 15000);
// Tip ticker: flash when height/tip changes (home only)
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

                # Pretty product paths + @name share URLs → SPA shell
                # Client bootstrapPrettyPath / showNameProfile map these to hash routes.
                if path in (
                    "/city",
                    "/city/",
                    "/play",
                    "/play/",
                    "/culture",
                    "/culture/",
                    "/charts",
                    "/charts/",
                    "/nfts",
                    "/nfts/",
                ):
                    return self._bytes(
                        200, EXPLORER_HTML.encode(), "text/html; charset=utf-8"
                    )
                # howlscan.org/@slug — public name share links
                if path.startswith("/@"):
                    slug = path[2:].strip("/").split("/")[0]
                    if slug and re.fullmatch(r"[A-Za-z0-9_-]{1,32}", slug):
                        return self._bytes(
                            200, EXPLORER_HTML.encode(), "text/html; charset=utf-8"
                        )

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

                if path in (
                    "/api/public/health",
                    "/api/health",
                    "/api/public/status",
                    "/api/status",
                ):
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
                        out = chain.network_health(window=limit)
                        # Charts sampler health (same data dir as explorer)
                        try:
                            samples_path = _howl_charts_samples_path()
                            samp = {"path": str(samples_path), "exists": samples_path.exists()}
                            if samples_path.exists():
                                raw = json.loads(
                                    samples_path.read_text(encoding="utf-8")
                                )
                                if isinstance(raw, dict):
                                    counts = {
                                        k: len(v)
                                        for k, v in raw.items()
                                        if isinstance(v, list)
                                    }
                                    samp["assets"] = len(counts)
                                    samp["points"] = sum(counts.values())
                                    # freshest sample timestamp
                                    latest = 0
                                    for pts in raw.values():
                                        if isinstance(pts, list) and pts:
                                            try:
                                                latest = max(
                                                    latest, int(pts[-1].get("t") or 0)
                                                )
                                            except (TypeError, ValueError, AttributeError):
                                                pass
                                    samp["latest_sample_ts"] = latest or None
                                    if latest:
                                        samp["sample_age_seconds"] = max(
                                            0, int(time.time()) - latest
                                        )
                            out["charts_sampler"] = samp
                        except Exception as se:
                            out["charts_sampler"] = {"error": str(se)}
                        out["product"] = "Howlcoin network status"
                        out["note"] = "Ops theater · live L1 metrics"
                        return self._json(200, out)
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

                # Custom market charts + live board (external coins, not Howlcoin)
                if path in ("/api/public/chart", "/api/chart", "/api/public/charts"):
                    cid = (qs.get("id") or qs.get("coin") or ["bitcoin"])[0]
                    days = (qs.get("days") or ["7"])[0]
                    force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
                    return self._json(
                        200, fetch_market_chart(coin_id=cid, days=str(days), force=force)
                    )
                if path in (
                    "/api/public/markets",
                    "/api/markets",
                    "/api/public/markets/board",
                ):
                    force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
                    return self._json(200, fetch_markets_board(force=force))
                if path in (
                    "/api/public/markets/coin",
                    "/api/public/coin",
                    "/api/coin",
                ):
                    cid = (qs.get("id") or qs.get("coin") or ["bitcoin"])[0]
                    force = (qs.get("force") or ["0"])[0] in ("1", "true", "yes")
                    return self._json(
                        200, fetch_coin_profile(coin_id=str(cid), force=force)
                    )

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

                    # Social howl feed (Play hub) — full history from txs
                    if rest[0] in ("howls", "howl-feed", "awoo"):
                        limit = int(qs.get("limit", ["40"])[0])
                        rows = chain.list_howls(limit=limit)
                        return self._json(
                            200,
                            {
                                "network": net,
                                "howls": rows,
                                "count": len(rows),
                                "note": "On-chain howls · Howlcoin Play",
                            },
                        )

                    # Howl City live feed
                    if rest[0] in ("city", "city-feed", "live"):
                        limit = int(qs.get("limit", ["50"])[0])
                        kinds_raw = (qs.get("kinds") or qs.get("kind") or [""])[0]
                        kinds = (
                            [k.strip() for k in kinds_raw.split(",") if k.strip()]
                            if kinds_raw
                            else None
                        )
                        rows = chain.list_city_events(limit=limit, kinds=kinds)
                        return self._json(
                            200,
                            {
                                "network": net,
                                "events": rows,
                                "count": len(rows),
                                "height": chain.height(),
                                "culture": chain.culture_stats(),
                                "note": "Howl City · live L1 feed",
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
                        status = (qs.get("status") or [None])[0]
                        limit = int(qs.get("limit", ["100"])[0])
                        rows = chain.list_contracts(
                            owner=owner, kind=kind, status=status, limit=limit
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
