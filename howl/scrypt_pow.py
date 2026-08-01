"""Scrypt proof-of-work — same family as early Dogecoin / Litecoin."""

from __future__ import annotations

import json
import struct
import time
from typing import Any, Dict, Optional, Tuple

from Crypto.Protocol.KDF import scrypt as scrypt_kdf

from .config import SCRYPT_DKLEN, SCRYPT_N, SCRYPT_P, SCRYPT_R


def header_bytes(header: Dict[str, Any]) -> bytes:
    """
    Serialize block header for hashing.
    Fields: version, prev_hash, merkle_root, timestamp, difficulty, nonce
    """
    parts = [
        struct.pack(">I", int(header["version"])),
        bytes.fromhex(header["prev_hash"]),
        bytes.fromhex(header["merkle_root"]),
        struct.pack(">Q", int(header["timestamp"])),
        struct.pack(">I", int(header["difficulty"])),
        struct.pack(">Q", int(header["nonce"])),
    ]
    return b"".join(parts)


def scrypt_hash(data: bytes) -> bytes:
    """
    Dogecoin-style light Scrypt: N=1024, r=1, p=1.
    Salt = data (common simple construction for educational/meme chains).
    """
    return scrypt_kdf(
        data,
        data,  # salt mirrors input (compact single-pass header hash)
        key_len=SCRYPT_DKLEN,
        N=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
    )


def pow_hash_hex(header: Dict[str, Any]) -> str:
    return scrypt_hash(header_bytes(header)).hex()


def meets_difficulty(hash_hex: str, difficulty: int) -> bool:
    """
    Difficulty = number of leading zero hex nibbles required.
    difficulty=4 => hash must start with '0000...'
    """
    if difficulty <= 0:
        return True
    return hash_hex.startswith("0" * difficulty)


def merkle_root(txids: list) -> str:
    if not txids:
        return "0" * 64
    layer = list(txids)
    from .crypto import sha256

    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        nxt = []
        for i in range(0, len(layer), 2):
            a = bytes.fromhex(layer[i])
            b = bytes.fromhex(layer[i + 1])
            nxt.append(sha256(a + b).hex())
        layer = nxt
    return layer[0]


def mine_block(
    header_template: Dict[str, Any],
    difficulty: int,
    max_nonce: Optional[int] = None,
    start_nonce: int = 0,
    progress_every: int = 200,
) -> Tuple[Dict[str, Any], str, int]:
    """
    Brute-force nonce until scrypt hash meets difficulty.
    Returns (header, hash_hex, hashes_tried).
    """
    header = dict(header_template)
    header["difficulty"] = difficulty
    nonce = start_nonce
    tried = 0
    t0 = time.time()

    while True:
        header["nonce"] = nonce
        h = pow_hash_hex(header)
        tried += 1
        if meets_difficulty(h, difficulty):
            return header, h, tried
        nonce += 1
        if max_nonce is not None and nonce > max_nonce:
            raise RuntimeError("max_nonce exceeded without finding block")
        if progress_every and tried % progress_every == 0:
            elapsed = max(time.time() - t0, 1e-6)
            rate = tried / elapsed
            print(
                f"  … mining {tried} hashes | {rate:.1f} H/s | nonce={nonce}",
                end="\r",
                flush=True,
            )


def estimate_hashrate(seconds: float = 2.0) -> float:
    """Quick local hashrate sample (hashes/sec)."""
    header = {
        "version": 1,
        "prev_hash": "00" * 32,
        "merkle_root": "11" * 32,
        "timestamp": int(time.time()),
        "difficulty": 99,  # impossible — just burn cycles
        "nonce": 0,
    }
    t0 = time.time()
    n = 0
    while time.time() - t0 < seconds:
        header["nonce"] = n
        pow_hash_hex(header)
        n += 1
    elapsed = time.time() - t0
    return n / elapsed
