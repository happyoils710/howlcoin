"""
Howl Swap (Phase A) — semi-custodial bridge SOL/USDC → native HOWL.

Users open an order, send SOL (or USDC) to the treasury deposit address.
A relayer watches deposits and pays HOWL from a hot wallet.

Env (explorer / relayer):
  HOWL_BRIDGE_ENABLED=1
  HOWL_BRIDGE_SOL_TREASURY=<base58 sol address>
  HOWL_BRIDGE_USDC_TREASURY=<same or ATA owner>
  HOWL_BRIDGE_HOWL_PER_SOL=100000          # HOWL received per 1 SOL (gross before fee)
  HOWL_BRIDGE_HOWL_PER_USDC=10             # HOWL per 1 USDC
  HOWL_BRIDGE_FEE_BPS=100                  # 1% fee
  HOWL_BRIDGE_MIN_SOL=0.01
  HOWL_BRIDGE_MIN_USDC=1
  HOWL_BRIDGE_MAX_SOL=10
  HOWL_BRIDGE_MAX_USDC=5000
  HOWL_BRIDGE_ORDER_TTL_SEC=3600
  HOWL_BRIDGE_DATA=/var/lib/howlcoin       # orders file parent (defaults public data dir)
  HOWL_BRIDGE_ADMIN_SECRET=...            # optional: POST /api/public/bridge/admin/*
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import COIN
from .crypto import is_valid_address

BRIDGE_FILE = "bridge_orders.json"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

_lock = threading.RLock()


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except ValueError:
        return default


def bridge_enabled() -> bool:
    v = _env("HOWL_BRIDGE_ENABLED", "0").lower()
    if v in ("1", "true", "yes", "on"):
        return True
    # enabled if a treasury is configured
    return bool(_env("HOWL_BRIDGE_SOL_TREASURY"))


def data_dir(fallback: Optional[Path] = None) -> Path:
    raw = _env("HOWL_BRIDGE_DATA") or _env("HOWL_PUBLIC_DATA")
    if raw:
        return Path(raw).expanduser()
    if fallback:
        return Path(fallback).expanduser()
    return Path.home() / ".howlcoin"


def orders_path(dd: Optional[Path] = None) -> Path:
    return data_dir(dd) / BRIDGE_FILE


def bridge_config() -> Dict[str, Any]:
    sol_treas = _env("HOWL_BRIDGE_SOL_TREASURY")
    usdc_treas = _env("HOWL_BRIDGE_USDC_TREASURY") or sol_treas
    howl_per_sol = _env_float("HOWL_BRIDGE_HOWL_PER_SOL", 100_000.0)
    howl_per_usdc = _env_float("HOWL_BRIDGE_HOWL_PER_USDC", 10.0)
    fee_bps = max(0, min(2000, _env_int("HOWL_BRIDGE_FEE_BPS", 100)))
    enabled = bridge_enabled() and bool(sol_treas)
    return {
        "enabled": enabled,
        "engine": "Howl Swap",
        "phase": "A",
        "note": (
            "Semi-custodial: send SOL/USDC to the deposit address, then a relayer "
            "credits native HOWL to your Howl address. Not trustless."
            if enabled
            else "Bridge offline — set HOWL_BRIDGE_ENABLED=1 and HOWL_BRIDGE_SOL_TREASURY on the server."
        ),
        "assets": [
            {
                "id": "sol",
                "symbol": "SOL",
                "chain": "solana",
                "decimals": 9,
                "deposit_address": sol_treas,
                "min": _env_float("HOWL_BRIDGE_MIN_SOL", 0.01),
                "max": _env_float("HOWL_BRIDGE_MAX_SOL", 10.0),
                "howl_per_unit": howl_per_sol,
            },
            {
                "id": "usdc",
                "symbol": "USDC",
                "chain": "solana",
                "mint": USDC_MINT,
                "decimals": 6,
                "deposit_address": usdc_treas,
                "min": _env_float("HOWL_BRIDGE_MIN_USDC", 1.0),
                "max": _env_float("HOWL_BRIDGE_MAX_USDC", 5000.0),
                "howl_per_unit": howl_per_usdc,
            },
        ],
        "fee_bps": fee_bps,
        "fee_pct": fee_bps / 100.0,
        "order_ttl_sec": _env_int("HOWL_BRIDGE_ORDER_TTL_SEC", 3600),
        "status_poll_sec": 8,
    }


def quote_howl(asset: str, amount_in: float) -> Dict[str, Any]:
    cfg = bridge_config()
    a = next((x for x in cfg["assets"] if x["id"] == asset), None)
    if not a:
        raise ValueError(f"unsupported asset {asset}")
    if amount_in < float(a["min"]):
        raise ValueError(f"minimum {a['min']} {a['symbol']}")
    if amount_in > float(a["max"]):
        raise ValueError(f"maximum {a['max']} {a['symbol']}")
    gross = amount_in * float(a["howl_per_unit"])
    fee_bps = int(cfg["fee_bps"])
    fee = gross * fee_bps / 10_000.0
    net = max(0.0, gross - fee)
    howlies = int(net * COIN)
    return {
        "asset": asset,
        "symbol": a["symbol"],
        "amount_in": amount_in,
        "howl_per_unit": a["howl_per_unit"],
        "gross_howl": round(gross, 8),
        "fee_howl": round(fee, 8),
        "fee_bps": fee_bps,
        "net_howl": round(net, 8),
        "net_howlies": howlies,
        "deposit_address": a["deposit_address"],
        "decimals": a["decimals"],
        "mint": a.get("mint"),
    }


def _load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"orders": []}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {"orders": []}


def _save(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2))
    tmp.replace(path)


def _expire(orders: List[Dict[str, Any]]) -> None:
    now = time.time()
    for o in orders:
        if o.get("status") == "awaiting_deposit" and now > float(o.get("expires_at") or 0):
            o["status"] = "expired"
            o["updated_at"] = now


def list_orders(dd: Optional[Path] = None, howl_address: str = "") -> List[Dict[str, Any]]:
    path = orders_path(dd)
    with _lock:
        data = _load(path)
        _expire(data.get("orders") or [])
        _save(path, data)
        orders = list(data.get("orders") or [])
    if howl_address:
        orders = [o for o in orders if o.get("howl_address") == howl_address]
    orders.sort(key=lambda o: float(o.get("created_at") or 0), reverse=True)
    return orders


def get_order(order_id: str, dd: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    for o in list_orders(dd):
        if o.get("id") == order_id:
            return o
    return None


def create_order(
    *,
    howl_address: str,
    asset: str,
    amount_in: float,
    sol_from: str = "",
    dd: Optional[Path] = None,
) -> Dict[str, Any]:
    if not bridge_enabled():
        raise RuntimeError("bridge disabled")
    howl_address = (howl_address or "").strip()
    if not is_valid_address(howl_address):
        raise ValueError("invalid Howl address")
    asset = (asset or "sol").lower().strip()
    q = quote_howl(asset, float(amount_in))
    if not q.get("deposit_address"):
        raise RuntimeError("deposit address not configured")
    ttl = _env_int("HOWL_BRIDGE_ORDER_TTL_SEC", 3600)
    now = time.time()
    oid = "hw-" + secrets.token_hex(8)
    # expected raw amount for matching
    decimals = int(q["decimals"])
    raw = int(round(float(amount_in) * (10**decimals)))
    order = {
        "id": oid,
        "status": "awaiting_deposit",
        "asset": asset,
        "symbol": q["symbol"],
        "howl_address": howl_address,
        "sol_from": (sol_from or "").strip(),
        "amount_in": q["amount_in"],
        "amount_in_raw": str(raw),
        "decimals": decimals,
        "mint": q.get("mint"),
        "deposit_address": q["deposit_address"],
        "howl_per_unit": q["howl_per_unit"],
        "fee_bps": q["fee_bps"],
        "gross_howl": q["gross_howl"],
        "fee_howl": q["fee_howl"],
        "net_howl": q["net_howl"],
        "net_howlies": q["net_howlies"],
        "memo": oid,  # put in Solana memo if wallet supports it
        "deposit_tx": "",
        "howl_txid": "",
        "created_at": now,
        "updated_at": now,
        "expires_at": now + ttl,
        "note": f"Send exactly {q['amount_in']} {q['symbol']} to deposit_address. Optional memo: {oid}",
    }
    path = orders_path(dd)
    with _lock:
        data = _load(path)
        orders = data.get("orders") or []
        _expire(orders)
        orders.insert(0, order)
        # keep last 500
        data["orders"] = orders[:500]
        _save(path, data)
    return order


def update_order(order_id: str, patch: Dict[str, Any], dd: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    path = orders_path(dd)
    with _lock:
        data = _load(path)
        orders = data.get("orders") or []
        for o in orders:
            if o.get("id") == order_id:
                o.update(patch)
                o["updated_at"] = time.time()
                _save(path, data)
                return dict(o)
    return None


def attach_deposit_tx(order_id: str, deposit_tx: str, dd: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    deposit_tx = (deposit_tx or "").strip()
    if len(deposit_tx) < 32:
        raise ValueError("invalid deposit tx signature")
    o = get_order(order_id, dd)
    if not o:
        return None
    if o.get("status") not in ("awaiting_deposit", "confirming"):
        raise ValueError(f"order status is {o.get('status')}")
    return update_order(
        order_id,
        {"deposit_tx": deposit_tx, "status": "confirming"},
        dd,
    )


def admin_secret_ok(secret: str) -> bool:
    expected = _env("HOWL_BRIDGE_ADMIN_SECRET")
    if not expected:
        return False
    return secrets.compare_digest(secret or "", expected)
