"""BIP39 mnemonics + BIP32 derivation for Howlcoin wallets."""

from __future__ import annotations

import hashlib
import hmac
import struct
from typing import List, Optional, Tuple

from ecdsa import SECP256k1, SigningKey
from ecdsa.ellipticcurve import Point
from mnemonic import Mnemonic

from .crypto import KeyPair, pubkey_to_address

# BIP44 coin type (unregistered meme-coin id; matches our P2P port vibe)
HOWL_COIN_TYPE = 42069
# m/44'/42069'/0'/0/index
DERIVATION_PURPOSE = 44
ACCOUNT = 0
CHANGE = 0

_MNEMO = Mnemonic("english")
# Force plain Python int — conda/gmpy ecdsa may return mpz (no .to_bytes)
_CURVE_ORDER = int(SECP256k1.order)
_GEN = SECP256k1.generator


def _int_to_32(n) -> bytes:
    """Serialize integer/mpz to 32 big-endian bytes."""
    return int(n).to_bytes(32, "big")


def generate_mnemonic(strength: int = 128) -> str:
    """Generate a BIP39 mnemonic (128-bit → 12 words, 256-bit → 24 words)."""
    return _MNEMO.generate(strength=strength)


def validate_mnemonic(phrase: str) -> bool:
    return _MNEMO.check(normalize_mnemonic(phrase))


def normalize_mnemonic(phrase: str) -> str:
    return " ".join(phrase.strip().lower().split())


def mnemonic_to_seed(phrase: str, passphrase: str = "") -> bytes:
    phrase = normalize_mnemonic(phrase)
    if not _MNEMO.check(phrase):
        raise ValueError("Invalid BIP39 mnemonic (checksum or word list)")
    return _MNEMO.to_seed(phrase, passphrase=passphrase)


def _ser32(i: int) -> bytes:
    return struct.pack(">I", i)


def _point_from_priv(k: int) -> Point:
    return k * _GEN


def _ser_p(point: Point) -> bytes:
    """Compressed SEC1 public key."""
    x = int(point.x())
    y = int(point.y())
    prefix = b"\x02" if (y % 2 == 0) else b"\x03"
    return prefix + _int_to_32(x)


def _ckd_priv(parent_key: bytes, parent_chain: bytes, index: int) -> Tuple[bytes, bytes]:
    """BIP32 child key derivation (private)."""
    assert len(parent_key) == 32
    assert len(parent_chain) == 32
    if index >= 0x80000000:
        data = b"\x00" + parent_key + _ser32(index)
    else:
        parent_int = int.from_bytes(parent_key, "big")
        if parent_int == 0 or parent_int >= _CURVE_ORDER:
            raise ValueError("invalid parent private key")
        data = _ser_p(_point_from_priv(parent_int)) + _ser32(index)
    I = hmac.new(parent_chain, data, hashlib.sha512).digest()
    IL, IR = I[:32], I[32:]
    il_int = int.from_bytes(IL, "big")
    child = int((il_int + int.from_bytes(parent_key, "big")) % _CURVE_ORDER)
    if il_int >= _CURVE_ORDER or child == 0:
        # extremely rare; skip to next index in callers if needed
        raise ValueError("invalid child key, try next index")
    return _int_to_32(child), IR


def master_from_seed(seed: bytes) -> Tuple[bytes, bytes]:
    I = hmac.new(b"Bitcoin seed", seed, hashlib.sha512).digest()
    return I[:32], I[32:]


def derive_path(seed: bytes, path: List[int]) -> bytes:
    key, chain = master_from_seed(seed)
    for index in path:
        key, chain = _ckd_priv(key, chain, index)
    return key


def howl_path(index: int = 0) -> List[int]:
    """BIP44 path m/44'/42069'/0'/0/index"""
    return [
        DERIVATION_PURPOSE | 0x80000000,
        HOWL_COIN_TYPE | 0x80000000,
        ACCOUNT | 0x80000000,
        CHANGE,
        index,
    ]


def keypair_from_mnemonic(
    phrase: str,
    index: int = 0,
    passphrase: str = "",
) -> KeyPair:
    seed = mnemonic_to_seed(phrase, passphrase=passphrase)
    priv = derive_path(seed, howl_path(index))
    return KeyPair.from_private_hex(priv.hex())


def path_string(index: int = 0) -> str:
    return f"m/44'/{HOWL_COIN_TYPE}'/{ACCOUNT}'/{CHANGE}/{index}"
