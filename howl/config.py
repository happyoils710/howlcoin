"""Chain parameters — early-Doge energy, Scrypt PoW."""

from pathlib import Path

# --- Identity ---
COIN_NAME = "Howlcoin"
TICKER = "HOWL"
VERSION = "0.5.0"
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

# Starting difficulty: leading zero-nibbles in the scrypt hex digest
# 4 = casual CPU-mineable on a laptop; raise later as hashrate grows
INITIAL_DIFFICULTY = 4

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

# --- Storage ---
DEFAULT_DATA_DIR = Path.home() / ".howlcoin"
CHAIN_FILE = "chain.json"
WALLET_FILE = "wallet.json"
MEMPOOL_FILE = "mempool.json"
PEER_FILE = "peers.json"
