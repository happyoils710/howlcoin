#!/usr/bin/env python3
"""
Howl Swap relayer — watch Solana deposits and credit native HOWL.

Setup:
  export HOWL_BRIDGE_ENABLED=1
  export HOWL_BRIDGE_SOL_TREASURY=<sol address that receives deposits>
  export HOWL_BRIDGE_HOWL_PER_SOL=100000
  export HOWL_BRIDGE_DATA=/var/lib/howlcoin
  export HOWL_BRIDGE_HOT_WALLET=/path/to/hot-wallet.json   # howl wallet.json with HOWL
  export HOWL_NODE_RPC=http://127.0.0.1:42070              # seed dashboard RPC
  export HOWL_PUBLIC_DATA=/var/lib/howlcoin
  export SOLANA_RPC=https://api.mainnet-beta.solana.com

  python3 scripts/howl-bridge-relayer.py
  # or: python3 scripts/howl-bridge-relayer.py --once

Does NOT spend SOL. Only observes treasury inbound SOL (native) and optional
USDC SPL transfers, then sends HOWL from the hot wallet.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# repo root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from howl.bridge import (  # noqa: E402
    USDC_MINT,
    get_order,
    list_orders,
    orders_path,
    update_order,
)
from howl.config import DEFAULT_TX_FEE_HOWLIES, MIN_TX_FEE_HOWLIES  # noqa: E402
from howl.wallet import Wallet, format_howl  # noqa: E402


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def rpc_json(url: str, method: str, params: list, timeout: int = 25) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    return data.get("result")


def sol_rpc(method: str, params: list):
    url = env("SOLANA_RPC", "https://api.mainnet-beta.solana.com")
    return rpc_json(url, method, params)


def find_sol_deposit(treasury: str, amount_lamports: int, since_ts: float, memo: str = "") -> str:
    """Return signature of matching inbound SOL transfer, or ''."""
    sigs = sol_rpc(
        "getSignaturesForAddress",
        [treasury, {"limit": 25}],
    ) or []
    tol = max(1000, amount_lamports // 1000)  # 0.1% or 1000 lamports
    for s in sigs:
        sig = s.get("signature") or ""
        if not sig:
            continue
        # blockTime may be null for very new
        bt = s.get("blockTime")
        if bt is not None and bt + 30 < since_ts:
            continue
        try:
            tx = sol_rpc(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception:
            continue
        if not tx:
            continue
        meta = tx.get("meta") or {}
        if meta.get("err"):
            continue
        # balance change for treasury
        msg = (tx.get("transaction") or {}).get("message") or {}
        keys = msg.get("accountKeys") or []
        # accountKeys may be list of str or {pubkey}
        pubs = []
        for k in keys:
            if isinstance(k, str):
                pubs.append(k)
            elif isinstance(k, dict):
                pubs.append(k.get("pubkey") or "")
        if treasury not in pubs:
            continue
        idx = pubs.index(treasury)
        pre = (meta.get("preBalances") or [0])[idx] if idx < len(meta.get("preBalances") or []) else 0
        post = (meta.get("postBalances") or [0])[idx] if idx < len(meta.get("postBalances") or []) else 0
        delta = int(post) - int(pre)
        if delta <= 0:
            continue
        if abs(delta - amount_lamports) <= tol:
            return sig
        # memo match + any positive if amount close
        if memo and memo in json.dumps(tx):
            if abs(delta - amount_lamports) <= max(tol, amount_lamports // 50):
                return sig
    return ""


def find_usdc_deposit(treasury: str, amount_raw: int, since_ts: float) -> str:
    """Best-effort: scan recent txs for SPL USDC credit to treasury-owned accounts."""
    sigs = sol_rpc("getSignaturesForAddress", [treasury, {"limit": 25}]) or []
    tol = max(10, amount_raw // 1000)
    for s in sigs:
        sig = s.get("signature") or ""
        bt = s.get("blockTime")
        if bt is not None and bt + 30 < since_ts:
            continue
        try:
            tx = sol_rpc(
                "getTransaction",
                [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}],
            )
        except Exception:
            continue
        if not tx or (tx.get("meta") or {}).get("err"):
            continue
        blob = json.dumps(tx)
        if USDC_MINT not in blob:
            continue
        # parse token balance changes
        meta = tx.get("meta") or {}
        pre = meta.get("preTokenBalances") or []
        post = meta.get("postTokenBalances") or []
        # map accountIndex -> amount
        def amt_map(rows):
            m = {}
            for r in rows:
                if r.get("mint") != USDC_MINT:
                    continue
                owner = (r.get("owner") or "")
                ui = ((r.get("uiTokenAmount") or {}).get("amount")) or "0"
                m[owner] = m.get(owner, 0) + int(ui)
            return m

        pre_m = amt_map(pre)
        post_m = amt_map(post)
        delta = int(post_m.get(treasury, 0)) - int(pre_m.get(treasury, 0))
        if delta > 0 and abs(delta - amount_raw) <= tol:
            return sig
    return ""


def send_howl(to: str, amount_howlies: int, memo: str = "") -> str:
    """Build+broadcast HOWL transfer from hot wallet via node RPC."""
    wallet_path = Path(env("HOWL_BRIDGE_HOT_WALLET", "")).expanduser()
    if not wallet_path.is_file():
        raise RuntimeError("HOWL_BRIDGE_HOT_WALLET not set or missing")
    node = env("HOWL_NODE_RPC", "http://127.0.0.1:42070").rstrip("/")
    data_dir = Path(env("HOWL_PUBLIC_DATA") or env("HOWL_BRIDGE_DATA") or "~/.howlcoin").expanduser()

    # load chain for nonce/balance via status API if possible
    try:
        req = urllib.request.Request(f"{node}/api/status", method="GET")
        with urllib.request.urlopen(req, timeout=12) as resp:
            status = json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"node status failed: {e}") from e

    w = Wallet(wallet_path, create_if_missing=False)
    from_key = w.primary
    # fetch nonce for hot address from public address endpoint if explorer available
    # Prefer node dashboard address if same wallet; else use chain file
    from howl.blockchain import Blockchain

    chain = Blockchain(data_dir)
    nonce = chain.next_nonce(from_key.address)
    bal = chain.balance(from_key.address)
    fee = max(MIN_TX_FEE_HOWLIES, DEFAULT_TX_FEE_HOWLIES)
    if bal < amount_howlies + fee:
        raise RuntimeError(
            f"hot wallet low balance: have {format_howl(bal)}, need {format_howl(amount_howlies + fee)}"
        )
    tx = w.build_tx(
        to=to,
        amount_howlies=amount_howlies,
        nonce=nonce,
        fee=fee,
        memo=memo or "howl-swap",
        key=from_key,
    )
    # broadcast via node
    try:
        body = json.dumps({"tx": tx}).encode()
        req = urllib.request.Request(
            f"{node}/api/broadcast",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
        return data.get("txid") or tx.get("txid") or ""
    except Exception:
        ok, msg = chain.add_to_mempool(tx)
        if not ok:
            raise RuntimeError(f"mempool reject: {msg}")
        return msg



def mint_whowl_to(to_sol: str, amount_ui: float) -> str:
    """Mint wHOWL (UI units) to user Solana wallet using treasury mint authority."""
    import subprocess

    mint = env("HOWL_SPL_MINT")
    if not mint:
        raise RuntimeError("HOWL_SPL_MINT not set")
    kp = Path(
        env("HOWL_WRAP_SOL_KEYPAIR")
        or env("HOWL_BRIDGE_SOL_KEYPAIR")
        or "/var/lib/howlcoin/bridge-sol-treasury.json"
    )
    if not kp.is_file():
        raise RuntimeError(f"missing Solana keypair {kp}")
    # find spl-token
    candidates = [
        "spl-token",
        str(Path.home() / ".local/share/solana/install/active_release/bin/spl-token"),
        "/root/.local/share/solana/install/active_release/bin/spl-token",
    ]
    bin_ = None
    for c in candidates:
        try:
            subprocess.run([c if c != "spl-token" else "spl-token", "--version"], capture_output=True, check=True)
            bin_ = c if c != "spl-token" else "spl-token"
            break
        except Exception:
            continue
    if not bin_:
        raise RuntimeError("spl-token CLI not found")
    kp_s = str(kp)
    # ensure ATA
    ca = subprocess.run(
        [bin_, "create-account", mint, "--owner", to_sol, "--fee-payer", kp_s],
        capture_output=True,
        text=True,
    )
    if ca.returncode != 0:
        err = (ca.stderr or ca.stdout or "").lower()
        if "already" not in err and "exist" not in err:
            print("  create-account:", (ca.stderr or ca.stdout or "")[:180])
    cmd = [
        bin_, "mint", mint, f"{float(amount_ui):.8f}",
        "--recipient-owner", to_sol,
        "--fee-payer", kp_s,
        "--mint-authority", kp_s,
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "mint failed")[:400])
    out = (r.stdout or "") + "\n" + (r.stderr or "")
    for line in out.splitlines():
        if "Signature" in line or "signature" in line:
            for part in line.split():
                if len(part) >= 64:
                    return part.strip()
    return out.strip()[:120] or "minted"


def process_once(dd: Path, dry_run: bool = False) -> int:
    n = 0
    treasury = env("HOWL_BRIDGE_SOL_TREASURY")
    if not treasury:
        print("HOWL_BRIDGE_SOL_TREASURY not set", file=sys.stderr)
        return 0
    orders = list_orders(dd)
    for o in orders:
        st = o.get("status")
        if st not in ("awaiting_deposit", "confirming", "paid"):
            continue
        oid = o["id"]
        asset = o.get("asset") or "sol"
        raw = int(o.get("amount_in_raw") or 0)
        created = float(o.get("created_at") or 0)

        if st in ("awaiting_deposit", "confirming") and not o.get("deposit_tx"):
            sig = ""
            # Accept deposits up to 48h *before* order creation (user often sends first).
            # since_ts is a lower bound: ignore txs older than this.
            lookback = max(0.0, float(created or 0) - 48 * 3600)
            try:
                if asset == "sol":
                    sig = find_sol_deposit(treasury, raw, lookback, memo=oid)
                elif asset == "usdc":
                    sig = find_usdc_deposit(treasury, raw, lookback)
            except Exception as e:
                print(f"[{oid}] scan error: {e}")
                continue
            if sig:
                print(f"[{oid}] deposit seen {sig[:16]}…")
                if not dry_run:
                    update_order(oid, {"deposit_tx": sig, "status": "paid"}, dd)
                o["deposit_tx"] = sig
                o["status"] = "paid"
            else:
                continue

        if o.get("status") == "paid" and not o.get("howl_txid") and not o.get("payout_txid"):
            howlies = int(o.get("net_howlies") or 0)
            net_howl = float(o.get("net_howl") or (howlies / 1e8))
            payout = (o.get("payout") or "howl").lower()
            to = o.get("howl_address") or ""
            sol_to = (o.get("sol_from") or "").strip()
            if dry_run:
                n += 1
                continue
            try:
                if payout == "whowl":
                    if not sol_to:
                        raise RuntimeError("sol_from missing for wHOWL payout")
                    print(f"[{oid}] mint {net_howl} wHOWL → {sol_to[:12]}…")
                    sig = mint_whowl_to(sol_to, net_howl)
                    update_order(
                        oid,
                        {"status": "completed", "payout_txid": sig, "howl_txid": sig, "payout": "whowl"},
                        dd,
                    )
                    print(f"[{oid}] wHOWL mint {sig}")
                else:
                    print(f"[{oid}] credit {format_howl(howlies)} → {to[:12]}…")
                    txid = send_howl(to, howlies, memo=f"bridge:{oid}")
                    update_order(
                        oid,
                        {"status": "completed", "howl_txid": txid, "payout": "howl"},
                        dd,
                    )
                    print(f"[{oid}] HOWL tx {txid}")
                n += 1
            except Exception as e:
                print(f"[{oid}] credit failed: {e}")
                update_order(oid, {"status": "paid", "error": str(e)}, dd)
    return n


def main() -> None:
    ap = argparse.ArgumentParser(description="Howl Swap bridge relayer")
    ap.add_argument("--once", action="store_true", help="single pass then exit")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--interval", type=int, default=20)
    args = ap.parse_args()
    dd = Path(env("HOWL_BRIDGE_DATA") or env("HOWL_PUBLIC_DATA") or str(Path.home() / ".howlcoin")).expanduser()
    print(f"Howl bridge relayer · orders {orders_path(dd)}")
    print(f"  treasury {env('HOWL_BRIDGE_SOL_TREASURY') or '(unset)'}")
    print(f"  hot wallet {env('HOWL_BRIDGE_HOT_WALLET') or '(unset)'}")
    while True:
        try:
            n = process_once(dd, dry_run=args.dry_run)
            if n:
                print(f"processed {n} order(s)")
        except Exception as e:
            print(f"loop error: {e}", file=sys.stderr)
        if args.once:
            break
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    main()
