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


def expected_hashes(difficulty: int) -> float:
    """Average hashes needed (each leading zero nibble is 1/16)."""
    if difficulty <= 0:
        return 1.0
    return float(16**int(difficulty))


def format_duration(seconds: float) -> str:
    """Human duration for ETA / elapsed (e.g. 45s, 12.3m, 2.4h)."""
    if seconds < 0 or seconds != seconds:  # NaN
        return "?"
    if seconds < 60:
        return f"{seconds:.0f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    if seconds < 86400:
        return f"{seconds / 3600:.1f}h"
    return f"{seconds / 86400:.1f}d"


def format_count(n: float) -> str:
    """Compact hash counts: 34400 → 34.4k, 1.68e7 → 16.8M."""
    n = float(n)
    if n < 1000:
        return f"{n:.0f}"
    if n < 1_000_000:
        return f"{n / 1000:.1f}k"
    if n < 1_000_000_000:
        return f"{n / 1_000_000:.1f}M"
    return f"{n / 1_000_000_000:.2f}B"


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
    progress_every: int = 400,
) -> Tuple[Dict[str, Any], str, int]:
    """
    Brute-force nonce until scrypt hash meets difficulty.
    Returns (header, hash_hex, hashes_tried).
    Progress line shows H/s, % of expected work, elapsed, and ETA.
    """
    header = dict(header_template)
    header["difficulty"] = difficulty
    nonce = start_nonce
    tried = 0
    t0 = time.time()
    expect = expected_hashes(difficulty)

    while True:
        header["nonce"] = nonce
        h = pow_hash_hex(header)
        tried += 1
        if meets_difficulty(h, difficulty):
            # clear progress line
            print(" " * 100, end="\r", flush=True)
            return header, h, tried
        nonce += 1
        if max_nonce is not None and nonce > max_nonce:
            raise RuntimeError("max_nonce exceeded without finding block")
        if progress_every and tried % progress_every == 0:
            elapsed = max(time.time() - t0, 1e-6)
            rate = tried / elapsed
            # progress vs average expected work (can go >100% — luck)
            pct = min(999.0, 100.0 * tried / expect) if expect > 0 else 0.0
            remain = max(0.0, expect - tried)
            eta_s = remain / rate if rate > 0 else float("inf")
            # after 100% of expected, show "overdue (luck)" style ETA as next expect slice
            if tried >= expect:
                eta_txt = f"overdue +{format_duration(elapsed - (expect / rate) if rate else 0)} (keep going)"
            else:
                eta_txt = f"ETA ~{format_duration(eta_s)}"
            print(
                f"  … {rate:,.0f} H/s | {pct:.1f}% of avg | "
                f"elapsed {format_duration(elapsed)} | {eta_txt} | "
                f"{format_count(tried)}/{format_count(expect)} hashes   ",
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
