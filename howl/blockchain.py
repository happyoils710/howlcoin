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
    DIFFICULTY_MAX_UP,
    GENESIS_MESSAGE,
    INITIAL_DIFFICULTY,
    MAX_DIFFICULTY_FLOAT,
    MAX_FUTURE_DRIFT_SECONDS,
    MIN_DIFFICULTY_FLOAT,
    MIN_TX_FEE_HOWLIES,
    RETARGET_NO_UP_GAP_SECONDS,
    RETARGET_SAFETY_ACTIVATION_HEIGHT,
    SMOOTH_DIFF_ACTIVATION_HEIGHT,
    STALL_MAX_ADJUST,
    STALL_SECONDS,
    VERSION,
    block_subsidy,
)
from .crypto import is_valid_address, sha256, tx_sighash, txid, verify_signature
from .scrypt_pow import (
    MiningSliceTimeout,
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

    # Native smart-contract templates (Howl Script Contracts)
    # packpot = social pot: join until unlock_height; last joiner claims all
    # barkbond = lock HOWL until you post an on-chain howl with the bond phrase
    CONTRACT_KINDS = ("tipjar", "timelock", "escrow", "packpot", "barkbond")

    def _rebuild_state(self) -> None:
        self.balances: Dict[str, int] = {}
        self.nonces: Dict[str, int] = {}
        # nft_id -> metadata + owner
        self.nfts: Dict[str, Dict[str, Any]] = {}
        # oracle_key -> latest observation
        self.oracle: Dict[str, Dict[str, Any]] = {}
        # contract_id -> Howl Script Contract state
        self.contracts: Dict[str, Dict[str, Any]] = {}
        for block in self.blocks:
            self._apply_block(block, mutate_only=True)

    def _credit_user_amount(self, t: str, amount: int) -> bool:
        """Whether amount should credit tx['to'] as a normal transfer."""
        if amount <= 0:
            return False
        if t in ("contract_deploy", "contract_call"):
            # Funds lock into contract.balance (or payouts handled separately)
            return False
        return True

    def _apply_contract_effects(
        self,
        tx: Dict[str, Any],
        height: int,
        block_ts: int,
        block_hash: str,
        bals: Optional[Dict[str, int]] = None,
        contracts: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        """Mutate contract state (+ optional provisional bals for payouts)."""
        use_bals = bals if bals is not None else self.balances
        use_c = contracts if contracts is not None else self.contracts
        t = tx.get("type") or "transfer"
        frm = tx.get("from") or ""
        to = tx.get("to") or ""
        amount = int(tx.get("amount", 0))
        if t == "contract_deploy":
            cid = (tx.get("contract_id") or "").strip()
            kind = (tx.get("contract_kind") or "").strip().lower()
            if not cid:
                return
            try:
                min_join = int(tx.get("min_join") or 0)
            except (TypeError, ValueError):
                min_join = 0
            use_c[cid] = {
                "contract_id": cid,
                "kind": kind,
                "name": (tx.get("name") or "Contract").strip()[:80],
                "owner": frm,
                "balance": amount,  # initial fund locked
                "status": "active",
                "unlock_height": int(tx.get("unlock_height") or 0),
                "counterparty": (tx.get("counterparty") or "").strip(),
                "arbiter": (tx.get("arbiter") or "").strip(),
                "funded_by": frm if amount else "",
                "last_joiner": frm if (kind == "packpot" and amount > 0) else "",
                "join_count": 1 if (kind == "packpot" and amount > 0) else 0,
                "min_join": max(0, min_join),
                "bond_phrase": (tx.get("bond_phrase") or "").strip()[:80],
                "deploy_txid": tx.get("txid"),
                "deploy_height": height,
                "deploy_timestamp": block_ts,
                "last_txid": tx.get("txid"),
                "last_height": height,
                "last_timestamp": block_ts,
                "last_block_hash": block_hash,
                "memo": tx.get("memo") or "",
                "history": [
                    {
                        "event": "deploy",
                        "txid": tx.get("txid"),
                        "from": frm,
                        "amount": amount,
                        "height": height,
                        "timestamp": block_ts,
                        "block_hash": block_hash,
                    }
                ],
            }
            return

        if t != "contract_call":
            return
        cid = (tx.get("contract_id") or "").strip()
        method = (tx.get("method") or "").strip().lower()
        c = use_c.get(cid)
        if not c or c.get("status") == "closed":
            return
        hist = list(c.get("history") or [])
        if method in ("donate", "fund", "join"):
            c["balance"] = int(c.get("balance") or 0) + amount
            if amount and not c.get("funded_by"):
                c["funded_by"] = frm
            if method == "join" or (c.get("kind") == "packpot" and method == "fund"):
                c["last_joiner"] = frm
                c["join_count"] = int(c.get("join_count") or 0) + 1
            hist.append(
                {
                    "event": method,
                    "txid": tx.get("txid"),
                    "from": frm,
                    "amount": amount,
                    "height": height,
                    "timestamp": block_ts,
                    "block_hash": block_hash,
                }
            )
        elif method in ("withdraw", "claim", "release", "refund"):
            # Payout from contract balance → `to`
            try:
                payout = int(tx.get("call_value") or 0)
            except (TypeError, ValueError):
                payout = 0
            bal = int(c.get("balance") or 0)
            if payout <= 0 or payout > bal:
                payout = bal
            if payout > 0:
                c["balance"] = bal - payout
                use_bals[to] = use_bals.get(to, 0) + payout
            if method in ("claim", "release", "refund"):
                c["status"] = "closed"
            hist.append(
                {
                    "event": method,
                    "txid": tx.get("txid"),
                    "from": frm,
                    "to": to,
                    "amount": payout,
                    "height": height,
                    "timestamp": block_ts,
                    "block_hash": block_hash,
                }
            )
        c["last_txid"] = tx.get("txid")
        c["last_height"] = height
        c["last_timestamp"] = block_ts
        c["last_block_hash"] = block_hash
        c["history"] = hist
        use_c[cid] = c

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
            t = tx.get("type") or "transfer"
            self.balances[frm] = self.balances.get(frm, 0) - amount - fee
            if self._credit_user_amount(t, amount):
                self.balances[to] = self.balances.get(to, 0) + amount
            self.nonces[frm] = int(tx["nonce"]) + 1

            try:
                block_ts = int((block.get("header") or {}).get("timestamp") or 0)
            except (TypeError, ValueError):
                block_ts = 0
            block_hash = block.get("hash") or ""
            if t == "nft_mint":
                nid = tx.get("nft_id") or ""
                if nid:
                    self.nfts[nid] = {
                        "nft_id": nid,
                        "owner": to,
                        "creator": frm,
                        "name": tx.get("name") or "Untitled",
                        "uri": tx.get("uri") or "",
                        "memo": tx.get("memo") or "",
                        "mint_txid": tx.get("txid"),
                        "mint_height": height,
                        "mint_timestamp": block_ts,
                        "mint_block_hash": block_hash,
                        "last_txid": tx.get("txid"),
                        "last_height": height,
                        "last_timestamp": block_ts,
                        "last_block_hash": block_hash,
                        "history": [
                            {
                                "event": "mint",
                                "txid": tx.get("txid"),
                                "from": frm,
                                "to": to,
                                "height": height,
                                "timestamp": block_ts,
                                "block_hash": block_hash,
                                "name": tx.get("name") or "Untitled",
                                "uri": tx.get("uri") or "",
                            }
                        ],
                    }
            elif t == "nft_transfer":
                nid = tx.get("nft_id") or ""
                if nid and nid in self.nfts:
                    self.nfts[nid]["owner"] = to
                    self.nfts[nid]["last_txid"] = tx.get("txid")
                    self.nfts[nid]["last_height"] = height
                    self.nfts[nid]["last_timestamp"] = block_ts
                    self.nfts[nid]["last_block_hash"] = block_hash
                    hist = list(self.nfts[nid].get("history") or [])
                    hist.append(
                        {
                            "event": "transfer",
                            "txid": tx.get("txid"),
                            "from": frm,
                            "to": to,
                            "height": height,
                            "timestamp": block_ts,
                            "block_hash": block_hash,
                        }
                    )
                    self.nfts[nid]["history"] = hist
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
            elif t in ("contract_deploy", "contract_call"):
                self._apply_contract_effects(tx, height, block_ts, block_hash)

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

    def _retarget_float(
        self,
        current_d: float,
        *,
        next_height: Optional[int] = None,
        gap_seconds: int = 0,
    ) -> float:
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

        # v0.6.1+: never spike upward as hard; never raise when already slow/stalled
        h = int(next_height if next_height is not None else (self.height() + 1))
        if h >= RETARGET_SAFETY_ACTIVATION_HEIGHT:
            # Cap increases (fast windows) more tightly than decreases
            if new_d > current_d:
                new_d = min(new_d, current_d * float(DIFFICULTY_MAX_UP))
            # Last window already slow, or tip is half-stalled → only allow flat/down
            if ratio >= 1.0 or gap_seconds >= RETARGET_NO_UP_GAP_SECONDS:
                new_d = min(new_d, current_d)
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

        tip_ts = int(self.tip()["header"]["timestamp"])
        gap = max(0, at_ts - tip_ts)

        # First smooth block: tip may still be legacy nibble — float convert is fine
        if height >= DIFFICULTY_ADJUST_INTERVAL and height % DIFFICULTY_ADJUST_INTERVAL == 0:
            d = self._retarget_float(d, next_height=height, gap_seconds=gap)

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
        provisional_contracts: Optional[Dict[str, Dict[str, Any]]] = None,
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
        elif t in (
            "nft_mint",
            "nft_transfer",
            "oracle",
            "contract_deploy",
            "contract_call",
        ):
            if amount < 0:
                return False, "amount must be >= 0"
        else:
            return False, f"unknown tx type {t}"

        bals = provisional_balances if provisional_balances is not None else self.balances
        nonces = provisional_nonces if provisional_nonces is not None else self.nonces
        nfts = provisional_nfts if provisional_nfts is not None else self.nfts
        contracts = (
            provisional_contracts
            if provisional_contracts is not None
            else self.contracts
        )

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
            # On-chain names: howl.name.<slug> — first claimant wins
            if key.startswith("howl.name."):
                slug = key[len("howl.name.") :]
                ok_n, msg_n = self._validate_name_slug(slug)
                if not ok_n:
                    return False, msg_n
                existing = self.oracle.get(key)
                if existing and (existing.get("reporter") or "") != tx["from"]:
                    return False, f"name @{slug} already taken"
                # value should identify the owner address (canonical)
                if str(val).strip() and str(val).strip() != tx["from"]:
                    # allow value == slug as soft display, or must be self address
                    if str(val).strip().lower() != slug.lower():
                        if not is_valid_address(str(val).strip()):
                            return False, "name value must be your HOWL address or the name itself"
                        if str(val).strip() != tx["from"]:
                            return False, "name value address must match your wallet"
        elif t == "contract_deploy":
            ok, msg = self._validate_contract_deploy(tx, contracts)
            if not ok:
                return False, msg
        elif t == "contract_call":
            ok, msg = self._validate_contract_call(tx, contracts)
            if not ok:
                return False, msg

        # Signature over canonical body (includes extended fields when present)
        if not verify_signature(tx["public_key"], tx_sighash(tx), tx["signature"]):
            return False, "bad signature"
        from .crypto import pubkey_to_address

        if pubkey_to_address(bytes.fromhex(tx["public_key"])) != tx["from"]:
            return False, "pubkey/address mismatch"
        return True, "ok"

    def _validate_contract_deploy(
        self, tx: Dict[str, Any], contracts: Dict[str, Dict[str, Any]]
    ) -> Tuple[bool, str]:
        cid = (tx.get("contract_id") or "").strip()
        if not cid or len(cid) > 64:
            return False, "contract_id required (1–64 chars)"
        if cid in contracts:
            return False, "contract_id already exists"
        kind = (tx.get("contract_kind") or "").strip().lower()
        if kind not in self.CONTRACT_KINDS:
            return False, f"contract_kind must be one of {', '.join(self.CONTRACT_KINDS)}"
        name = (tx.get("name") or "").strip()
        if not name or len(name) > 80:
            return False, "contract name required (1–80 chars)"
        if tx["to"] != tx["from"]:
            return False, "deploy to must be self"
        amount = int(tx.get("amount", 0))
        if kind == "timelock":
            try:
                uh = int(tx.get("unlock_height") or 0)
            except (TypeError, ValueError):
                return False, "unlock_height required for timelock"
            if uh <= self.height():
                return False, "unlock_height must be in the future"
            if amount <= 0:
                return False, "timelock requires initial fund amount > 0"
        elif kind == "escrow":
            cp = (tx.get("counterparty") or "").strip()
            if not is_valid_address(cp):
                return False, "escrow needs counterparty (seller) HOWL address"
            if cp == tx["from"]:
                return False, "counterparty cannot be self"
            arb = (tx.get("arbiter") or "").strip()
            if arb and not is_valid_address(arb):
                return False, "bad arbiter address"
            try:
                uh = int(tx.get("unlock_height") or 0)
            except (TypeError, ValueError):
                uh = 0
            if uh and uh <= self.height():
                return False, "escrow unlock_height must be future or 0"
        elif kind == "packpot":
            try:
                uh = int(tx.get("unlock_height") or 0)
            except (TypeError, ValueError):
                return False, "unlock_height required for pack pot"
            if uh <= self.height():
                return False, "pack pot unlock_height must be in the future"
            try:
                mj = int(tx.get("min_join") or 0)
            except (TypeError, ValueError):
                mj = 0
            if mj < 0:
                return False, "min_join must be >= 0"
            # optional seed pot from creator
        elif kind == "barkbond":
            phrase = (tx.get("bond_phrase") or "").strip()
            if not phrase or len(phrase) > 80:
                return False, "bond_phrase required (1–80 chars) for bark bond"
            if amount <= 0:
                return False, "bark bond requires initial fund amount > 0"
            try:
                uh = int(tx.get("unlock_height") or 0)
            except (TypeError, ValueError):
                uh = 0
            if uh and uh <= self.height():
                return False, "bark bond unlock_height must be future or 0"
        # tipjar: amount optional initial seed
        return True, "ok"

    def _validate_contract_call(
        self, tx: Dict[str, Any], contracts: Dict[str, Dict[str, Any]]
    ) -> Tuple[bool, str]:
        cid = (tx.get("contract_id") or "").strip()
        if not cid or cid not in contracts:
            return False, "unknown contract_id"
        c = contracts[cid]
        if c.get("status") == "closed":
            return False, "contract is closed"
        method = (tx.get("method") or "").strip().lower()
        kind = (c.get("kind") or "").lower()
        amount = int(tx.get("amount", 0))
        frm = tx["from"]
        to = tx["to"]

        if kind == "tipjar":
            if method == "donate":
                if amount <= 0:
                    return False, "donate amount must be > 0"
                if to != frm:
                    return False, "donate to must be self"
            elif method == "withdraw":
                if amount != 0:
                    return False, "withdraw amount field must be 0 (use call_value)"
                if frm != c.get("owner"):
                    return False, "only owner can withdraw tipjar"
                if to != frm:
                    return False, "withdraw to must be owner"
                try:
                    cv = int(tx.get("call_value") or 0)
                except (TypeError, ValueError):
                    cv = 0
                bal = int(c.get("balance") or 0)
                if bal <= 0:
                    return False, "tipjar empty"
                if cv < 0 or (cv > 0 and cv > bal):
                    return False, "call_value exceeds tipjar balance"
            else:
                return False, "tipjar methods: donate, withdraw"

        elif kind == "timelock":
            if method == "fund":
                if amount <= 0:
                    return False, "fund amount must be > 0"
                if to != frm:
                    return False, "fund to must be self"
            elif method == "claim":
                if amount != 0:
                    return False, "claim amount field must be 0"
                if frm != c.get("owner"):
                    return False, "only owner can claim timelock"
                if to != frm:
                    return False, "claim to must be owner"
                uh = int(c.get("unlock_height") or 0)
                if self.height() < uh:
                    return False, f"timelock locked until height {uh}"
                if int(c.get("balance") or 0) <= 0:
                    return False, "timelock empty"
            else:
                return False, "timelock methods: fund, claim"

        elif kind == "escrow":
            owner = c.get("owner") or ""
            seller = c.get("counterparty") or ""
            arbiter = c.get("arbiter") or ""
            if method == "fund":
                if amount <= 0:
                    return False, "fund amount must be > 0"
                if frm != owner:
                    return False, "only escrow buyer (owner) can fund"
                if to != frm:
                    return False, "fund to must be self"
            elif method == "release":
                if amount != 0:
                    return False, "release amount field must be 0"
                if frm not in (owner, seller, arbiter) or not frm:
                    return False, "release: buyer, seller, or arbiter only"
                if to != seller:
                    return False, "release to must be seller (counterparty)"
                if int(c.get("balance") or 0) <= 0:
                    return False, "escrow empty"
            elif method == "refund":
                if amount != 0:
                    return False, "refund amount field must be 0"
                uh = int(c.get("unlock_height") or 0)
                allowed = frm == arbiter or (uh and self.height() >= uh and frm == owner)
                if not allowed:
                    return False, "refund: arbiter anytime, or buyer after unlock_height"
                if to != owner:
                    return False, "refund to must be buyer (owner)"
                if int(c.get("balance") or 0) <= 0:
                    return False, "escrow empty"
            else:
                return False, "escrow methods: fund, release, refund"

        elif kind == "packpot":
            # Social pot: join (fund) until unlock_height; last joiner claims all
            if method == "join":
                if amount <= 0:
                    return False, "join amount must be > 0"
                if to != frm:
                    return False, "join to must be self"
                uh = int(c.get("unlock_height") or 0)
                if self.height() >= uh:
                    return False, f"join window closed at height {uh}"
                try:
                    mj = int(c.get("min_join") or 0)
                except (TypeError, ValueError):
                    mj = 0
                if mj > 0 and amount < mj:
                    return False, f"join below min ({mj} howlies)"
            elif method == "claim":
                if amount != 0:
                    return False, "claim amount field must be 0"
                uh = int(c.get("unlock_height") or 0)
                if self.height() < uh:
                    return False, f"pack pot still open until height {uh}"
                last = (c.get("last_joiner") or "").strip()
                if not last:
                    return False, "no joiners yet"
                if frm != last:
                    return False, "only the last joiner can claim the pack pot"
                if to != last:
                    return False, "claim to must be last joiner"
                if int(c.get("balance") or 0) <= 0:
                    return False, "pack pot empty"
            else:
                return False, "pack pot methods: join, claim"

        elif kind == "barkbond":
            # Lock HOWL; release after posting howl with bond_phrase (oracle howl.bond.<cid>)
            if method == "fund":
                if amount <= 0:
                    return False, "fund amount must be > 0"
                if frm != c.get("owner"):
                    return False, "only bond owner can fund more"
                if to != frm:
                    return False, "fund to must be self"
            elif method == "claim":
                if amount != 0:
                    return False, "claim amount field must be 0"
                if frm != c.get("owner"):
                    return False, "only bond owner can claim"
                if to != frm:
                    return False, "claim to must be owner"
                uh = int(c.get("unlock_height") or 0)
                if uh and self.height() < uh:
                    return False, f"bark bond locked until height {uh}"
                if int(c.get("balance") or 0) <= 0:
                    return False, "bark bond empty"
                phrase = (c.get("bond_phrase") or "").strip().lower()
                if not phrase:
                    return False, "bond has no phrase"
                # Proof: owner posted oracle key howl.bond.<cid> with matching value
                okey = f"howl.bond.{cid}"
                row = self.oracle.get(okey) if hasattr(self, "oracle") else None
                if not row:
                    return False, (
                        f"post an on-chain howl first: oracle key {okey} "
                        f"with value containing your bond phrase"
                    )
                if (row.get("reporter") or "") != frm:
                    return False, "bond howl must be posted by the bond owner"
                val = str(row.get("value") or "").lower()
                if phrase not in val:
                    return False, "oracle value must contain the bond phrase"
            else:
                return False, "bark bond methods: fund, claim"
        else:
            return False, "unknown contract kind"

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
        contracts = {k: dict(v) for k, v in self.contracts.items()}
        dropped = 0
        for tx in self.mempool:
            ok, _ = self.validate_tx(
                tx,
                provisional_balances=bals,
                provisional_nonces=nonces,
                provisional_nfts=nfts,
                provisional_contracts=contracts,
            )
            if not ok:
                dropped += 1
                continue
            kept.append(tx)
            amount, fee = int(tx.get("amount", 0)), int(tx.get("fee", 0))
            frm, to = tx.get("from") or "", tx.get("to") or ""
            tt = tx.get("type") or "transfer"
            bals[frm] = bals.get(frm, 0) - amount - fee
            if self._credit_user_amount(tt, amount):
                bals[to] = bals.get(to, 0) + amount
            nonces[frm] = int(tx.get("nonce", 0)) + 1
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
            elif tt in ("contract_deploy", "contract_call"):
                self._apply_contract_effects(
                    tx, self.height(), 0, "", bals=bals, contracts=contracts
                )
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
        contracts = {k: dict(v) for k, v in self.contracts.items()}
        # apply coinbase
        cb = txs[0]
        bals[cb["to"]] = bals.get(cb["to"], 0) + int(cb["amount"])
        for tx in txs[1:]:
            ok, msg = self.validate_tx(
                tx,
                provisional_balances=bals,
                provisional_nonces=nonces,
                provisional_nfts=nfts,
                provisional_contracts=contracts,
            )
            if not ok:
                return False, f"tx invalid: {msg}"
            frm, to = tx["from"], tx["to"]
            amount, fee = int(tx.get("amount", 0)), int(tx.get("fee", 0))
            tt = tx.get("type") or "transfer"
            bals[frm] = bals.get(frm, 0) - amount - fee
            if self._credit_user_amount(tt, amount):
                bals[to] = bals.get(to, 0) + amount
            nonces[frm] = int(tx["nonce"]) + 1
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
            elif tt in ("contract_deploy", "contract_call"):
                try:
                    bts = int(h.get("timestamp") or 0)
                except (TypeError, ValueError):
                    bts = 0
                self._apply_contract_effects(
                    tx,
                    int(block.get("height") or 0),
                    bts,
                    block.get("hash") or "",
                    bals=bals,
                    contracts=contracts,
                )
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
        contracts = {k: dict(v) for k, v in self.contracts.items()}
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
                provisional_contracts=contracts,
            )
            if not ok:
                continue
            selected.append(tx)
            amount, fee = int(tx.get("amount", 0)), int(tx.get("fee", 0))
            fees_total += fee
            tt = tx.get("type") or "transfer"
            bals[tx["from"]] = bals.get(tx["from"], 0) - amount - fee
            if self._credit_user_amount(tt, amount):
                bals[tx["to"]] = bals.get(tx["to"], 0) + amount
            nonces[tx["from"]] = int(tx["nonce"]) + 1
            # provisional NFT / contract state for multi-tx blocks
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
            elif tt in ("contract_deploy", "contract_call"):
                self._apply_contract_effects(
                    tx, height, block_ts, "", bals=bals, contracts=contracts
                )

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

    def mine_one(self, miner_address: str, slice_seconds: float = 90.0) -> Dict[str, Any]:
        """
        Mine the next block. Rebuilds the template every `slice_seconds` so
        stall relief / retarget / mempool can update (avoids multi-day stuck slices).
        """
        total_tried = 0
        t0 = time.time()
        while True:
            template = self.build_block_template(miner_address)
            diff = template["difficulty"]
            subsidy = template["subsidy"]
            expect = expected_hashes(diff)
            d_f = difficulty_float_from_raw(diff)
            height = template["height"]
            slice_started = time.time()
            self.mine_progress = {
                "active": True,
                "height": height,
                "difficulty": diff,
                "difficulty_label": format_difficulty(diff),
                "difficulty_float": d_f,
                "expect": expect,
                "hashes": 0,
                "hps": 0.0,
                "elapsed": 0.0,
                "eta_seconds": expect / 1000.0 if expect else None,
                "slice_seconds": float(slice_seconds),
                "slice_started": slice_started,
                "refresh_in": float(slice_seconds),
                "total_hashes": total_tried,
                "started_at": t0,
            }
            print(
                f"Mining block #{height} | diff={format_difficulty(diff)} | "
                f"reward={format_howl(subsidy)} | txs={len(template['transactions'])-1}"
            )
            if is_smooth_difficulty_raw(diff):
                print(
                    f"  Need ~{format_count(expect)} hashes on average "
                    f"(smooth d={d_f:.3f}). Refreshing template every {slice_seconds:.0f}s."
                )
            else:
                print(
                    f"  Need ~{format_count(expect)} hashes on average "
                    f"(legacy: {diff} leading zero hex digits)."
                )
            for label, hps in (("~500 H/s", 500), ("~1.5k H/s", 1500), ("~5k H/s", 5000)):
                eta = expect / hps
                print(f"  · at {label} → avg ~{format_duration(eta)}")
            print("  Leave this running — Ctrl+C cancels the block. Luck varies.\n")

            def _prog(p: Dict[str, Any]) -> None:
                now = time.time()
                self.mine_progress = {
                    **getattr(self, "mine_progress", {}),
                    "active": True,
                    "height": height,
                    "difficulty": diff,
                    "difficulty_label": format_difficulty(diff),
                    "difficulty_float": d_f,
                    "expect": expect,
                    "hashes": p.get("hashes", 0),
                    "hps": p.get("hps", 0.0),
                    "elapsed": p.get("elapsed", 0.0),
                    "eta_seconds": p.get("eta_seconds"),
                    "pct": p.get("pct", 0.0),
                    "slice_seconds": float(slice_seconds),
                    "slice_started": slice_started,
                    "refresh_in": max(0.0, float(slice_seconds) - (now - slice_started)),
                    "total_hashes": total_tried + int(p.get("hashes") or 0),
                    "started_at": t0,
                }

            try:
                header, block_hash, tried = mine_block(
                    template["header"],
                    difficulty=diff,
                    max_seconds=float(slice_seconds),
                    progress_callback=_prog,
                )
            except MiningSliceTimeout as e:
                total_tried += e.tried
                print(
                    f"  ↻ Template refresh after {format_duration(e.seconds)} "
                    f"({format_count(e.tried)} hashes) — rechecking difficulty/stall…"
                )
                continue
            total_tried += tried
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
            self.mine_progress = {
                "active": False,
                "height": self.height(),
                "last_found_height": block["height"],
                "last_found_hash": block_hash,
                "total_hashes": total_tried,
                "hps": total_tried / elapsed,
                "elapsed": elapsed,
            }
            print(
                f"\n✓ Block #{block['height']} {block_hash[:16]}… | "
                f"{format_count(total_tried)} hashes in {format_duration(elapsed)} "
                f"({total_tried / elapsed:,.0f} H/s) | last slice luck ×{luck:.2f} | "
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
            "retarget_safety_height": RETARGET_SAFETY_ACTIVATION_HEIGHT,
            "tip": tip["hash"],
            "tip_timestamp": tip_ts or None,
            "tip_age_seconds": tip_age,
            "mine_progress": getattr(self, "mine_progress", None),
            "mempool": len(self.mempool),
            "addresses": len(self.balances),
            "circulating_howlies": supply,
            "circulating": format_howl(supply),
            "algo": "scrypt (N=1024,r=1,p=1)",
            "block_time_target": f"{BLOCK_TIME_SECONDS}s",
        }

    def network_health(self, window: int = 40) -> Dict[str, Any]:
        """
        Rolling block-time / difficulty series for Howlscan health charts.
        """
        n = max(5, min(int(window), 120))
        blocks = self.blocks[-n:] if len(self.blocks) > n else list(self.blocks)
        series = []
        times = []
        for i, b in enumerate(blocks):
            try:
                ts = int((b.get("header") or {}).get("timestamp") or 0)
            except (TypeError, ValueError):
                ts = 0
            try:
                raw = int((b.get("header") or {}).get("difficulty") or 0)
            except (TypeError, ValueError):
                raw = 0
            d_f = difficulty_float_from_raw(raw) if raw else 0.0
            dt = None
            if i > 0:
                try:
                    prev_ts = int((blocks[i - 1].get("header") or {}).get("timestamp") or 0)
                    if ts and prev_ts:
                        dt = max(0, ts - prev_ts)
                        times.append(dt)
                except (TypeError, ValueError):
                    pass
            series.append(
                {
                    "height": b.get("height"),
                    "timestamp": ts or None,
                    "difficulty": raw,
                    "difficulty_float": d_f,
                    "block_time": dt,
                }
            )
        avg_bt = (sum(times) / len(times)) if times else None
        tip_age = None
        try:
            tip_ts = int((self.tip().get("header") or {}).get("timestamp") or 0)
            if tip_ts:
                tip_age = max(0, int(time.time()) - tip_ts)
        except (TypeError, ValueError):
            pass
        healthy = True
        status = "ok"
        if tip_age is not None and tip_age > STALL_SECONDS:
            healthy = False
            status = "stalled"
        elif tip_age is not None and tip_age > BLOCK_TIME_SECONDS * 10:
            status = "slow"
        return {
            "height": self.height(),
            "tip_age_seconds": tip_age,
            "target_block_time": BLOCK_TIME_SECONDS,
            "avg_block_time": avg_bt,
            "window": len(series),
            "status": status,
            "healthy": healthy,
            "stall_seconds": STALL_SECONDS,
            "retarget_safety_height": RETARGET_SAFETY_ACTIVATION_HEIGHT,
            "version": VERSION,
            "series": series,
            "difficulty_label": format_difficulty(self.current_difficulty()),
            "next_difficulty_label": format_difficulty(self.next_difficulty()),
            "expected_hashes_next": expected_hashes(self.next_difficulty()),
            "mempool": len(self.mempool),
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

    def _block_meta(self, height: Optional[int]) -> Dict[str, Any]:
        """Timestamp + hash for a block height (for NFT enrichment)."""
        if height is None:
            return {"timestamp": None, "block_hash": None, "iso": None}
        try:
            h = int(height)
        except (TypeError, ValueError):
            return {"timestamp": None, "block_hash": None, "iso": None}
        if h < 0 or h >= len(self.blocks):
            return {"timestamp": None, "block_hash": None, "iso": None}
        b = self.blocks[h]
        try:
            ts = int((b.get("header") or {}).get("timestamp") or 0) or None
        except (TypeError, ValueError):
            ts = None
        iso = None
        if ts:
            try:
                from datetime import datetime, timezone

                iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (OSError, OverflowError, ValueError):
                iso = None
        return {"timestamp": ts, "block_hash": b.get("hash"), "iso": iso}

    def _enrich_nft(self, n: Dict[str, Any], *, include_history: bool = True) -> Dict[str, Any]:
        """Copy NFT with full on-chain timestamps (mint + last + event history)."""
        out = dict(n)
        mint_h = out.get("mint_height")
        last_h = out.get("last_height") if out.get("last_height") is not None else mint_h
        mint_m = self._block_meta(mint_h)
        last_m = self._block_meta(last_h)
        # Prefer stored values; fall back to block header lookup (legacy NFTs)
        if not out.get("mint_timestamp"):
            out["mint_timestamp"] = mint_m["timestamp"]
        if not out.get("mint_block_hash"):
            out["mint_block_hash"] = mint_m["block_hash"]
        if not out.get("last_timestamp"):
            out["last_timestamp"] = last_m["timestamp"] or out.get("mint_timestamp")
        if not out.get("last_block_hash"):
            out["last_block_hash"] = last_m["block_hash"] or out.get("mint_block_hash")
        out["mint_time_iso"] = mint_m["iso"]
        if out.get("mint_timestamp") and not out.get("mint_time_iso"):
            try:
                from datetime import datetime, timezone

                out["mint_time_iso"] = datetime.fromtimestamp(
                    int(out["mint_timestamp"]), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (OSError, OverflowError, ValueError, TypeError):
                pass
        out["last_time_iso"] = last_m["iso"]
        if out.get("last_timestamp") and not out.get("last_time_iso"):
            try:
                from datetime import datetime, timezone

                out["last_time_iso"] = datetime.fromtimestamp(
                    int(out["last_timestamp"]), tz=timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ")
            except (OSError, OverflowError, ValueError, TypeError):
                pass

        hist = list(out.get("history") or [])
        if not hist and out.get("mint_txid"):
            # Rebuild minimal history for pre-upgrade NFTs
            hist = [
                {
                    "event": "mint",
                    "txid": out.get("mint_txid"),
                    "from": out.get("creator"),
                    "to": out.get("creator"),
                    "height": out.get("mint_height"),
                    "timestamp": out.get("mint_timestamp"),
                    "block_hash": out.get("mint_block_hash"),
                    "name": out.get("name"),
                    "uri": out.get("uri"),
                }
            ]
            if out.get("last_txid") and out.get("last_txid") != out.get("mint_txid"):
                hist.append(
                    {
                        "event": "transfer",
                        "txid": out.get("last_txid"),
                        "from": None,
                        "to": out.get("owner"),
                        "height": out.get("last_height"),
                        "timestamp": out.get("last_timestamp"),
                        "block_hash": out.get("last_block_hash"),
                    }
                )
        # Fill missing timestamps on each history event from block headers
        filled = []
        for ev in hist:
            e = dict(ev)
            if e.get("height") is not None and not e.get("timestamp"):
                m = self._block_meta(e.get("height"))
                e["timestamp"] = m["timestamp"]
                if not e.get("block_hash"):
                    e["block_hash"] = m["block_hash"]
            if e.get("timestamp"):
                try:
                    from datetime import datetime, timezone

                    e["time_iso"] = datetime.fromtimestamp(
                        int(e["timestamp"]), tz=timezone.utc
                    ).strftime("%Y-%m-%dT%H:%M:%SZ")
                except (OSError, OverflowError, ValueError, TypeError):
                    e["time_iso"] = None
            else:
                e["time_iso"] = None
            filled.append(e)
        if include_history:
            out["history"] = filled
            out["history_count"] = len(filled)
        else:
            out.pop("history", None)
            out["history_count"] = len(filled)
        return out

    def list_nfts(
        self,
        owner: Optional[str] = None,
        limit: int = 100,
        *,
        include_history: bool = False,
    ) -> List[Dict[str, Any]]:
        items = list(self.nfts.values())
        if owner:
            items = [n for n in items if n.get("owner") == owner]
        items.sort(
            key=lambda n: (
                -(n.get("last_height") or n.get("mint_height") or 0),
                -(n.get("mint_height") or 0),
                n.get("nft_id") or "",
            )
        )
        return [
            self._enrich_nft(n, include_history=include_history) for n in items[:limit]
        ]

    def get_nft(self, nft_id: str, *, include_history: bool = True) -> Optional[Dict[str, Any]]:
        n = self.nfts.get(nft_id)
        if not n:
            return None
        return self._enrich_nft(n, include_history=include_history)

    def nft_events(self, limit: int = 100) -> List[Dict[str, Any]]:
        """All NFT mint/transfer events on-chain with timestamps (newest first)."""
        events: List[Dict[str, Any]] = []
        for b in self.blocks:
            height = b.get("height", 0)
            try:
                ts = int((b.get("header") or {}).get("timestamp") or 0) or None
            except (TypeError, ValueError):
                ts = None
            iso = None
            if ts:
                try:
                    from datetime import datetime, timezone

                    iso = datetime.fromtimestamp(ts, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                except (OSError, OverflowError, ValueError):
                    iso = None
            for tx in b.get("transactions") or []:
                t = tx.get("type") or "transfer"
                if t not in ("nft_mint", "nft_transfer"):
                    continue
                events.append(
                    {
                        "event": "mint" if t == "nft_mint" else "transfer",
                        "nft_id": tx.get("nft_id"),
                        "name": tx.get("name") or "",
                        "uri": tx.get("uri") or "",
                        "from": tx.get("from"),
                        "to": tx.get("to"),
                        "txid": tx.get("txid"),
                        "height": height,
                        "timestamp": ts,
                        "time_iso": iso,
                        "block_hash": b.get("hash"),
                        "memo": tx.get("memo") or "",
                    }
                )
        events.reverse()  # newest first
        return events[:limit]

    def oracle_feed(self, limit: int = 100) -> List[Dict[str, Any]]:
        items = list(self.oracle.values())
        items.sort(key=lambda o: (-(o.get("height") or 0), o.get("key") or ""))
        return items[:limit]

    def oracle_get(self, key: str) -> Optional[Dict[str, Any]]:
        return self.oracle.get(key)

    # ---------- on-chain names (howl.name.<slug>) ----------

    NAME_PREFIX = "howl.name."
    NAME_RESERVED = frozenset(
        {
            "howl",
            "howlcoin",
            "admin",
            "null",
            "undefined",
            "owner",
            "root",
            "miner",
            "genesis",
            "oracle",
            "system",
            "support",
            "official",
        }
    )

    @classmethod
    def _validate_name_slug(cls, slug: str) -> Tuple[bool, str]:
        s = (slug or "").strip().lower()
        if len(s) < 3 or len(s) > 16:
            return False, "name must be 3–16 characters"
        if not all(c.isalnum() or c == "_" for c in s):
            return False, "name: only a–z, 0–9, underscore"
        if s[0] == "_" or s[-1] == "_":
            return False, "name cannot start/end with underscore"
        if s in cls.NAME_RESERVED:
            return False, f"name @{s} is reserved"
        if s.isdigit():
            return False, "name cannot be only digits"
        return True, "ok"

    def name_registry(self) -> Dict[str, Dict[str, Any]]:
        """
        Map slug -> { name, address, height, txid, key }.
        Primary: oracle keys howl.name.<slug> (reporter = owner).
        Legacy: single key howl.name with value=slug (reporter = owner).
        """
        reg: Dict[str, Dict[str, Any]] = {}
        # legacy first (weaker) — overwritten by explicit howl.name.slug keys
        legacy = self.oracle.get("howl.name")
        if legacy:
            slug = str(legacy.get("value") or "").strip().lower()
            addr = legacy.get("reporter") or ""
            ok, _ = self._validate_name_slug(slug)
            if ok and addr:
                reg[slug] = {
                    "name": slug,
                    "address": addr,
                    "height": legacy.get("height"),
                    "txid": legacy.get("txid"),
                    "key": "howl.name",
                    "legacy": True,
                }
        for key, row in self.oracle.items():
            if not key.startswith(self.NAME_PREFIX):
                continue
            slug = key[len(self.NAME_PREFIX) :].strip().lower()
            ok, _ = self._validate_name_slug(slug)
            if not ok:
                continue
            addr = row.get("reporter") or ""
            val = str(row.get("value") or "").strip()
            if is_valid_address(val):
                addr = val
            if not addr:
                continue
            reg[slug] = {
                "name": slug,
                "address": addr,
                "height": row.get("height"),
                "txid": row.get("txid"),
                "key": key,
                "legacy": False,
            }
        return reg

    def resolve_name(self, name: str) -> Optional[Dict[str, Any]]:
        s = (name or "").strip().lower()
        if s.startswith("@"):
            s = s[1:]
        if s.endswith(".howl"):
            s = s[: -len(".howl")]
        reg = self.name_registry()
        return reg.get(s)

    def name_for_address(self, address: str) -> Optional[str]:
        addr = (address or "").strip()
        if not addr:
            return None
        # prefer non-legacy, highest height if multiple
        best: Optional[Tuple[int, str]] = None
        for slug, row in self.name_registry().items():
            if row.get("address") != addr:
                continue
            h = int(row.get("height") or 0)
            if best is None or h >= best[0]:
                best = (h, slug)
        return best[1] if best else None

    def list_names(self, limit: int = 200) -> List[Dict[str, Any]]:
        items = list(self.name_registry().values())
        items.sort(
            key=lambda r: (-(r.get("height") or 0), r.get("name") or "")
        )
        return items[: max(1, min(500, limit))]

    def _enrich_contract(self, c: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(c)
        bal = int(out.get("balance") or 0)
        out["balance_fmt"] = format_howl(bal)
        return out

    def list_contracts(
        self,
        owner: Optional[str] = None,
        kind: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        items = list(self.contracts.values())
        if owner:
            o = owner.strip()
            items = [
                c
                for c in items
                if c.get("owner") == o
                or c.get("counterparty") == o
                or c.get("arbiter") == o
                or c.get("last_joiner") == o
                # open pack pots are public join games
                or (
                    (c.get("kind") or "").lower() == "packpot"
                    and c.get("status") == "active"
                )
            ]
        if kind:
            k = kind.strip().lower()
            items = [c for c in items if (c.get("kind") or "").lower() == k]
        items.sort(
            key=lambda c: (
                -(c.get("last_height") or c.get("deploy_height") or 0),
                c.get("contract_id") or "",
            )
        )
        return [self._enrich_contract(c) for c in items[:limit]]

    def get_contract(self, contract_id: str) -> Optional[Dict[str, Any]]:
        c = self.contracts.get(contract_id)
        if not c:
            return None
        return self._enrich_contract(c)

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
                            "type": tx.get("type") or "transfer",
                            "from": tx.get("from"),
                            "to": tx.get("to"),
                            "amount": tx.get("amount"),
                            "fee": tx.get("fee", 0),
                            "block_height": b["height"],
                            "block_hash": b["hash"],
                            "direction": direction,
                            "contract_id": tx.get("contract_id"),
                            "method": tx.get("method"),
                            "nft_id": tx.get("nft_id"),
                        }
                    )
        txs = list(reversed(txs))[:limit]
        nm = self.name_for_address(address)
        return {
            "address": address,
            "balance": self.balance(address),
            "balance_fmt": format_howl(self.balance(address)),
            "nonce": self.next_nonce(address),
            "name": nm,
            "name_display": f"@{nm}" if nm else None,
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

