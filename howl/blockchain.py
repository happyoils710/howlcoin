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
    MAX_DIFFICULTY_FLOAT,
    MAX_FUTURE_DRIFT_SECONDS,
    MIN_DIFFICULTY_FLOAT,
    MIN_TX_FEE_HOWLIES,
    SMOOTH_DIFF_ACTIVATION_HEIGHT,
    STALL_MAX_ADJUST,
    STALL_SECONDS,
    VERSION,
    block_subsidy,
)
from .crypto import is_valid_address, sha256, tx_sighash, txid, verify_signature
from .scrypt_pow import (
    difficulty_float_from_raw,
    encode_difficulty_milli,
    expected_hashes,
    format_count,
    format_difficulty,
    format_duration,
    is_smooth_difficulty_raw,
    meets_difficulty,
    merkle_root,
    mine_block,
    pow_hash_hex,
)
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
        self.balances: Dict[str, int] = {}
        self.nonces: Dict[str, int] = {}
        # nft_id -> metadata + owner
        self.nfts: Dict[str, Dict[str, Any]] = {}
        # oracle_key -> latest observation
        self.oracle: Dict[str, Dict[str, Any]] = {}
        for block in self.blocks:
            self._apply_block(block, mutate_only=True)

    def _apply_block(self, block: Dict[str, Any], mutate_only: bool = False) -> None:
        height = block.get("height", 0)
        for tx in block["transactions"]:
            if tx.get("type") == "coinbase":
                to = tx["to"]
                if to == "HOWL_GENESIS_BURN":
                    continue
                self.balances[to] = self.balances.get(to, 0) + int(tx["amount"])
                continue

            frm = tx["from"]
            to = tx["to"]
            amount = int(tx.get("amount", 0))
            fee = int(tx.get("fee", 0))
            self.balances[frm] = self.balances.get(frm, 0) - amount - fee
            if amount:
                self.balances[to] = self.balances.get(to, 0) + amount
            self.nonces[frm] = int(tx["nonce"]) + 1

            t = tx.get("type") or "transfer"
            if t == "nft_mint":
                nid = tx.get("nft_id") or ""
                if nid:
                    self.nfts[nid] = {
                        "nft_id": nid,
                        "owner": to,
                        "creator": frm,
                        "name": tx.get("name") or "Untitled",
                        "uri": tx.get("uri") or "",
                        "mint_txid": tx.get("txid"),
                        "mint_height": height,
                    }
            elif t == "nft_transfer":
                nid = tx.get("nft_id") or ""
                if nid and nid in self.nfts:
                    self.nfts[nid]["owner"] = to
                    self.nfts[nid]["last_txid"] = tx.get("txid")
                    self.nfts[nid]["last_height"] = height
            elif t == "oracle":
                key = str(tx.get("oracle_key") or "")
                if key:
                    self.oracle[key] = {
                        "key": key,
                        "value": tx.get("oracle_value"),
                        "source_chain": tx.get("source_chain") or "unknown",
                        "observed_at": tx.get("observed_at"),
                        "reporter": frm,
                        "txid": tx.get("txid"),
                        "height": height,
                    }

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
        """Raw header difficulty field of the tip (nibble or milli)."""
        if len(self.blocks) < 2:
            return INITIAL_DIFFICULTY
        return int(self.tip()["header"]["difficulty"])

    def current_difficulty_float(self) -> float:
        """Nibble-equivalent float for the tip (works for legacy + smooth)."""
        if len(self.blocks) < 2:
            return float(INITIAL_DIFFICULTY)
        return difficulty_float_from_raw(self.current_difficulty())

    def _retarget_float(self, current_d: float) -> float:
        """Adjust nibble-equivalent difficulty from the last retarget window."""
        interval = DIFFICULTY_ADJUST_INTERVAL
        if len(self.blocks) < interval:
            return current_d
        newer = self.blocks[-1]
        older = self.blocks[-interval]
        actual = max(1, int(newer["header"]["timestamp"]) - int(older["header"]["timestamp"]))
        expected = interval * BLOCK_TIME_SECONDS
        ratio = actual / expected
        # slower blocks => ratio > 1 => lower difficulty
        if ratio > DIFFICULTY_MAX_ADJUST:
            ratio = DIFFICULTY_MAX_ADJUST
        if ratio < 1 / DIFFICULTY_MAX_ADJUST:
            ratio = 1 / DIFFICULTY_MAX_ADJUST
        new_d = current_d / ratio if ratio != 0 else current_d
        return new_d

    def _apply_stall_float(self, d: float, gap_seconds: int) -> float:
        """
        Deterministic stall relief from (block_ts - tip_ts).
        If the gap exceeds STALL_SECONDS, allow extra reduction beyond 4× clamp.
        """
        if gap_seconds < STALL_SECONDS:
            return d
        # How many target windows late (e.g. 20 min windows)
        window = DIFFICULTY_ADJUST_INTERVAL * BLOCK_TIME_SECONDS
        overdue_windows = gap_seconds / float(window)
        # factor 1 at exactly STALL, grows with lateness
        factor = min(STALL_MAX_ADJUST, max(1.0, overdue_windows))
        # Also scale by how far past STALL_SECONDS we are
        stall_extra = gap_seconds / float(STALL_SECONDS)
        factor = min(STALL_MAX_ADJUST, max(factor, stall_extra))
        return d / factor

    def next_difficulty(self, at_timestamp: Optional[int] = None) -> int:
        """
        Raw difficulty that the next block must carry.

        Legacy (height < SMOOTH_DIFF_ACTIVATION_HEIGHT): integer leading-zero
        nibbles, retarget every 20 blocks, round to int 1..12.

        Smooth (height >= activation): milli-nibble encoding (d*1000), continuous
        target, same retarget ratio, plus timestamp-based stall relief.
        """
        height = self.height() + 1
        if height < SMOOTH_DIFF_ACTIVATION_HEIGHT:
            return self._next_difficulty_legacy_nibble()

        at_ts = int(at_timestamp if at_timestamp is not None else time.time())
        d = self.current_difficulty_float()

        # First smooth block: tip may still be legacy nibble — float convert is fine
        if height >= DIFFICULTY_ADJUST_INTERVAL and height % DIFFICULTY_ADJUST_INTERVAL == 0:
            d = self._retarget_float(d)

        tip_ts = int(self.tip()["header"]["timestamp"])
        gap = max(0, at_ts - tip_ts)
        d = self._apply_stall_float(d, gap)

        d = max(MIN_DIFFICULTY_FLOAT, min(MAX_DIFFICULTY_FLOAT, d))
        return encode_difficulty_milli(d)

    def _next_difficulty_legacy_nibble(self) -> int:
        """Pre-v0.6 schedule — must remain bit-identical for historical blocks."""
        height = self.height() + 1
        if height < DIFFICULTY_ADJUST_INTERVAL:
            return INITIAL_DIFFICULTY
        if height % DIFFICULTY_ADJUST_INTERVAL != 0:
            return self.current_difficulty()

        interval = DIFFICULTY_ADJUST_INTERVAL
        newer = self.blocks[-1]
        older = self.blocks[-interval]
        actual = max(1, newer["header"]["timestamp"] - older["header"]["timestamp"])
        expected = interval * BLOCK_TIME_SECONDS
        ratio = actual / expected
        diff = float(self.current_difficulty())
        if ratio > DIFFICULTY_MAX_ADJUST:
            ratio = DIFFICULTY_MAX_ADJUST
        if ratio < 1 / DIFFICULTY_MAX_ADJUST:
            ratio = 1 / DIFFICULTY_MAX_ADJUST
        new_diff = diff / ratio if ratio != 0 else diff
        new_diff = max(1, min(12, round(new_diff)))
        return int(new_diff)

    # ---------- validation ----------

    def validate_tx(
        self,
        tx: Dict[str, Any],
        provisional_balances: Optional[Dict[str, int]] = None,
        provisional_nonces: Optional[Dict[str, int]] = None,
        provisional_nfts: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> Tuple[bool, str]:
        if tx.get("type") == "coinbase":
            return False, "coinbase not allowed in mempool"
        for field in ("from", "to", "amount", "nonce", "public_key", "signature"):
            if field not in tx:
                return False, f"missing field {field}"
        if not is_valid_address(tx["from"]) or not is_valid_address(tx["to"]):
            return False, "bad address"

        t = tx.get("type") or "transfer"
        amount = int(tx.get("amount", 0))
        fee = int(tx.get("fee", 0))
        if fee < 0:
            return False, "fee must be >= 0"
        if fee < MIN_TX_FEE_HOWLIES:
            return False, (
                f"fee too low (min {format_howl(MIN_TX_FEE_HOWLIES)}; "
                "fees pay the miner who confirms your tx)"
            )

        if t == "transfer":
            if amount <= 0:
                return False, "amount must be > 0"
        elif t in ("nft_mint", "nft_transfer", "oracle"):
            if amount < 0:
                return False, "amount must be >= 0"
        else:
            return False, f"unknown tx type {t}"

        bals = provisional_balances if provisional_balances is not None else self.balances
        nonces = provisional_nonces if provisional_nonces is not None else self.nonces
        nfts = provisional_nfts if provisional_nfts is not None else self.nfts

        bal = bals.get(tx["from"], 0)
        if bal < amount + fee:
            return False, f"insufficient balance ({format_howl(bal)})"
        expected_nonce = nonces.get(tx["from"], 0)
        if int(tx["nonce"]) != expected_nonce:
            return False, f"bad nonce (want {expected_nonce})"

        if t == "nft_mint":
            name = (tx.get("name") or "").strip()
            if not name or len(name) > 80:
                return False, "nft name required (1–80 chars)"
            uri = (tx.get("uri") or "").strip()
            if len(uri) > 500:
                return False, "uri too long"
            nid = (tx.get("nft_id") or "").strip()
            if not nid or len(nid) > 64:
                return False, "nft_id required"
            if nid in nfts:
                return False, "nft_id already exists"
            # mint to self or specified to
            if tx["to"] != tx["from"]:
                # allow mint-to-other as gift mint
                pass
        elif t == "nft_transfer":
            nid = (tx.get("nft_id") or "").strip()
            if not nid or nid not in nfts:
                return False, "unknown nft_id"
            if nfts[nid].get("owner") != tx["from"]:
                return False, "not nft owner"
            if tx["to"] == tx["from"]:
                return False, "cannot transfer to self"
        elif t == "oracle":
            key = str(tx.get("oracle_key") or "").strip()
            if not key or len(key) > 120:
                return False, "oracle_key required (1–120 chars)"
            val = tx.get("oracle_value")
            if val is None or str(val) == "":
                return False, "oracle_value required"
            if len(str(val)) > 2000:
                return False, "oracle_value too long"
            # oracle posts are self-targeted (no coin transfer)
            if amount != 0:
                return False, "oracle amount must be 0"

        # Signature over canonical body (includes extended fields when present)
        if not verify_signature(tx["public_key"], tx_sighash(tx), tx["signature"]):
            return False, "bad signature"
        from .crypto import pubkey_to_address

        if pubkey_to_address(bytes.fromhex(tx["public_key"])) != tx["from"]:
            return False, "pubkey/address mismatch"
        return True, "ok"

    def add_to_mempool(self, tx: Dict[str, Any]) -> Tuple[bool, str]:
        # Drop dead txs first so new valid nonces are not blocked by ghosts
        self.purge_invalid_mempool(save=False)
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
        # Reject conflicting nonce from same sender (prevents dual-stuck sends)
        frm = tx.get("from")
        nonce = int(tx.get("nonce", -1))
        for t in self.mempool:
            if t.get("from") == frm and int(t.get("nonce", -2)) == nonce:
                return False, (
                    f"nonce {nonce} already pending for this address "
                    f"(txid {(t.get('txid') or '')[:16]}…). "
                    "Wait for confirm or drop the pending tx first."
                )
        self.mempool.append(tx)
        self.save_mempool()
        return True, tid

    def purge_invalid_mempool(self, save: bool = True) -> int:
        """
        Remove mempool txs that fail validation (wrong nonce, low balance, etc.).
        Returns number of dropped txs. Call after blocks land or on broadcast.
        """
        if not self.mempool:
            return 0
        kept: List[Dict[str, Any]] = []
        # Simulate selection order so only first valid nonce-N from a sender survives
        bals = dict(self.balances)
        nonces = dict(self.nonces)
        nfts = {k: dict(v) for k, v in self.nfts.items()}
        dropped = 0
        for tx in self.mempool:
            ok, _ = self.validate_tx(
                tx,
                provisional_balances=bals,
                provisional_nonces=nonces,
                provisional_nfts=nfts,
            )
            if not ok:
                dropped += 1
                continue
            kept.append(tx)
            amount, fee = int(tx.get("amount", 0)), int(tx.get("fee", 0))
            frm, to = tx.get("from") or "", tx.get("to") or ""
            bals[frm] = bals.get(frm, 0) - amount - fee
            if amount:
                bals[to] = bals.get(to, 0) + amount
            nonces[frm] = int(tx.get("nonce", 0)) + 1
            tt = tx.get("type") or "transfer"
            if tt == "nft_mint":
                nid = tx.get("nft_id") or ""
                if nid:
                    nfts[nid] = {
                        "nft_id": nid,
                        "owner": to,
                        "creator": frm,
                        "name": tx.get("name") or "",
                        "uri": tx.get("uri") or "",
                    }
            elif tt == "nft_transfer":
                nid = tx.get("nft_id") or ""
                if nid in nfts:
                    nfts[nid]["owner"] = to
        if dropped:
            self.mempool = kept
            if save:
                self.save_mempool()
        return dropped

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
        height = int(block.get("height") or 0)
        smooth = height >= SMOOTH_DIFF_ACTIVATION_HEIGHT
        if not meets_difficulty(block_hash, int(h["difficulty"]), smooth=smooth):
            return False, "insufficient proof of work"
        # timestamp bounds (monotonic + limited future drift)
        try:
            bts = int(h["timestamp"])
            pts = int(prev["header"]["timestamp"])
        except (KeyError, TypeError, ValueError):
            return False, "bad timestamp"
        if bts < pts:
            return False, "timestamp before previous block"
        if bts > int(time.time()) + MAX_FUTURE_DRIFT_SECONDS:
            return False, "timestamp too far in the future"
        # coinbase checks
        txs = block["transactions"]
        if not txs or txs[0].get("type") != "coinbase":
            return False, "missing coinbase"
        subsidy = block_subsidy(block["height"])
        fees_total = sum(int(t.get("fee", 0)) for t in txs[1:])
        max_coinbase = subsidy + fees_total
        if int(txs[0]["amount"]) > max_coinbase:
            return False, "coinbase too large (subsidy + fees)"
        if int(txs[0]["amount"]) < subsidy:
            return False, "coinbase below subsidy"
        # validate rest against rolling state copy
        bals = dict(self.balances)
        nonces = dict(self.nonces)
        nfts = {k: dict(v) for k, v in self.nfts.items()}
        # apply coinbase
        cb = txs[0]
        bals[cb["to"]] = bals.get(cb["to"], 0) + int(cb["amount"])
        for tx in txs[1:]:
            ok, msg = self.validate_tx(
                tx,
                provisional_balances=bals,
                provisional_nonces=nonces,
                provisional_nfts=nfts,
            )
            if not ok:
                return False, f"tx invalid: {msg}"
            frm, to = tx["from"], tx["to"]
            amount, fee = int(tx.get("amount", 0)), int(tx.get("fee", 0))
            bals[frm] = bals.get(frm, 0) - amount - fee
            if amount:
                bals[to] = bals.get(to, 0) + amount
            nonces[frm] = int(tx["nonce"]) + 1
            tt = tx.get("type") or "transfer"
            if tt == "nft_mint":
                nid = tx.get("nft_id") or ""
                if nid:
                    nfts[nid] = {
                        "nft_id": nid,
                        "owner": to,
                        "creator": frm,
                        "name": tx.get("name") or "",
                        "uri": tx.get("uri") or "",
                    }
            elif tt == "nft_transfer":
                nid = tx.get("nft_id") or ""
                if nid in nfts:
                    nfts[nid]["owner"] = to
        return True, "ok"

    def append_block(self, block: Dict[str, Any]) -> Tuple[bool, str]:
        ok, msg = self.validate_block(block, self.tip())
        if not ok:
            return False, msg
        # enforce difficulty schedule for non-genesis (timestamp-aware for stall)
        try:
            at_ts = int(block["header"]["timestamp"])
        except (KeyError, TypeError, ValueError):
            return False, "bad timestamp"
        expected_diff = self.next_difficulty(at_timestamp=at_ts)
        if int(block["header"]["difficulty"]) != expected_diff:
            return False, f"wrong difficulty (got {block['header']['difficulty']}, want {expected_diff})"
        self.blocks.append(block)
        self._apply_block(block)
        # purge mempool txs that confirmed
        confirmed = {t["txid"] for t in block["transactions"] if "txid" in t}
        self.mempool = [t for t in self.mempool if t.get("txid") not in confirmed]
        # also drop invalid / conflicting-nonce leftovers
        self.purge_invalid_mempool(save=False)
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
            # difficulty schedule check against temp tip state (timestamp-aware)
            try:
                at_ts = int(b["header"]["timestamp"])
            except (KeyError, TypeError, ValueError):
                return False, f"bad timestamp at {i}"
            expected = temp.next_difficulty(at_timestamp=at_ts)
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
        now_ts = int(time.time())
        # Ensure timestamp is at least tip+1 so validation is monotonic
        tip_ts = int(self.tip()["header"]["timestamp"])
        block_ts = max(now_ts, tip_ts + 1)
        difficulty = self.next_difficulty(at_timestamp=block_ts)
        subsidy = block_subsidy(height)
        # select valid mempool txs first so coinbase can include their fees
        self.purge_invalid_mempool(save=True)
        selected: List[Dict[str, Any]] = []
        bals = dict(self.balances)
        nonces = dict(self.nonces)
        nfts = {k: dict(v) for k, v in self.nfts.items()}
        bals[miner_address] = bals.get(miner_address, 0) + subsidy
        fees_total = 0
        for tx in self.mempool[: max_txs * 2]:
            if len(selected) >= max_txs:
                break
            ok, _ = self.validate_tx(
                tx,
                provisional_balances=bals,
                provisional_nonces=nonces,
                provisional_nfts=nfts,
            )
            if not ok:
                continue
            selected.append(tx)
            amount, fee = int(tx.get("amount", 0)), int(tx.get("fee", 0))
            fees_total += fee
            bals[tx["from"]] = bals.get(tx["from"], 0) - amount - fee
            if amount:
                bals[tx["to"]] = bals.get(tx["to"], 0) + amount
            nonces[tx["from"]] = int(tx["nonce"]) + 1
            # provisional NFT ownership for multi-tx blocks
            tt = tx.get("type") or "transfer"
            if tt == "nft_mint":
                nid = tx.get("nft_id") or ""
                if nid:
                    nfts[nid] = {
                        "nft_id": nid,
                        "owner": tx["to"],
                        "creator": tx["from"],
                        "name": tx.get("name") or "",
                        "uri": tx.get("uri") or "",
                    }
            elif tt == "nft_transfer":
                nid = tx.get("nft_id") or ""
                if nid in nfts:
                    nfts[nid]["owner"] = tx["to"]

        reward = subsidy + fees_total
        # credit fees to miner in provisional bals (already had subsidy)
        bals[miner_address] = bals.get(miner_address, 0) + fees_total
        coinbase = {
            "type": "coinbase",
            "to": miner_address,
            "amount": reward,
            "height": height,
            "subsidy": subsidy,
            "fees": fees_total,
            "memo": f"Howl height {height}",
            "txid": sha256(
                f"coinbase:{height}:{miner_address}:{reward}:{fees_total}".encode()
            ).hex(),
        }

        txs = [coinbase] + selected
        txids = [t["txid"] for t in txs]
        header = {
            "version": 1,
            "prev_hash": self.tip()["hash"],
            "merkle_root": merkle_root(txids),
            "timestamp": block_ts,
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
        expect = expected_hashes(diff)
        d_f = difficulty_float_from_raw(diff)
        print(
            f"Mining block #{template['height']} | diff={format_difficulty(diff)} | "
            f"reward={format_howl(subsidy)} | txs={len(template['transactions'])-1}"
        )
        if is_smooth_difficulty_raw(diff):
            print(
                f"  Need ~{format_count(expect)} hashes on average "
                f"(smooth work index d={d_f:.3f}, target continuous)."
            )
        else:
            print(
                f"  Need ~{format_count(expect)} hashes on average "
                f"(legacy: {diff} leading zero hex digits)."
            )
        # rough laptop band so people know not to Ctrl+C after 30s
        for label, hps in (("~500 H/s", 500), ("~1.5k H/s", 1500), ("~5k H/s", 5000)):
            eta = expect / hps
            print(f"  · at {label} → avg ~{format_duration(eta)}")
        print("  Leave this running — Ctrl+C cancels the block. Luck varies.\n")
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
        luck = (expect / tried) if tried else 0.0
        print(
            f"\n✓ Block #{block['height']} {block_hash[:16]}… | "
            f"{format_count(tried)} hashes in {format_duration(elapsed)} "
            f"({tried / elapsed:,.0f} H/s) | luck ×{luck:.2f} vs avg | "
            f"+{format_howl(subsidy)}"
        )
        return block

    # ---------- info ----------

    def summary(self) -> Dict[str, Any]:
        supply = sum(self.balances.values())
        tip = self.tip()
        tip_ts = 0
        try:
            tip_ts = int((tip.get("header") or {}).get("timestamp") or 0)
        except (TypeError, ValueError):
            tip_ts = 0
        tip_age = max(0, int(time.time()) - tip_ts) if tip_ts else None
        cur_raw = self.current_difficulty()
        nxt_raw = self.next_difficulty()
        next_h = self.height() + 1
        return {
            "name": "Howlcoin",
            "ticker": "HOWL",
            "version": VERSION,
            "protocol": "0.6-smooth-diff" if next_h >= SMOOTH_DIFF_ACTIVATION_HEIGHT else "0.5-nibble-diff",
            "smooth_diff_activation_height": SMOOTH_DIFF_ACTIVATION_HEIGHT,
            "height": self.height(),
            "difficulty": cur_raw,
            "difficulty_float": self.current_difficulty_float(),
            "difficulty_label": format_difficulty(cur_raw),
            "next_difficulty": nxt_raw,
            "next_difficulty_float": difficulty_float_from_raw(nxt_raw)
            if is_smooth_difficulty_raw(nxt_raw) or next_h >= SMOOTH_DIFF_ACTIVATION_HEIGHT
            else float(nxt_raw),
            "next_difficulty_label": format_difficulty(nxt_raw)
            if next_h >= SMOOTH_DIFF_ACTIVATION_HEIGHT or is_smooth_difficulty_raw(nxt_raw)
            else f"{nxt_raw} (nibble)",
            "expected_hashes_next": expected_hashes(nxt_raw),
            "stall_seconds": STALL_SECONDS,
            "tip": tip["hash"],
            "tip_timestamp": tip_ts or None,
            "tip_age_seconds": tip_age,
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

    def list_nfts(self, owner: Optional[str] = None, limit: int = 100) -> List[Dict[str, Any]]:
        items = list(self.nfts.values())
        if owner:
            items = [n for n in items if n.get("owner") == owner]
        items.sort(key=lambda n: (-(n.get("mint_height") or 0), n.get("nft_id") or ""))
        return items[:limit]

    def get_nft(self, nft_id: str) -> Optional[Dict[str, Any]]:
        return self.nfts.get(nft_id)

    def oracle_feed(self, limit: int = 100) -> List[Dict[str, Any]]:
        items = list(self.oracle.values())
        items.sort(key=lambda o: (-(o.get("height") or 0), o.get("key") or ""))
        return items[:limit]

    def oracle_get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.oracle.get(key)

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

    def richlist(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(500, limit))
        items = sorted(self.balances.items(), key=lambda x: -x[1])[:limit]
        return [
            {
                "rank": i + 1,
                "address": addr,
                "balance": bal,
                "balance_fmt": format_howl(bal),
            }
            for i, (addr, bal) in enumerate(items)
        ]

    def mempool_list(self) -> List[Dict[str, Any]]:
        out = []
        for tx in self.mempool:
            out.append(
                {
                    "txid": tx.get("txid"),
                    "type": tx.get("type", "transfer"),
                    "from": tx.get("from"),
                    "to": tx.get("to"),
                    "amount": tx.get("amount", 0),
                    "fee": tx.get("fee", 0),
                    "nonce": tx.get("nonce"),
                    "memo": tx.get("memo", ""),
                    "confirmed": False,
                }
            )
        return list(reversed(out))

    def reload_from_disk(self) -> None:
        """Re-read chain.json (for explorer watching live nodes)."""
        if self.chain_path.exists():
            self._load()
            self._load_mempool()

