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
    """Prefer explorer/node address API; fall back to local chain file."""
    api_base = api_base.rstrip("/")
    for path in (
        f"/api/public/address/{address}",
        f"/api/address/{address}",
    ):
        try:
            j = _http_json(api_base + path)
            if "nonce" in j:
                return int(j["nonce"])
            if j.get("account") and "nonce" in j["account"]:
                return int(j["account"]["nonce"])
            # some APIs return next_nonce
            if "next_nonce" in j:
                return int(j["next_nonce"])
        except Exception:
            continue
    if data_dir:
        try:
            from ..blockchain import Blockchain

            chain = Blockchain(Path(data_dir))
            return int(chain.next_nonce(address))
        except Exception:
            pass
    return 0


def broadcast_tx(api_base: str, tx: Dict[str, Any], data_dir: Optional[Path] = None) -> str:
    api_base = api_base.rstrip("/")
    for path in ("/api/public/broadcast", "/api/broadcast"):
        try:
            j = _http_json(api_base + path, {"tx": tx})
            return str(j.get("txid") or tx.get("txid") or "")
        except Exception:
            continue
    if data_dir:
        from ..blockchain import Blockchain

        chain = Blockchain(Path(data_dir))
        ok, msg = chain.add_to_mempool(tx)
        if not ok:
            raise RuntimeError(f"mempool reject: {msg}")
        return str(tx.get("txid") or msg)
    raise RuntimeError("broadcast failed — no API and no data_dir")


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
