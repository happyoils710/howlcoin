"""Settle agent consensus results on Howl L1 (oracle transactions)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from ..config import COIN, MIN_TX_FEE_HOWLIES, DEFAULT_TX_FEE_HOWLIES
from ..crypto import tx_sighash, txid
from ..wallet import Wallet


def _http_json(url: str, payload: Optional[dict] = None, timeout: int = 25) -> dict:
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; settlement)"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data else "GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def build_oracle_tx(
    wallet: Wallet,
    *,
    oracle_key: str,
    oracle_value: str,
    nonce: int,
    fee: Optional[int] = None,
    source_chain: str = "howlcoin",
) -> Dict[str, Any]:
    """Build a signed oracle observation tx (amount 0, fee paid to miners)."""
    key = wallet.primary
    fee = int(fee if fee is not None else max(MIN_TX_FEE_HOWLIES, DEFAULT_TX_FEE_HOWLIES))
    body: Dict[str, Any] = {
        "type": "oracle",
        "from": key.address,
        "to": key.address,
        "amount": 0,
        "fee": fee,
        "nonce": int(nonce),
        "memo": "howl.agent",
        "public_key": key.public_key_hex,
        "oracle_key": oracle_key[:120],
        "oracle_value": str(oracle_value)[:2000],
        "source_chain": source_chain,
        "observed_at": int(time.time()),
    }
    sig = key.sign(tx_sighash(body))
    body["signature"] = sig
    body["txid"] = txid(body)
    return body


def next_nonce(api_base: str, address: str, data_dir: Optional[Path] = None) -> int:
    """
    Next valid transfer nonce for `address`.

    Chain rule: nonce must equal accounts' expected next (0, then 1, …).
    Explorer `address.nonce` is that value. Also bump past any mempool txs
    from the same sender so we never re-use a pending nonce.
    """
    api_base = api_base.rstrip("/")
    n = 0
    got = False
    for path in (
        f"/api/public/address/{address}",
        f"/api/address/{address}",
    ):
        try:
            j = _http_json(api_base + path)
            if "nonce" in j:
                n = int(j["nonce"])
                got = True
                break
            if j.get("account") and "nonce" in j["account"]:
                n = int(j["account"]["nonce"])
                got = True
                break
            if "next_nonce" in j:
                n = int(j["next_nonce"])
                got = True
                break
        except Exception:
            continue
    if not got and data_dir:
        try:
            from ..blockchain import Blockchain

            chain = Blockchain(Path(data_dir))
            n = int(chain.next_nonce(address))
            got = True
        except Exception:
            pass

    # Pending mempool nonces from this sender
    for path in ("/api/public/mempool", "/api/mempool"):
        try:
            mp = _http_json(api_base + path)
            items = mp.get("transactions") or mp.get("mempool") or mp.get("txs") or []
            if isinstance(items, dict):
                items = list(items.values())
            for t in items:
                if not isinstance(t, dict):
                    continue
                if (t.get("from") or "") != address:
                    continue
                try:
                    n = max(n, int(t.get("nonce") or 0) + 1)
                except (TypeError, ValueError):
                    pass
            break
        except Exception:
            continue
    return int(n)


def _http_json_raise(url: str, payload: Optional[dict] = None, timeout: int = 25) -> dict:
    """Like _http_json but includes server JSON error body in exceptions."""
    data = None if payload is None else json.dumps(payload).encode()
    headers = {"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; settlement)"}
    if data:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        url,
        data=data,
        headers=headers,
        method="POST" if data else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()
            j = json.loads(body)
            msg = j.get("error") or j.get("message") or body
        except Exception:
            msg = body or str(e)
        raise RuntimeError(str(msg)) from e


def broadcast_tx(api_base: str, tx: Dict[str, Any], data_dir: Optional[Path] = None) -> str:
    api_base = api_base.rstrip("/")
    last_err: Optional[str] = None
    for path in ("/api/public/broadcast", "/api/broadcast"):
        try:
            j = _http_json_raise(api_base + path, {"tx": tx})
            if j.get("error"):
                raise RuntimeError(str(j["error"]))
            return str(j.get("txid") or tx.get("txid") or "")
        except Exception as e:
            last_err = str(e)
            # Don't silently try the next path on clear validation errors
            low = last_err.lower()
            if "nonce" in low or "insufficient" in low or "reject" in low or "invalid" in low:
                raise RuntimeError(last_err) from e
            continue
    if data_dir:
        from ..blockchain import Blockchain

        chain = Blockchain(Path(data_dir))
        ok, msg = chain.add_to_mempool(tx)
        if not ok:
            raise RuntimeError(f"mempool reject: {msg}")
        return str(tx.get("txid") or msg)
    raise RuntimeError(last_err or "broadcast failed — no API and no data_dir")


def parse_wanted_nonce(err: str) -> Optional[int]:
    """Parse 'bad nonce (want 9)' → 9."""
    import re

    m = re.search(r"want\s+(\d+)", str(err), re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"nonce\s+(\d+)\s+already", str(err), re.I)
    if m:
        return int(m.group(1)) + 1
    return None

def settle_consensus(
    *,
    wallet_path: Path,
    api_base: str,
    proposal: Dict[str, Any],
    result: Dict[str, Any],
    data_dir: Optional[Path] = None,
) -> Tuple[str, str]:
    """
    Post consensus result on-chain under oracle key:
      howl.agent.consensus.<proposal_id>
    Returns (oracle_key, txid).
    """
    wallet = Wallet(wallet_path, create_if_missing=False)
    pid = str(proposal.get("proposal_id") or "unknown")[:40]
    key = f"howl.agent.consensus.{pid}"
    value = json.dumps(
        {
            "proposal": proposal,
            "result": result,
            "settled_at": int(time.time()),
            "agent_system": "howl-agents/v1",
        },
        separators=(",", ":"),
    )[:2000]
    nonce = next_nonce(api_base, wallet.primary.address, data_dir)
    tx = build_oracle_tx(wallet, oracle_key=key, oracle_value=value, nonce=nonce)
    fee_howl = max(MIN_TX_FEE_HOWLIES, DEFAULT_TX_FEE_HOWLIES) / COIN
    txid_out = broadcast_tx(api_base, tx, data_dir)
    return key, txid_out


def settle_finding(
    *,
    wallet_path: Path,
    api_base: str,
    finding: Dict[str, Any],
    data_dir: Optional[Path] = None,
) -> Tuple[str, str]:
    wallet = Wallet(wallet_path, create_if_missing=False)
    fid = str(finding.get("finding_id") or "f")[:40]
    key = f"howl.agent.finding.{fid}"
    value = json.dumps(finding, separators=(",", ":"))[:2000]
    nonce = next_nonce(api_base, wallet.primary.address, data_dir)
    tx = build_oracle_tx(wallet, oracle_key=key, oracle_value=value, nonce=nonce)
    txid_out = broadcast_tx(api_base, tx, data_dir)
    return key, txid_out
