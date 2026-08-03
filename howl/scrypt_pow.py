"""Scrypt proof-of-work — same family as early Dogecoin / Litecoin."""

from __future__ import annotations

import struct
import time
from decimal import Decimal, getcontext
from typing import Any, Callable, Dict, Optional, Tuple, Union

from Crypto.Protocol.KDF import scrypt as scrypt_kdf

from .config import DIFFICULTY_MILLI, SCRYPT_DKLEN, SCRYPT_N, SCRYPT_P, SCRYPT_R

getcontext().prec = 80

# Header difficulty field: legacy nibble (1–12) vs smooth milli (e.g. 2000 = d 2.000)
SMOOTH_DIFF_RAW_THRESHOLD = 100


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


def is_smooth_difficulty_raw(difficulty_raw: int) -> bool:
    """True when header difficulty uses milli-nibble encoding (v0.6+)."""
    return int(difficulty_raw) >= SMOOTH_DIFF_RAW_THRESHOLD


def difficulty_float_from_raw(difficulty_raw: int) -> float:
    """Interpret header difficulty as nibble-equivalent float."""
    raw = int(difficulty_raw)
    if is_smooth_difficulty_raw(raw):
        return raw / float(DIFFICULTY_MILLI)
    return float(raw)


def encode_difficulty_milli(d: float) -> int:
    """Encode nibble-equivalent float as milli integer for the header field."""
    return max(1, int(round(float(d) * DIFFICULTY_MILLI)))


def target_from_difficulty_float(d: float) -> int:
    """
    Exclusive upper bound for hash integer: hash < target.
    target = 2^256 / 16^d  (= 2^(256 - 4d)).
    """
    if d <= 0:
        return 1 << 256
    target = (Decimal(2) ** 256) / (Decimal(16) ** Decimal(str(d)))
    t = int(target)
    if t < 1:
        return 1
    return t


def meets_difficulty(
    hash_hex: str,
    difficulty: Union[int, float],
    *,
    smooth: Optional[bool] = None,
) -> bool:
    """
    Check proof-of-work.

    Legacy (smooth=False): difficulty = leading zero hex nibbles.
    Smooth (smooth=True): difficulty = milli-nibble (d*1000), continuous target.
    If smooth is None, auto-detect: raw >= 100 → smooth.
    """
    raw = int(difficulty) if not isinstance(difficulty, float) else difficulty
    if smooth is None:
        if isinstance(difficulty, float):
            smooth = True
            d = float(difficulty)
            return int(hash_hex, 16) < target_from_difficulty_float(d)
        smooth = is_smooth_difficulty_raw(int(difficulty))

    if not smooth:
        n = int(difficulty)
        if n <= 0:
            return True
        return hash_hex.startswith("0" * n)

    d = difficulty_float_from_raw(int(difficulty)) if not isinstance(difficulty, float) else float(difficulty)
    if d <= 0:
        return True
    return int(hash_hex, 16) < target_from_difficulty_float(d)


def expected_hashes(difficulty: Union[int, float], *, smooth: Optional[bool] = None) -> float:
    """Average hashes needed for the given difficulty encoding."""
    if isinstance(difficulty, float):
        d = float(difficulty)
        if d <= 0:
            return 1.0
        return float(16 ** d)
    raw = int(difficulty)
    if smooth is None:
        smooth = is_smooth_difficulty_raw(raw)
    if not smooth:
        if raw <= 0:
            return 1.0
        return float(16 ** raw)
    d = difficulty_float_from_raw(raw)
    if d <= 0:
        return 1.0
    return float(16 ** d)


def format_difficulty(difficulty_raw: int) -> str:
    """Human-readable difficulty for logs and UI."""
    raw = int(difficulty_raw)
    if is_smooth_difficulty_raw(raw):
        d = difficulty_float_from_raw(raw)
        return f"{d:.3f} (smooth)"
    return f"{raw} (nibble)"


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


class MiningSliceTimeout(Exception):
    """Raised when a mining slice expires so the caller can rebuild the template."""

    def __init__(self, tried: int, seconds: float):
        self.tried = tried
        self.seconds = seconds
        super().__init__(f"mining slice timeout after {tried} hashes / {seconds:.0f}s")


def mine_block(
    header_template: Dict[str, Any],
    difficulty: int,
    max_nonce: Optional[int] = None,
    start_nonce: int = 0,
    progress_every: int = 400,
    max_seconds: Optional[float] = None,
    progress_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Tuple[Dict[str, Any], str, int]:
    """
    Brute-force nonce until scrypt hash meets difficulty.
    Returns (header, hash_hex, hashes_tried).

    If max_seconds is set, raises MiningSliceTimeout so continuous miners can
    rebuild the block template (stall relief / retarget / new mempool txs).
    """
    header = dict(header_template)
    header["difficulty"] = int(difficulty)
    nonce = start_nonce
    tried = 0
    t0 = time.time()
    expect = expected_hashes(difficulty)
    smooth = is_smooth_difficulty_raw(int(difficulty))
    d_label = format_difficulty(int(difficulty))

    def _emit() -> None:
        if not progress_callback:
            return
        elapsed = max(time.time() - t0, 1e-6)
        rate = tried / elapsed
        remain = max(0.0, expect - tried)
        try:
            progress_callback(
                {
                    "hashes": tried,
                    "expect": expect,
                    "hps": rate,
                    "elapsed": elapsed,
                    "eta_seconds": (remain / rate) if rate > 0 else None,
                    "difficulty": int(difficulty),
                    "difficulty_label": d_label,
                    "pct": min(999.0, 100.0 * tried / expect) if expect > 0 else 0.0,
                    "slice_seconds": max_seconds,
                    "nonce": nonce,
                }
            )
        except Exception:
            pass

    while True:
        header["nonce"] = nonce
        h = pow_hash_hex(header)
        tried += 1
        if meets_difficulty(h, difficulty):
            # clear progress line
            print(" " * 100, end="\r", flush=True)
            _emit()
            return header, h, tried
        nonce += 1
        if max_nonce is not None and nonce > max_nonce:
            raise RuntimeError("max_nonce exceeded without finding block")
        if max_seconds is not None and (time.time() - t0) >= float(max_seconds):
            print(" " * 100, end="\r", flush=True)
            _emit()
            raise MiningSliceTimeout(tried, time.time() - t0)
        if progress_every and tried % progress_every == 0:
            elapsed = max(time.time() - t0, 1e-6)
            rate = tried / elapsed
            pct = min(999.0, 100.0 * tried / expect) if expect > 0 else 0.0
            remain = max(0.0, expect - tried)
            eta_s = remain / rate if rate > 0 else float("inf")
            if tried >= expect:
                eta_txt = f"overdue +{format_duration(elapsed - (expect / rate) if rate else 0)} (keep going)"
            else:
                eta_txt = f"ETA ~{format_duration(eta_s)}"
            mode = "smooth" if smooth else "nibble"
            slice_note = f" | slice {format_duration(float(max_seconds))}" if max_seconds else ""
            print(
                f"  … {rate:,.0f} H/s | {pct:.1f}% of avg | "
                f"elapsed {format_duration(elapsed)} | {eta_txt} | "
                f"{format_count(tried)}/{format_count(expect)} | {mode} {d_label}{slice_note}   ",
                end="\r",
                flush=True,
            )
            _emit()


def estimate_hashrate(seconds: float = 2.0) -> float:
    """Quick local hashrate sample (hashes/sec)."""
    header = {
        "version": 1,
        "prev_hash": "00" * 32,
        "merkle_root": "11" * 32,
        "timestamp": int(time.time()),
        "difficulty": 99,  # impossible legacy — just burn cycles
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
