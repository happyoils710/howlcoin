"""Chain parameters — early-Doge energy, Scrypt PoW."""

from pathlib import Path

# --- Identity ---
COIN_NAME = "Howlcoin"
TICKER = "HOWL"
VERSION = "0.6.4"
GENESIS_MESSAGE = (
    "2026-08-01 Howlcoin: the moon heard a howl and howled back. "
    "Scrypt free. Much chain. Very wow."
)

# --- Units ---
# 1 HOWL = 100_000_000 howlies (same 8-decimal convention as BTC/DOGE)
COIN = 100_000_000
DECIMALS = 8

# --- Consensus (Doge-flavored) ---
# Scrypt parameters identical in spirit to Litecoin/Dogecoin (N=1024, r=1, p=1)
SCRYPT_N = 1024
SCRYPT_R = 1
SCRYPT_P = 1
SCRYPT_DKLEN = 32

# Target block time ~60s like early Dogecoin
BLOCK_TIME_SECONDS = 60

# Difficulty retarget every N blocks (early Doge retargeted often)
DIFFICULTY_ADJUST_INTERVAL = 20
# Clamp retarget so difficulty cannot swing more than 4x per window
DIFFICULTY_MAX_ADJUST = 4.0

# Starting difficulty: leading zero-nibbles in the scrypt hex digest (legacy era)
# 4 = casual CPU-mineable on a laptop; raise later as hashrate grows
INITIAL_DIFFICULTY = 4

# --- v0.6 smooth difficulty hard fork ---
# From this height, header.difficulty is milli-nibble work (d*1000), not raw
# leading-zero nibble count. PoW check uses continuous target 2^(256-4d).
# Heights below this keep the legacy nibble schedule for historical validation.
SMOOTH_DIFF_ACTIVATION_HEIGHT = 120
# Encode float difficulty d as int(round(d * DIFFICULTY_MILLI))
DIFFICULTY_MILLI = 1000
# Float bounds after activation (nibble-equivalent scale)
MIN_DIFFICULTY_FLOAT = 1.0
MAX_DIFFICULTY_FLOAT = 12.0
# Stall relief: if (block_ts - tip_ts) exceeds this, difficulty may drop
# beyond the normal 4× retarget clamp (deterministic from timestamps).
STALL_SECONDS = 2 * 60 * 60  # 2 hours
STALL_MAX_ADJUST = 16.0  # max extra reduction factor when heavily stalled
# Block timestamp may not be more than this far ahead of local clock
MAX_FUTURE_DRIFT_SECONDS = 2 * 60 * 60

# --- v0.6.1 retarget safety (does NOT rewrite historical blocks) ---
# From this height, upward retarget is softer and never raises difficulty when
# the last window was already slow or the tip is half-stalled.
RETARGET_SAFETY_ACTIVATION_HEIGHT = 300
# Max difficulty *increase* per window after safety activation (down still 4×)
DIFFICULTY_MAX_UP = 2.0
# If tip age ≥ this when retargeting, never increase difficulty
RETARGET_NO_UP_GAP_SECONDS = STALL_SECONDS // 2  # 1 hour

# Block subsidy schedule (in HOWL, not howlies)
# Early Doge vibes: generous early rewards, then taper
def block_subsidy_howl(height: int) -> int:
    """Return coinbase reward in whole HOWL for a given height."""
    if height == 0:
        return 0  # genesis has no spendable subsidy (message only)
    if height < 1000:
        return 500_000  # launch era — fat stacks
    if height < 10_000:
        return 250_000
    if height < 50_000:
        return 100_000
    if height < 200_000:
        return 50_000
    if height < 500_000:
        return 10_000
    # tail emission so miners always have a reason to howl
    return 1_000


def block_subsidy(height: int) -> int:
    """Return coinbase reward in howlies (base units)."""
    return block_subsidy_howl(height) * COIN


# Soft cap narrative (not hard-enforced; emission asymptotes via tail)
MAX_MONEY_HOWL = 100_000_000_000  # 100 billion HOWL vibe-cap

# --- Transaction fees (paid to the block miner) ---
# Minimum fee required to relay/include a transfer. Helps fund nodes that mine.
MIN_TX_FEE_HOWLIES = 1 * COIN  # 1 HOWL minimum
DEFAULT_TX_FEE_HOWLIES = 1 * COIN  # default wallet fee suggestion

# --- Network ---
DEFAULT_P2P_PORT = 42069
DEFAULT_RPC_PORT = 42070
MAGIC_BYTES = b"HOWL"  # network message magic
# Public seed (howlscan.org / DigitalOcean) — used by desktop launcher & --public
PUBLIC_SEED = "147.182.223.204:42069"
PUBLIC_SEED_HOST = "147.182.223.204"
PUBLIC_SEED_PORT = 42069

# --- Storage ---
DEFAULT_DATA_DIR = Path.home() / ".howlcoin"
CHAIN_FILE = "chain.json"
WALLET_FILE = "wallet.json"
MEMPOOL_FILE = "mempool.json"
PEER_FILE = "peers.json"
