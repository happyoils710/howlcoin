#!/usr/bin/env python3
"""
Howl Wrap relayer — native HOWL ↔ wHOWL (SPL).

wrap:   detect HOWL deposit to wrap address → mint SPL to user ATA
unwrap: detect SPL transfer to treasury ATA → send native HOWL

Env: see howl/wrap.py + HOWL_BRIDGE_HOT_WALLET, SOLANA_RPC, keypair path.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from howl.config import COIN, DEFAULT_TX_FEE_HOWLIES, MIN_TX_FEE_HOWLIES  # noqa: E402
from howl.wallet import Wallet  # noqa: E402
from howl.wrap import (  # noqa: E402
    get_order,
    list_orders,
    orders_path,
    spl_mint,
    update_order,
    wrap_config,
    wrap_enabled,
)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def sol_rpc(method: str, params: list, timeout: int = 30):
    url = env("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def howl_rpc(path: str, payload: dict | None = None) -> dict:
    base = env("HOWL_NODE_RPC", "http://127.0.0.1:42070").rstrip("/")
    url = base + path
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"} if data else {},
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def load_hot_wallet() -> Wallet:
    path = Path(env("HOWL_BRIDGE_HOT_WALLET") or env("HOWL_WRAP_HOT_WALLET") or "").expanduser()
    if not path.is_file():
        raise SystemExit("set HOWL_BRIDGE_HOT_WALLET to hot wallet json")
    return Wallet(path, create_if_missing=False)


def spl_token_bin() -> str:
    for p in (
        "spl-token",
        str(Path.home() / ".local/share/solana/install/active_release/bin/spl-token"),
        "/root/.local/share/solana/install/active_release/bin/spl-token",
    ):
        if p == "spl-token" or Path(p).is_file():
            try:
                subprocess.run([p if p != "spl-token" else "spl-token", "--version"], capture_output=True, check=True)
                return p if p != "spl-token" else "spl-token"
            except Exception:
                continue
    raise SystemExit("spl-token CLI not found — run scripts/create-howl-spl-mint.sh first")


def keypair_path() -> Path:
    p = Path(
        env("HOWL_WRAP_SOL_KEYPAIR")
        or env("HOWL_BRIDGE_SOL_KEYPAIR")
        or "/var/lib/howlcoin/bridge-sol-treasury.json"
    )
    if not p.is_file():
        raise SystemExit(f"missing Solana keypair {p}")
    return p


def mint_whowl(to_sol: str, amount: float, mint: str) -> str:
    """Mint amount wHOWL (UI units) to to_sol owner ATA. Returns signature or CLI output."""
    kp = str(keypair_path())
    bin_ = spl_token_bin()
    # amount as UI units; spl-token uses UI amount with --
    cmd = [
        bin_,
        "mint",
        mint,
        f"{amount:.8f}",
        to_sol,
        "--fee-payer",
        kp,
        "--mint-authority",
        kp,
        "--owner",
        to_sol,
    ]
    # Ensure ATA exists
    subprocess.run(
        [bin_, "create-account", mint, "--owner", to_sol, "--fee-payer", kp],
        capture_output=True,
        text=True,
    )
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout or "mint failed")
    # parse signature if present
    out = (r.stdout or "") + (r.stderr or "")
    for line in out.splitlines():
        if "Signature" in line or "signature" in line:
            parts = line.split()
            for p in parts:
                if len(p) >= 64:
                    return p
    return out.strip()[:120] or "minted"


def _chain_height(chain) -> int:
    h = getattr(chain, "height", 0)
    try:
        return int(h() if callable(h) else h or 0)
    except Exception:
        return 0


def _block_ts(block) -> int:
    if block is None:
        return 0
    if isinstance(block, dict):
        if block.get("timestamp") is not None:
            try:
                return int(block["timestamp"])
            except Exception:
                pass
        hdr = block.get("header") or {}
        if isinstance(hdr, dict) and hdr.get("timestamp") is not None:
            try:
                return int(hdr["timestamp"])
            except Exception:
                pass
        return 0
    return int(getattr(block, "timestamp", 0) or 0)


def find_howl_deposit(deposit_addr: str, amount_howlies: int, since_ts: float, memo: str = "") -> str:
    """Scan L1 chain for inbound HOWL matching amount to deposit address."""
    from howl.blockchain import Blockchain

    data_dir = Path(
        env("HOWL_PUBLIC_DATA") or env("HOWL_WRAP_DATA") or env("HOWL_BRIDGE_DATA") or "/var/lib/howlcoin"
    ).expanduser()
    chain = Blockchain(data_dir)
    tol = max(COIN // 100, amount_howlies // 1000)
    tip = _chain_height(chain)
    start = max(0, tip - 800)
    for h in range(tip, start - 1, -1):
        try:
            block = chain.get_block(h)
            if block is None:
                block = chain.get_block(str(h))
        except Exception:
            continue
        if not block:
            continue
        ts = _block_ts(block)
        if ts and since_ts and ts < since_ts - 7200:
            # keep scanning a bit — timestamps on Howl can lag wall clock
            pass
        txs = getattr(block, "transactions", None) or (block.get("transactions") if isinstance(block, dict) else []) or []
        for tx in txs:
            if isinstance(tx, dict):
                to = tx.get("to") or ""
                amt = int(tx.get("amount") or 0)
                txid = tx.get("txid") or ""
                m = str(tx.get("memo") or "")
            else:
                to = getattr(tx, "to", "") or ""
                amt = int(getattr(tx, "amount", 0) or 0)
                txid = getattr(tx, "txid", "") or ""
                m = str(getattr(tx, "memo", "") or "")
            if to != deposit_addr:
                continue
            if abs(amt - amount_howlies) > tol:
                continue
            # Prefer memo match when provided, but accept blank-memo deposits of exact amount
            if memo and m and (memo not in m) and (memo[:12] not in m):
                continue
            if txid:
                return txid
    return ""


def list_orphan_howl_deposits(deposit_addr: str, lookback: int = 500) -> list:
    """Inbound HOWL to wrap deposit not already claimed by a wrap order."""
    from howl.blockchain import Blockchain

    data_dir = Path(
        env("HOWL_PUBLIC_DATA") or env("HOWL_WRAP_DATA") or env("HOWL_BRIDGE_DATA") or "/var/lib/howlcoin"
    ).expanduser()
    chain = Blockchain(data_dir)
    claimed = set()
    for o in list_orders(limit=500):
        if o.get("direction") == "wrap" and o.get("deposit_txid"):
            claimed.add(o["deposit_txid"])
    tip = _chain_height(chain)
    start = max(0, tip - lookback)
    found = []
    for h in range(start, tip + 1):
        try:
            block = chain.get_block(h) or chain.get_block(str(h))
        except Exception:
            continue
        if not block:
            continue
        txs = (block.get("transactions") if isinstance(block, dict) else getattr(block, "transactions", None)) or []
        for tx in txs:
            if not isinstance(tx, dict):
                continue
            if (tx.get("to") or "") != deposit_addr:
                continue
            txid = tx.get("txid") or ""
            if not txid or txid in claimed:
                continue
            amt = int(tx.get("amount") or 0)
            found.append({
                "height": h if isinstance(block, dict) else getattr(block, "height", h),
                "txid": txid,
                "from": tx.get("from") or "",
                "amount_howl": amt / COIN,
                "amount_howlies": amt,
                "memo": tx.get("memo") or "",
            })
    return found


def find_spl_deposit(treasury: str, mint: str, amount_raw: int, since_ts: float) -> str:
    """Find inbound SPL transfer of mint to treasury (amount in base units)."""
    # get token accounts for treasury
    accs = sol_rpc(
        "getTokenAccountsByOwner",
        [treasury, {"mint": mint}, {"encoding": "jsonParsed"}],
    ) or {}
    value = accs.get("value") or []
    for acc in value:
        pubkey = acc.get("pubkey") or ""
        if not pubkey:
            continue
        sigs = sol_rpc("getSignaturesForAddress", [pubkey, {"limit": 20}]) or []
        for s in sigs:
            sig = s.get("signature") or ""
            block_time = s.get("blockTime") or 0
            if block_time and block_time < since_ts - 120:
                continue
            tx = sol_rpc("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
            if not tx:
                continue
            meta = tx.get("meta") or {}
            if meta.get("err"):
                continue
            # crude: look for token balance increase on treasury ATA
            pre = meta.get("preTokenBalances") or []
            post = meta.get("postTokenBalances") or []
            # map account index
            pre_amt = 0
            post_amt = 0
            for b in pre:
                if b.get("mint") == mint and b.get("owner") == treasury:
                    pre_amt = int(b.get("uiTokenAmount", {}).get("amount") or 0)
            for b in post:
                if b.get("mint") == mint and b.get("owner") == treasury:
                    post_amt = int(b.get("uiTokenAmount", {}).get("amount") or 0)
            delta = post_amt - pre_amt
            if delta > 0 and abs(delta - amount_raw) <= max(1, amount_raw // 1000):
                return sig
    return ""


def process_wrap(order: dict, hot: Wallet) -> None:
    cfg = wrap_config()
    mint = order.get("mint") or cfg.get("mint") or spl_mint()
    deposit = order.get("deposit_howl_address") or cfg.get("howl_deposit_address") or hot.address
    amount_in = float(order["amount_in"])
    amount_out = float(order["amount_out"])
    amount_howlies = int(round(amount_in * COIN))
    since = float(order.get("created_at") or time.time()) - 60

    txid = order.get("deposit_txid") or ""
    if not txid:
        txid = find_howl_deposit(deposit, amount_howlies, since, order.get("memo") or order.get("id") or "")
        if txid:
            update_order(order["id"], deposit_txid=txid, status="confirming")
            print(f"  wrap {order['id']}: deposit {txid[:16]}…")
        else:
            return
    # mint SPL
    try:
        sig = mint_whowl(order["sol_address"], amount_out, mint)
        update_order(order["id"], status="completed", payout_txid=sig, error=None)
        print(f"  wrap {order['id']}: minted {amount_out} wHOWL → {order['sol_address'][:8]}… {sig}")
    except Exception as e:
        update_order(order["id"], status="error", error=str(e)[:200])
        print(f"  wrap {order['id']}: mint error {e}")


def send_howl(to: str, amount_howlies: int, memo: str = "") -> str:
    """Build+broadcast HOWL from hot wallet (same pattern as Howl Swap relayer)."""
    from howl.blockchain import Blockchain
    from howl.wallet import format_howl

    wallet_path = Path(env("HOWL_BRIDGE_HOT_WALLET") or env("HOWL_WRAP_HOT_WALLET") or "").expanduser()
    if not wallet_path.is_file():
        raise RuntimeError("HOWL_BRIDGE_HOT_WALLET not set")
    node = env("HOWL_NODE_RPC", "http://127.0.0.1:42070").rstrip("/")
    data_dir = Path(env("HOWL_PUBLIC_DATA") or env("HOWL_WRAP_DATA") or env("HOWL_BRIDGE_DATA") or "/var/lib/howlcoin").expanduser()
    w = Wallet(wallet_path, create_if_missing=False)
    from_key = w.primary
    chain = Blockchain(data_dir)
    nonce = chain.next_nonce(from_key.address)
    bal = chain.balance(from_key.address)
    fee = max(MIN_TX_FEE_HOWLIES, DEFAULT_TX_FEE_HOWLIES)
    if bal < amount_howlies + fee:
        raise RuntimeError(f"hot low: have {format_howl(bal)}, need {format_howl(amount_howlies + fee)}")
    tx = w.build_tx(
        to=to,
        amount_howlies=amount_howlies,
        nonce=nonce,
        fee=fee,
        memo=memo or "howl-wrap",
        key=from_key,
    )
    body = json.dumps({"tx": tx}).encode()
    req = urllib.request.Request(
        f"{node}/api/broadcast",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return data.get("txid") or tx.get("txid") or ""
    except Exception:
        ok, msg = chain.add_to_mempool(tx)
        if not ok:
            raise RuntimeError(f"mempool reject: {msg}")
        return msg


def process_unwrap(order: dict, hot: Wallet) -> None:
    cfg = wrap_config()
    mint = order.get("mint") or cfg.get("mint") or spl_mint()
    treasury = order.get("deposit_sol_treasury") or cfg.get("sol_treasury")
    amount_in = float(order["amount_in"])
    amount_out = float(order["amount_out"])
    amount_raw = int(round(amount_in * (10**8)))
    since = float(order.get("created_at") or time.time()) - 60

    sig = order.get("deposit_txid") or ""
    if not sig:
        sig = find_spl_deposit(treasury, mint, amount_raw, since)
        if sig:
            update_order(order["id"], deposit_txid=sig, status="confirming")
            print(f"  unwrap {order['id']}: SPL deposit {sig[:16]}…")
        else:
            return
    try:
        howlies = int(round(amount_out * COIN))
        txid = send_howl(order["howl_address"], howlies, memo=order.get("id") or "unwrap")
        update_order(order["id"], status="completed", payout_txid=str(txid), error=None)
        print(f"  unwrap {order['id']}: paid {amount_out} HOWL → {order['howl_address'][:10]}…")
    except Exception as e:
        update_order(order["id"], status="error", error=str(e)[:200])
        print(f"  unwrap {order['id']}: pay error {e}")


def tick() -> None:
    if not wrap_enabled():
        print("wrap disabled (set HOWL_SPL_MINT + HOWL_WRAP_ENABLED=1)")
        return
    cfg = wrap_config()
    print(f"wrap tick mint={cfg.get('mint')} deposit={cfg.get('howl_deposit_address')}")
    hot = load_hot_wallet()
    # ensure deposit address matches hot if empty
    if not cfg.get("howl_deposit_address"):
        os.environ.setdefault("HOWL_WRAP_HOWL_DEPOSIT", hot.address)
    open_orders = [
        o
        for o in list_orders(limit=100)
        if o.get("status") in ("awaiting_deposit", "confirming")
        and (o.get("expires_at") or 0) > time.time() - 3600
    ]
    for o in open_orders:
        try:
            if o.get("direction") == "wrap":
                process_wrap(o, hot)
            elif o.get("direction") == "unwrap":
                process_unwrap(o, hot)
        except Exception as e:
            print(f"  order {o.get('id')} error: {e}")


def fulfill_wrap_deposit(txid: str, sol_address: str, amount_howl: float | None = None) -> None:
    """
    Manual recovery: mint wHOWL for an existing HOWL deposit that had no wrap order.
    Usage:
      HOWL_BRIDGE_HOT_WALLET=... python3 scripts/howl-wrap-relayer.py \
        --fulfill-txid <howl_txid> --sol <user_sol_address> [--amount 10]
    """
    from howl.blockchain import Blockchain
    from howl.wrap import create_order, quote_wrap

    data_dir = Path(
        env("HOWL_PUBLIC_DATA") or env("HOWL_WRAP_DATA") or env("HOWL_BRIDGE_DATA") or "/var/lib/howlcoin"
    ).expanduser()
    chain = Blockchain(data_dir)
    cfg = wrap_config()
    deposit = cfg.get("howl_deposit_address") or ""
    tip = _chain_height(chain)
    found = None
    for h in range(max(0, tip - 2000), tip + 1):
        try:
            block = chain.get_block(h) or chain.get_block(str(h))
        except Exception:
            continue
        if not block:
            continue
        for tx in (block.get("transactions") if isinstance(block, dict) else []) or []:
            if isinstance(tx, dict) and (tx.get("txid") or "") == txid:
                found = tx
                break
        if found:
            break
    if not found:
        raise SystemExit(f"txid not found on chain: {txid}")
    if (found.get("to") or "") != deposit:
        raise SystemExit(f"txid to={found.get('to')} is not wrap deposit {deposit}")
    amt_howlies = int(found.get("amount") or 0)
    amt = amount_howl if amount_howl is not None else (amt_howlies / COIN)
    howl_from = found.get("from") or deposit
    q = quote_wrap(amt, "wrap")
    order = create_order(
        direction="wrap",
        amount_howl=amt,
        howl_address=howl_from,
        sol_address=sol_address,
    )
    update_order(
        order["id"],
        deposit_txid=txid,
        status="confirming",
        amount_in=q["amount_in"],
        amount_out=q["amount_out"],
        fee_howl=q["fee_howl"],
    )
    order = get_order(order["id"]) or order
    print(f"created order {order['id']} for {q['amount_out']} wHOWL → {sol_address}")
    hot = load_hot_wallet()
    process_wrap(order, hot)
    print("done · check order status / Solscan mint supply")


def main() -> None:
    ap = argparse.ArgumentParser(description="Howl Wrap relayer")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=int, default=30)
    ap.add_argument("--list-orphans", action="store_true", help="List HOWL deposits with no wrap order")
    ap.add_argument("--fulfill-txid", default="", help="Howl L1 deposit txid to fulfill manually")
    ap.add_argument("--sol", default="", help="User Solana address to receive wHOWL (with --fulfill-txid)")
    ap.add_argument("--amount", type=float, default=None, help="Override amount HOWL for fulfill")
    args = ap.parse_args()
    print("Howl Wrap relayer · orders", orders_path())
    if args.list_orphans:
        cfg = wrap_config()
        dep = cfg.get("howl_deposit_address") or ""
        rows = list_orphan_howl_deposits(dep)
        print(json.dumps(rows, indent=2))
        if not rows:
            print("(no orphan deposits in lookback)")
        return
    if args.fulfill_txid:
        if not args.sol:
            raise SystemExit("--sol USER_SOLANA_ADDRESS required with --fulfill-txid")
        fulfill_wrap_deposit(args.fulfill_txid.strip(), args.sol.strip(), args.amount)
        return
    if args.once:
        tick()
        return
    while True:
        try:
            tick()
            # surface orphans occasionally
            try:
                cfg = wrap_config()
                dep = cfg.get("howl_deposit_address") or ""
                orphans = list_orphan_howl_deposits(dep, lookback=200)
                if orphans:
                    print(f"  WARN {len(orphans)} orphan HOWL deposit(s) without wrap order — use --list-orphans / --fulfill-txid")
                    for o in orphans[-3:]:
                        print(f"    h={o['height']} {o['amount_howl']} HOWL tx={o['txid'][:16]}… from={o['from'][:12]}…")
            except Exception as e:
                print("  orphan scan", e)
        except Exception as e:
            print("tick error", e)
        time.sleep(max(10, args.interval))


if __name__ == "__main__":
    main()
