"""Local wallet storage, BIP39 mnemonics, and transaction building."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .bip39util import (
    generate_mnemonic,
    keypair_from_mnemonic,
    normalize_mnemonic,
    path_string,
    validate_mnemonic,
)
from .config import COIN, WALLET_FILE
from .crypto import KeyPair, is_valid_address, tx_sighash, txid


class Wallet:
    def __init__(self, path: Path, create_if_missing: bool = True):
        self.path = path
        self.keys: List[KeyPair] = []
        self.label = "main"
        self.mnemonic: Optional[str] = None  # BIP39 phrase if wallet is seed-backed
        self.passphrase: str = ""  # optional BIP39 passphrase (empty default)
        self.next_index: int = 1  # next derivation index for new_address
        self.derivation: str = path_string(0)
        if path.exists():
            self.load()
        elif create_if_missing:
            self.create_new()
        else:
            raise FileNotFoundError(f"No wallet at {path}")

    def create_new(self, strength: int = 128) -> KeyPair:
        """Create a new BIP39 wallet (12 words by default)."""
        phrase = generate_mnemonic(strength=strength)
        return self._init_from_mnemonic(phrase, passphrase="")

    def restore_from_mnemonic(self, phrase: str, passphrase: str = "") -> KeyPair:
        """Replace this wallet with one restored from a BIP39 phrase."""
        phrase = normalize_mnemonic(phrase)
        if not validate_mnemonic(phrase):
            raise ValueError("Invalid BIP39 mnemonic")
        return self._init_from_mnemonic(phrase, passphrase=passphrase)

    def import_private_key(self, private_key_hex: str, replace: bool = True) -> KeyPair:
        """
        Import a raw secp256k1 private key (64 hex chars).
        If replace=True, becomes the only/primary key; else appends.
        No BIP39 mnemonic is attached (legacy key wallet).
        """
        key_hex = private_key_hex.strip().lower().replace("0x", "")
        if len(key_hex) != 64:
            raise ValueError("Private key must be 64 hex characters (32 bytes)")
        try:
            bytes.fromhex(key_hex)
        except ValueError as e:
            raise ValueError("Private key is not valid hex") from e
        kp = KeyPair.from_private_hex(key_hex)
        if replace:
            self.keys = [kp]
            self.mnemonic = None
            self.passphrase = ""
            self.next_index = 1
            self.derivation = "imported-private-key"
        else:
            # avoid duplicates
            if any(k.private_key_hex == kp.private_key_hex for k in self.keys):
                return kp
            self.keys.append(kp)
        self.save()
        return kp

    def _init_from_mnemonic(self, phrase: str, passphrase: str = "") -> KeyPair:
        self.mnemonic = normalize_mnemonic(phrase)
        self.passphrase = passphrase
        self.next_index = 1
        kp = keypair_from_mnemonic(self.mnemonic, index=0, passphrase=passphrase)
        self.keys = [kp]
        self.derivation = path_string(0)
        self.save()
        return kp

    def load(self) -> None:
        data = json.loads(self.path.read_text())
        self.label = data.get("label", "main")
        self.keys = [KeyPair.from_dict(k) for k in data["keys"]]
        self.mnemonic = data.get("mnemonic")  # may be None for legacy wallets
        self.passphrase = data.get("passphrase", "")
        self.next_index = int(data.get("next_index", len(self.keys)))
        self.derivation = data.get("derivation", path_string(0))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "label": self.label,
            "coin": "HOWL",
            "version": 2,
            "keys": [k.to_dict() for k in self.keys],
            "next_index": self.next_index,
            "derivation": self.derivation,
        }
        if self.mnemonic:
            payload["mnemonic"] = self.mnemonic
            payload["passphrase"] = self.passphrase
            payload["mnemonic_warning"] = (
                "This file holds your BIP39 seed. Anyone with it can steal your HOWL."
            )
        self.path.write_text(json.dumps(payload, indent=2))

    @property
    def primary(self) -> KeyPair:
        return self.keys[0]

    @property
    def address(self) -> str:
        return self.primary.address

    def get_key_by_address(self, address: str) -> Optional[KeyPair]:
        """Find a keypair in this wallet by HOWL address."""
        for k in self.keys:
            if k.address == address:
                return k
        return None

    def list_addresses(self) -> List[str]:
        return [k.address for k in self.keys]

    @property
    def has_mnemonic(self) -> bool:
        return bool(self.mnemonic)

    def new_address(self) -> KeyPair:
        """
        Derive next address from mnemonic if available;
        otherwise generate a random key (legacy mode).
        """
        if self.mnemonic:
            idx = self.next_index
            kp = keypair_from_mnemonic(self.mnemonic, index=idx, passphrase=self.passphrase)
            self.keys.append(kp)
            self.next_index = idx + 1
            self.save()
            return kp
        kp = KeyPair.generate()
        self.keys.append(kp)
        self.save()
        return kp

    def backup_file(self) -> Path:
        """Copy wallet.json to a timestamped backup next to it."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        dest = self.path.with_name(f"wallet.backup-{ts}.json")
        shutil.copy2(self.path, dest)
        return dest

    def build_tx(
        self,
        to: str,
        amount_howlies: int,
        nonce: int,
        fee: int = 0,
        memo: str = "",
        key: Optional[KeyPair] = None,
    ) -> Dict[str, Any]:
        if not is_valid_address(to):
            raise ValueError(f"Invalid HOWL address: {to}")
        if amount_howlies <= 0:
            raise ValueError("Amount must be positive")
        key = key or self.primary
        body = {
            "from": key.address,
            "to": to,
            "amount": amount_howlies,
            "fee": fee,
            "nonce": nonce,
            "memo": memo,
            "public_key": key.public_key_hex,
        }
        sig = key.sign(tx_sighash(body))
        body["signature"] = sig
        body["txid"] = txid(body)
        return body


def format_howl(howlies: int) -> str:
    whole = howlies // COIN
    frac = abs(howlies) % COIN
    return f"{whole}.{frac:08d} HOWL"


def parse_howl(text: str) -> int:
    """Parse '123.45' or '123' into howlies."""
    text = text.strip().upper().replace("HOWL", "").strip()
    if not text:
        raise ValueError("empty amount")
    if "." in text:
        left, right = text.split(".", 1)
        right = (right + "00000000")[:8]
        return int(left) * COIN + int(right)
    return int(text) * COIN
