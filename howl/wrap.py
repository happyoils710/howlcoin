"""
Wrapped HOWL (wHOWL) — native HOWL L1 ↔ Solana SPL mint.

Semi-custodial wrap (same trust model as Howl Swap Phase A):
  wrap:   send native HOWL → mint authority credits SPL to user's Solana ATA
  unwrap: send SPL to treasury ATA → burn/lock + credit native HOWL on L1

Env:
  HOWL_SPL_MINT=<mint base58>
  HOWL_WRAP_ENABLED=1
  HOWL_WRAP_HOWL_DEPOSIT=<Howl L1 address receiving wrap deposits>
  HOWL_WRAP_SOL_TREASURY=<Solana address; SPL unwrap deposits + mint authority>
  HOWL_WRAP_FEE_BPS=50
  HOWL_WRAP_MIN_HOWL=1
  HOWL_WRAP_MAX_HOWL=10000000
  HOWL_WRAP_DATA=/var/lib/howlcoin
  HOWL_WRAP_ADMIN_SECRET=...
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

WRAP_FILE = "wrap_orders.json"
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


def data_dir(fallback: Optional[Path] = None) -> Path:
    raw = _env("HOWL_WRAP_DATA") or _env("HOWL_BRIDGE_DATA") or _env("HOWL_PUBLIC_DATA")
    if raw:
        return Path(raw).expanduser()
    if fallback:
        return Path(fallback).expanduser()
    return Path.home() / ".howlcoin"


def orders_path(dd: Optional[Path] = None) -> Path:
    return data_dir(dd) / WRAP_FILE


def spl_mint() -> str:
    return _env("HOWL_SPL_MINT")


def wrap_enabled() -> bool:
    v = _env("HOWL_WRAP_ENABLED", "").lower()
    if v in ("0", "false", "no", "off"):
        return False
    if v in ("1", "true", "yes", "on"):
        return bool(spl_mint())
    # auto-enable when mint is configured
    return bool(spl_mint())


def wrap_config() -> Dict[str, Any]:
    mint = spl_mint()
    howl_deposit = _env("HOWL_WRAP_HOWL_DEPOSIT") or _env("HOWL_BRIDGE_HOT_ADDRESS")
    # hot wallet address often only in bootstrap json — allow empty in config
    sol_treas = _env("HOWL_WRAP_SOL_TREASURY") or _env("HOWL_BRIDGE_SOL_TREASURY")
    fee_bps = max(0, min(2000, _env_int("HOWL_WRAP_FEE_BPS", 50)))
    enabled = wrap_enabled() and bool(mint)
    return {
        "enabled": enabled,
        "engine": "Howl Wrap",
        "phase": "SPL",
        "symbol": "wHOWL",
        "name": "Wrapped HOWL",
        "decimals": 8,
        "mint": mint or None,
        "mint_explorer": f"https://solscan.io/token/{mint}" if mint else None,
        "howl_deposit_address": howl_deposit or None,
        "sol_treasury": sol_treas or None,
        "fee_bps": fee_bps,
        "fee_pct": fee_bps / 100.0,
        "min_howl": _env_float("HOWL_WRAP_MIN_HOWL", 1.0),
        "max_howl": _env_float("HOWL_WRAP_MAX_HOWL", 10_000_000.0),
        "ratio": "1 native HOWL ≈ 1 wHOWL (minus fee)",
        "note": (
            "Semi-custodial wrap: lock native HOWL on Howl L1 to mint wHOWL (SPL) on Solana, "
            "or send wHOWL to the treasury to unwrap back to native HOWL. Not trustless."
            if enabled
            else "Wrap offline — create mint (scripts/create-howl-spl-mint.sh) and set HOWL_SPL_MINT."
        ),
        "directions": [
            {
                "id": "wrap",
                "label": "Wrap · HOWL → wHOWL",
                "from": "Howl L1",
                "to": "Solana SPL",
                "deposit": "native HOWL",
                "receive": "wHOWL SPL",
            },
            {
                "id": "unwrap",
                "label": "Unwrap · wHOWL → HOWL",
                "from": "Solana SPL",
                "to": "Howl L1",
                "deposit": "wHOWL SPL",
                "receive": "native HOWL",
            },
        ],
    }


def _load_orders(dd: Optional[Path] = None) -> List[Dict[str, Any]]:
    path = orders_path(dd)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
        if isinstance(data, list):
            return data
        if isinstance(data, dict) and isinstance(data.get("orders"), list):
            return data["orders"]
    except Exception:
        pass
    return []


def _save_orders(orders: List[Dict[str, Any]], dd: Optional[Path] = None) -> None:
    path = orders_path(dd)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps({"orders": orders, "updated": int(time.time())}, indent=2))
    tmp.replace(path)


def quote_wrap(amount_howl: float, direction: str = "wrap") -> Dict[str, Any]:
    cfg = wrap_config()
    direction = (direction or "wrap").lower().strip()
    if direction not in ("wrap", "unwrap"):
        raise ValueError("direction must be wrap or unwrap")
    amt = float(amount_howl)
    if amt < cfg["min_howl"]:
        raise ValueError(f"min {cfg['min_howl']} HOWL")
    if amt > cfg["max_howl"]:
        raise ValueError(f"max {cfg['max_howl']} HOWL")
    fee_bps = int(cfg["fee_bps"])
    fee = amt * fee_bps / 10_000.0
    out = amt - fee
    if out <= 0:
        raise ValueError("amount too small after fee")
    return {
        "direction": direction,
        "amount_in": amt,
        "fee_howl": fee,
        "fee_bps": fee_bps,
        "amount_out": out,
        "in_asset": "HOWL" if direction == "wrap" else "wHOWL",
        "out_asset": "wHOWL" if direction == "wrap" else "HOWL",
        "mint": cfg.get("mint"),
        "enabled": cfg["enabled"],
    }


def create_order(
    *,
    direction: str,
    amount_howl: float,
    howl_address: str,
    sol_address: str,
    dd: Optional[Path] = None,
) -> Dict[str, Any]:
    if not wrap_enabled():
        raise RuntimeError("wrap not enabled")
    direction = (direction or "wrap").lower().strip()
    if direction not in ("wrap", "unwrap"):
        raise ValueError("direction must be wrap or unwrap")
    howl_address = (howl_address or "").strip()
    sol_address = (sol_address or "").strip()
    if not is_valid_address(howl_address):
        raise ValueError("invalid Howl address")
    if len(sol_address) < 32 or len(sol_address) > 48:
        raise ValueError("invalid Solana address")
    q = quote_wrap(amount_howl, direction)
    cfg = wrap_config()
    oid = "hw-" + secrets.token_hex(8)
    now = int(time.time())
    ttl = _env_int("HOWL_WRAP_ORDER_TTL_SEC", 3600)
    order: Dict[str, Any] = {
        "id": oid,
        "direction": direction,
        "status": "awaiting_deposit",
        "howl_address": howl_address,
        "sol_address": sol_address,
        "amount_in": q["amount_in"],
        "amount_out": q["amount_out"],
        "fee_howl": q["fee_howl"],
        "fee_bps": q["fee_bps"],
        "mint": cfg.get("mint"),
        "deposit_howl_address": cfg.get("howl_deposit_address"),
        "deposit_sol_treasury": cfg.get("sol_treasury"),
        "created_at": now,
        "expires_at": now + ttl,
        "deposit_txid": None,
        "payout_txid": None,
        "memo": oid,
        "error": None,
    }
    with _lock:
        orders = _load_orders(dd)
        orders.insert(0, order)
        _save_orders(orders[:500], dd)
    return order


def get_order(oid: str, dd: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    oid = (oid or "").strip()
    with _lock:
        for o in _load_orders(dd):
            if o.get("id") == oid:
                return o
    return None


def list_orders(
    *,
    howl: str = "",
    sol: str = "",
    limit: int = 50,
    dd: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    howl = (howl or "").strip()
    sol = (sol or "").strip()
    out: List[Dict[str, Any]] = []
    with _lock:
        for o in _load_orders(dd):
            if howl and o.get("howl_address") != howl:
                continue
            if sol and o.get("sol_address") != sol:
                continue
            out.append(o)
            if len(out) >= limit:
                break
    return out


def update_order(oid: str, **fields: Any) -> Optional[Dict[str, Any]]:
    oid = (oid or "").strip()
    with _lock:
        orders = _load_orders()
        for i, o in enumerate(orders):
            if o.get("id") == oid:
                o = {**o, **fields, "updated_at": int(time.time())}
                orders[i] = o
                _save_orders(orders)
                return o
    return None


def attach_deposit_tx(oid: str, txid: str) -> Dict[str, Any]:
    txid = (txid or "").strip()
    if len(txid) < 8:
        raise ValueError("bad deposit tx id")
    o = update_order(oid, deposit_txid=txid, status="confirming")
    if not o:
        raise ValueError("order not found")
    return o


def admin_secret_ok(secret: str) -> bool:
    expected = _env("HOWL_WRAP_ADMIN_SECRET") or _env("HOWL_BRIDGE_ADMIN_SECRET")
    return bool(expected) and secrets.compare_digest(expected, (secret or "").strip())
