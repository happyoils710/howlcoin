"""Keys, addresses, and transaction signatures (ECDSA secp256k1)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import base58
from ecdsa import SECP256k1, SigningKey, VerifyingKey, BadSignatureError
from ecdsa.util import sigencode_string, sigdecode_string


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def double_sha256(data: bytes) -> bytes:
    return sha256(sha256(data))


def ripemd160(data: bytes) -> bytes:
    h = hashlib.new("ripemd160")
    h.update(data)
    return h.digest()


def hash160(data: bytes) -> bytes:
    return ripemd160(sha256(data))


# Version byte for Howlcoin addresses (distinct from BTC/DOGE; base58 starts with H)
ADDRESS_VERSION = b"\x28"


def pubkey_to_address(pubkey: bytes) -> str:
    payload = ADDRESS_VERSION + hash160(pubkey)
    checksum = double_sha256(payload)[:4]
    return base58.b58encode(payload + checksum).decode("ascii")


def is_valid_address(address: str) -> bool:
    try:
        raw = base58.b58decode(address)
    except Exception:
        return False
    if len(raw) != 25:
        return False
    payload, checksum = raw[:-4], raw[-4:]
    if double_sha256(payload)[:4] != checksum:
        return False
    return payload[:1] == ADDRESS_VERSION


@dataclass
class KeyPair:
    private_key_hex: str
    public_key_hex: str
    address: str

    @classmethod
    def generate(cls) -> "KeyPair":
        sk = SigningKey.generate(curve=SECP256k1)
        vk = sk.get_verifying_key()
        priv = sk.to_string()
        pub = b"\x04" + vk.to_string()  # uncompressed
        return cls(
            private_key_hex=priv.hex(),
            public_key_hex=pub.hex(),
            address=pubkey_to_address(pub),
        )

    @classmethod
    def from_private_hex(cls, private_key_hex: str) -> "KeyPair":
        sk = SigningKey.from_string(bytes.fromhex(private_key_hex), curve=SECP256k1)
        vk = sk.get_verifying_key()
        pub = b"\x04" + vk.to_string()
        return cls(
            private_key_hex=private_key_hex,
            public_key_hex=pub.hex(),
            address=pubkey_to_address(pub),
        )

    def sign(self, message: bytes) -> str:
        sk = SigningKey.from_string(bytes.fromhex(self.private_key_hex), curve=SECP256k1)
        sig = sk.sign_deterministic(message, hashfunc=hashlib.sha256, sigencode=sigencode_string)
        return sig.hex()

    def to_dict(self) -> Dict[str, str]:
        return {
            "private_key": self.private_key_hex,
            "public_key": self.public_key_hex,
            "address": self.address,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, str]) -> "KeyPair":
        return cls(
            private_key_hex=d["private_key"],
            public_key_hex=d["public_key"],
            address=d["address"],
        )


def verify_signature(public_key_hex: str, message: bytes, signature_hex: str) -> bool:
    try:
        pub = bytes.fromhex(public_key_hex)
        if pub.startswith(b"\x04") and len(pub) == 65:
            pub = pub[1:]
        vk = VerifyingKey.from_string(pub, curve=SECP256k1)
        vk.verify(
            bytes.fromhex(signature_hex),
            message,
            hashfunc=hashlib.sha256,
            sigdecode=sigdecode_string,
        )
        return True
    except (BadSignatureError, ValueError, Exception):
        return False


def tx_sighash(tx_body: Dict[str, Any]) -> bytes:
    """Canonical bytes that get signed for a transaction (exclude signature fields)."""
    body: Dict[str, Any] = {
        "from": tx_body.get("from"),
        "to": tx_body.get("to"),
        "amount": tx_body.get("amount"),
        "fee": tx_body.get("fee", 0),
        "nonce": tx_body.get("nonce"),
        "memo": tx_body.get("memo", ""),
    }
    # Extended ops (NFT / oracle) — only included when set so legacy transfers stay valid
    tx_type = tx_body.get("type") or "transfer"
    if tx_type and tx_type != "transfer":
        body["type"] = tx_type
    for k in (
        "nft_id",
        "name",
        "uri",
        "oracle_key",
        "oracle_value",
        "source_chain",
        "observed_at",
    ):
        if tx_body.get(k) is not None and tx_body.get(k) != "":
            body[k] = tx_body[k]
    return sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode())


def txid(tx: Dict[str, Any]) -> str:
    """Transaction id = sha256 of full tx json (with sig)."""
    payload = json.dumps(tx, sort_keys=True, separators=(",", ":")).encode()
    return sha256(payload).hex()


def secure_token(n: int = 16) -> str:
    return os.urandom(n).hex()
