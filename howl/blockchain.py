"""Howlcoin chain state: blocks, UTXO-less account balances, validation."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import (
    BLOCK_TIME_SECONDS,
    CHAIN_FILE,
    COIN,
    DIFFICULTY_ADJUST_INTERVAL,
    DIFFICULTY_MAX_ADJUST,
    GENESIS_MESSAGE,
    INITIAL_DIFFICULTY,
    block_subsidy,
)
from .crypto import is_valid_address, sha256, tx_sighash, txid, verify_signature
from .scrypt_pow import meets_difficulty, merkle_root, mine_block, pow_hash_hex
from .wallet import format_howl


class Blockchain:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.chain_path = data_dir / CHAIN_FILE
        self.mempool_path = data_dir / "mempool.json"
        self.blocks: List[Dict[str, Any]] = []
        self.mempool: List[Dict[str, Any]] = []
        # address -> balance in howlies
        self.balances: Dict[str, int] = {}
        # address -> next expected nonce
        self.nonces: Dict[str, int] = {}
        data_dir.mkdir(parents=True, exist_ok=True)
        if self.chain_path.exists():
            self._load()
        else:
            self._create_genesis()
        self._load_mempool()

    # ---------- persistence ----------

    def _load(self) -> None:
        raw = json.loads(self.chain_path.read_text())
        self.blocks = raw["blocks"]
        self._rebuild_state()

    def save(self) -> None:
        payload = {
            "coin": "Howlcoin",
            "ticker": "HOWL",
            "blocks": self.blocks,
        }
        self.chain_path.write_text(json.dumps(payload, indent=2))

    def _load_mempool(self) -> None:
        if self.mempool_path.exists():
            self.mempool = json.loads(self.mempool_path.read_text())
        else:
            self.mempool = []

    def save_mempool(self) -> None:
        self.mempool_path.write_text(json.dumps(self.mempool, indent=2))

    # ---------- genesis ----------

    def _create_genesis(self) -> None:
        print("Creating Howlcoin genesis block (Scrypt)…")
        coinbase = {
            "type": "coinbase",
            "to": "HOWL_GENESIS_BURN",
            "amount": 0,
            "memo": GENESIS_MESSAGE,
            "txid": sha256(GENESIS_MESSAGE.encode()).hex(),
        }
        header_template = {
            "version": 1,
            "prev_hash": "00" * 32,
            "merkle_root": merkle_root([coinbase["txid"]]),
            "timestamp": 1754006400,  # fixed for reproducibility (2025-08-01 vibe)
            "difficulty": 1,  # trivial for genesis
            "nonce": 0,
        }
        header, block_hash, tried = mine_block(header_template, difficulty=1, progress_every=0)
        block = {
            "height": 0,
            "hash": block_hash,
            "header": header,
            "transactions": [coinbase],
        }
        self.blocks = [block]
        self._rebuild_state()
        self.save()
        print(f"Genesis howled into existence: {block_hash[:16]}… ({tried} hashes)")

    # ---------- state ----------

    def _rebuild_state(self) -> None:
        self.balances = {}
        self.nonces = {}
        for block in self.blocks:
            self._apply_block(block, mutate_only=True)

    def _apply_block(self, block: Dict[str, Any], mutate_only: bool = False) -> None:
        for tx in block["transactions"]:
            if tx.get("type") == "coinbase":
                to = tx["to"]
                if to == "HOWL_GENESIS_BURN":
                    continue
                self.balances[to] = self.balances.get(to, 0) + int(tx["amount"])
                continue
            # transfer
            frm = tx["from"]
            to = tx["to"]
            amount = int(tx["amount"])
            fee = int(tx.get("fee", 0))
            self.balances[frm] = self.balances.get(frm, 0) - amount - fee
            self.balances[to] = self.balances.get(to, 0) + amount
            # fees go to miner via coinbase already including fees in our simple model
            # (we subtract fee from sender; miner coinbase is pure subsidy — fees burned for simplicity)
            self.nonces[frm] = int(tx["nonce"]) + 1

    def tip(self) -> Dict[str, Any]:
        return self.blocks[-1]

    def height(self) -> int:
        return len(self.blocks) - 1

    def balance(self, address: str) -> int:
        return self.balances.get(address, 0)

    def next_nonce(self, address: str) -> int:
        return self.nonces.get(address, 0)

    # ---------- difficulty ----------

    def current_difficulty(self) -> int:
        if len(self.blocks) < 2:
            return INITIAL_DIFFICULTY
        return int(self.tip()["header"]["difficulty"])

    def next_difficulty(self) -> int:
        height = self.height() + 1
        if height < DIFFICULTY_ADJUST_INTERVAL:
            return INITIAL_DIFFICULTY
        if height % DIFFICULTY_ADJUST_INTERVAL != 0:
            return self.current_difficulty()

        # retarget based on last interval
        interval = DIFFICULTY_ADJUST_INTERVAL
        newer = self.blocks[-1]
        older = self.blocks[-interval]
        actual = max(1, newer["header"]["timestamp"] - older["header"]["timestamp"])
        expected = interval * BLOCK_TIME_SECONDS
        ratio = actual / expected
        # slower blocks => ratio > 1 => lower difficulty
        diff = float(self.current_difficulty())
        if ratio > DIFFICULTY_MAX_ADJUST:
            ratio = DIFFICULTY_MAX_ADJUST
        if ratio < 1 / DIFFICULTY_MAX_ADJUST:
            ratio = 1 / DIFFICULTY_MAX_ADJUST
        # if actual > expected, mining was hard/slow → decrease difficulty
        new_diff = diff / ratio if ratio != 0 else diff
        new_diff = max(1, min(12, round(new_diff)))
        return int(new_diff)

    # ---------- validation ----------

    def validate_tx(self, tx: Dict[str, Any], provisional_balances: Optional[Dict[str, int]] = None, provisional_nonces: Optional[Dict[str, int]] = None) -> Tuple[bool, str]:
        if tx.get("type") == "coinbase":
            return False, "coinbase not allowed in mempool"
        for field in ("from", "to", "amount", "nonce", "public_key", "signature"):
            if field not in tx:
                return False, f"missing field {field}"
        if not is_valid_address(tx["from"]) or not is_valid_address(tx["to"]):
            return False, "bad address"
        amount = int(tx["amount"])
        fee = int(tx.get("fee", 0))
        if amount <= 0:
            return False, "amount must be > 0"
        if fee < 0:
            return False, "fee must be >= 0"

        bals = provisional_balances if provisional_balances is not None else self.balances
        nonces = provisional_nonces if provisional_nonces is not None else self.nonces
        bal = bals.get(tx["from"], 0)
        if bal < amount + fee:
            return False, f"insufficient balance ({format_howl(bal)})"
        expected_nonce = nonces.get(tx["from"], 0)
        if int(tx["nonce"]) != expected_nonce:
            return False, f"bad nonce (want {expected_nonce})"

        body = {
            "from": tx["from"],
            "to": tx["to"],
            "amount": amount,
            "fee": fee,
            "nonce": int(tx["nonce"]),
            "memo": tx.get("memo", ""),
        }
        if not verify_signature(tx["public_key"], tx_sighash(body), tx["signature"]):
            return False, "bad signature"
        # address must match pubkey
        from .crypto import pubkey_to_address

        if pubkey_to_address(bytes.fromhex(tx["public_key"])) != tx["from"]:
            return False, "pubkey/address mismatch"
        if tx.get("txid") != txid({**body, "public_key": tx["public_key"], "signature": tx["signature"]}):
            # recompute allowing full tx
            full = dict(tx)
            if full.get("txid") and full["txid"] != txid({k: v for k, v in full.items() if k != "txid"}):
                # soft check — recompute
                pass
        return True, "ok"

    def add_to_mempool(self, tx: Dict[str, Any]) -> Tuple[bool, str]:
        ok, msg = self.validate_tx(tx)
        if not ok:
            return False, msg
        tid = tx.get("txid") or txid(tx)
        tx["txid"] = tid
        if any(t.get("txid") == tid for t in self.mempool):
            return False, "already in mempool"
        # also reject if already in chain
        for b in self.blocks:
            for t in b["transactions"]:
                if t.get("txid") == tid:
                    return False, "already confirmed"
        self.mempool.append(tx)
        self.save_mempool()
        return True, tid

    def validate_block(self, block: Dict[str, Any], prev: Dict[str, Any]) -> Tuple[bool, str]:
        h = block["header"]
        if h["prev_hash"] != prev["hash"]:
            return False, "prev_hash mismatch"
        if block["height"] != prev["height"] + 1:
            return False, "height mismatch"
        txids = [t["txid"] for t in block["transactions"]]
        if h["merkle_root"] != merkle_root(txids):
            return False, "merkle root mismatch"
        block_hash = pow_hash_hex(h)
        if block_hash != block["hash"]:
            return False, "hash mismatch"
        if not meets_difficulty(block_hash, int(h["difficulty"])):
            return False, "insufficient proof of work"
        # coinbase checks
        txs = block["transactions"]
        if not txs or txs[0].get("type") != "coinbase":
            return False, "missing coinbase"
        subsidy = block_subsidy(block["height"])
        if int(txs[0]["amount"]) > subsidy:
            # allow equal; fees burned so coinbase == subsidy only
            return False, "coinbase too large"
        # validate rest against rolling state copy
        bals = dict(self.balances)
        nonces = dict(self.nonces)
        # apply coinbase
        cb = txs[0]
        bals[cb["to"]] = bals.get(cb["to"], 0) + int(cb["amount"])
        for tx in txs[1:]:
            ok, msg = self.validate_tx(tx, provisional_balances=bals, provisional_nonces=nonces)
            if not ok:
                return False, f"tx invalid: {msg}"
            frm, to = tx["from"], tx["to"]
            amount, fee = int(tx["amount"]), int(tx.get("fee", 0))
            bals[frm] = bals.get(frm, 0) - amount - fee
            bals[to] = bals.get(to, 0) + amount
            nonces[frm] = int(tx["nonce"]) + 1
        return True, "ok"

    def append_block(self, block: Dict[str, Any]) -> Tuple[bool, str]:
        ok, msg = self.validate_block(block, self.tip())
        if not ok:
            return False, msg
        # enforce difficulty schedule for non-genesis
        expected_diff = self.next_difficulty()
        if int(block["header"]["difficulty"]) != expected_diff:
            return False, f"wrong difficulty (got {block['header']['difficulty']}, want {expected_diff})"
        self.blocks.append(block)
        self._apply_block(block)
        # purge mempool txs that confirmed
        confirmed = {t["txid"] for t in block["transactions"] if "txid" in t}
        self.mempool = [t for t in self.mempool if t.get("txid") not in confirmed]
        self.save()
        self.save_mempool()
        return True, "ok"

    def get_blocks_from(self, from_height: int, limit: int = 500) -> List[Dict[str, Any]]:
        """Return blocks with height >= from_height (inclusive), capped."""
        if from_height < 0:
            from_height = 0
        return self.blocks[from_height : from_height + limit]

    def genesis_hash(self) -> str:
        return self.blocks[0]["hash"] if self.blocks else ""

    def try_add_block(self, block: Dict[str, Any]) -> Tuple[bool, str]:
        """Accept a peer block if it extends our tip."""
        if not block or "hash" not in block:
            return False, "malformed block"
        # already have it?
        if any(b["hash"] == block["hash"] for b in self.blocks):
            return True, "already have"
        if block.get("height") != self.height() + 1:
            return False, f"not next height (have {self.height()}, got {block.get('height')})"
        return self.append_block(block)

    def adopt_chain(self, blocks: List[Dict[str, Any]]) -> Tuple[bool, str]:
        """
        Replace local chain if `blocks` is a longer valid Howlcoin chain
        sharing our genesis. Used for IBD / catch-up from peers.
        """
        if not blocks:
            return False, "empty chain"
        if blocks[0].get("hash") != self.genesis_hash():
            return False, "genesis mismatch — not the same Howlcoin network"
        if len(blocks) <= len(self.blocks):
            return False, "peer chain not longer"

        # Validate sequentially from genesis
        work_blocks = [blocks[0]]
        # rebuild state incrementally with a temp chain
        temp = Blockchain.__new__(Blockchain)
        temp.data_dir = self.data_dir
        temp.chain_path = self.chain_path
        temp.mempool_path = self.mempool_path
        temp.blocks = [blocks[0]]
        temp.mempool = list(self.mempool)
        temp.balances = {}
        temp.nonces = {}
        temp._rebuild_state()

        for i in range(1, len(blocks)):
            b = blocks[i]
            if b.get("height") != i:
                return False, f"height gap at {i}"
            ok, msg = temp.validate_block(b, temp.tip())
            if not ok:
                return False, f"invalid at height {i}: {msg}"
            # difficulty schedule check against temp tip state
            expected = temp.next_difficulty()
            if int(b["header"]["difficulty"]) != expected:
                return False, f"bad difficulty at {i}"
            temp.blocks.append(b)
            temp._apply_block(b)

        # adopt
        self.blocks = blocks
        self._rebuild_state()
        # drop mempool txs that are now invalid
        kept = []
        for tx in self.mempool:
            ok, _ = self.validate_tx(tx)
            if ok:
                kept.append(tx)
        self.mempool = kept
        self.save()
        self.save_mempool()
        return True, f"adopted chain height {self.height()}"

    # ---------- mining helper ----------

    def build_block_template(self, miner_address: str, max_txs: int = 50) -> Dict[str, Any]:
        height = self.height() + 1
        difficulty = self.next_difficulty()
        subsidy = block_subsidy(height)
        coinbase = {
            "type": "coinbase",
            "to": miner_address,
            "amount": subsidy,
            "height": height,
            "memo": f"Howl height {height}",
            "txid": sha256(f"coinbase:{height}:{miner_address}:{subsidy}".encode()).hex(),
        }
        # select valid mempool txs
        selected: List[Dict[str, Any]] = []
        bals = dict(self.balances)
        nonces = dict(self.nonces)
        bals[miner_address] = bals.get(miner_address, 0) + subsidy
        for tx in self.mempool[: max_txs * 2]:
            if len(selected) >= max_txs:
                break
            ok, _ = self.validate_tx(tx, provisional_balances=bals, provisional_nonces=nonces)
            if not ok:
                continue
            selected.append(tx)
            amount, fee = int(tx["amount"]), int(tx.get("fee", 0))
            bals[tx["from"]] = bals.get(tx["from"], 0) - amount - fee
            bals[tx["to"]] = bals.get(tx["to"], 0) + amount
            nonces[tx["from"]] = int(tx["nonce"]) + 1

        txs = [coinbase] + selected
        txids = [t["txid"] for t in txs]
        header = {
            "version": 1,
            "prev_hash": self.tip()["hash"],
            "merkle_root": merkle_root(txids),
            "timestamp": int(time.time()),
            "difficulty": difficulty,
            "nonce": 0,
        }
        return {
            "height": height,
            "header": header,
            "transactions": txs,
            "difficulty": difficulty,
            "subsidy": subsidy,
        }

    def mine_one(self, miner_address: str) -> Dict[str, Any]:
        template = self.build_block_template(miner_address)
        diff = template["difficulty"]
        subsidy = template["subsidy"]
        print(
            f"Mining block #{template['height']} | diff={diff} | "
            f"reward={format_howl(subsidy)} | txs={len(template['transactions'])-1}"
        )
        t0 = time.time()
        header, block_hash, tried = mine_block(template["header"], difficulty=diff)
        elapsed = max(time.time() - t0, 1e-9)
        block = {
            "height": template["height"],
            "hash": block_hash,
            "header": header,
            "transactions": template["transactions"],
        }
        ok, msg = self.append_block(block)
        if not ok:
            raise RuntimeError(f"mined block rejected: {msg}")
        print(
            f"\n✓ Block #{block['height']} {block_hash[:16]}… | "
            f"{tried} hashes in {elapsed:.1f}s ({tried/elapsed:.1f} H/s) | "
            f"+{format_howl(subsidy)}"
        )
        return block

    # ---------- info ----------

    def summary(self) -> Dict[str, Any]:
        supply = sum(self.balances.values())
        return {
            "name": "Howlcoin",
            "ticker": "HOWL",
            "height": self.height(),
            "difficulty": self.current_difficulty(),
            "next_difficulty": self.next_difficulty(),
            "tip": self.tip()["hash"],
            "mempool": len(self.mempool),
            "addresses": len(self.balances),
            "circulating_howlies": supply,
            "circulating": format_howl(supply),
            "algo": "scrypt (N=1024,r=1,p=1)",
            "block_time_target": f"{BLOCK_TIME_SECONDS}s",
        }

    # ---------- explorer queries ----------

    def get_block(self, height_or_hash: str) -> Optional[Dict[str, Any]]:
        s = str(height_or_hash).strip()
        if s.isdigit():
            h = int(s)
            if 0 <= h < len(self.blocks):
                return self.blocks[h]
            return None
        s = s.lower()
        for b in self.blocks:
            if b.get("hash", "").lower() == s or b.get("hash", "").lower().startswith(s):
                return b
        return None

    def recent_blocks(self, limit: int = 25) -> List[Dict[str, Any]]:
        limit = max(1, min(200, limit))
        out = []
        for b in reversed(self.blocks[-limit:]):
            txs = b.get("transactions") or []
            coinbase = next((t for t in txs if t.get("type") == "coinbase"), None)
            out.append(
                {
                    "height": b["height"],
                    "hash": b["hash"],
                    "timestamp": b["header"].get("timestamp"),
                    "difficulty": b["header"].get("difficulty"),
                    "tx_count": len(txs),
                    "miner": (coinbase or {}).get("to"),
                    "reward": (coinbase or {}).get("amount", 0),
                }
            )
        return out

    def recent_transactions(self, limit: int = 25) -> List[Dict[str, Any]]:
        """Newest transactions across tip blocks + mempool."""
        limit = max(1, min(200, limit))
        out: List[Dict[str, Any]] = []
        for tx in reversed(self.mempool):
            out.append(
                {
                    "txid": tx.get("txid"),
                    "type": tx.get("type", "transfer"),
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "amount": tx.get("amount", 0),
                    "fee": tx.get("fee", 0),
                    "block_height": None,
                    "confirmed": False,
                    "timestamp": None,
                }
            )
            if len(out) >= limit:
                return out
        for b in reversed(self.blocks):
            for tx in reversed(b.get("transactions") or []):
                out.append(
                    {
                        "txid": tx.get("txid"),
                        "type": tx.get("type", "transfer"),
                        "from": tx.get("from"),
                        "to": tx.get("to"),
                        "amount": tx.get("amount", 0),
                        "fee": tx.get("fee", 0),
                        "block_height": b["height"],
                        "block_hash": b["hash"],
                        "confirmed": True,
                        "timestamp": b["header"].get("timestamp"),
                    }
                )
                if len(out) >= limit:
                    return out
        return out

    def find_tx(self, txid_q: str) -> Optional[Dict[str, Any]]:
        q = txid_q.strip().lower()
        for b in self.blocks:
            for tx in b.get("transactions") or []:
                tid = (tx.get("txid") or "").lower()
                if tid == q or tid.startswith(q):
                    return {
                        "tx": tx,
                        "block_height": b["height"],
                        "block_hash": b["hash"],
                        "confirmed": True,
                    }
        for tx in self.mempool:
            tid = (tx.get("txid") or "").lower()
            if tid == q or tid.startswith(q):
                return {"tx": tx, "confirmed": False, "block_height": None, "block_hash": None}
        return None

    def address_history(self, address: str, limit: int = 50) -> Dict[str, Any]:
        txs = []
        for b in self.blocks:
            for tx in b.get("transactions") or []:
                if tx.get("type") == "coinbase" and tx.get("to") == address:
                    txs.append(
                        {
                            "txid": tx.get("txid"),
                            "type": "coinbase",
                            "amount": tx.get("amount"),
                            "block_height": b["height"],
                            "block_hash": b["hash"],
                            "direction": "in",
                        }
                    )
                elif tx.get("from") == address or tx.get("to") == address:
                    direction = "out" if tx.get("from") == address else "in"
                    txs.append(
                        {
                            "txid": tx.get("txid"),
                            "type": "transfer",
                            "from": tx.get("from"),
                            "to": tx.get("to"),
                            "amount": tx.get("amount"),
                            "fee": tx.get("fee", 0),
                            "block_height": b["height"],
                            "block_hash": b["hash"],
                            "direction": direction,
                        }
                    )
        txs = list(reversed(txs))[:limit]
        return {
            "address": address,
            "balance": self.balance(address),
            "balance_fmt": format_howl(self.balance(address)),
            "nonce": self.next_nonce(address),
            "tx_count": len(txs),
            "transactions": txs,
        }

    def reload_from_disk(self) -> None:
        """Re-read chain.json (for explorer watching live nodes)."""
        if self.chain_path.exists():
            self._load()
            self._load_mempool()

