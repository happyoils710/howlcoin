#!/usr/bin/env python3
"""Howlcoin CLI — wallet, mine, send, status."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .blockchain import Blockchain
from .config import (
    COIN_NAME,
    DEFAULT_DATA_DIR,
    DEFAULT_P2P_PORT,
    DEFAULT_RPC_PORT,
    TICKER,
    WALLET_FILE,
    block_subsidy_howl,
)
from .dashboard import Dashboard
from .network import Node
from .scrypt_pow import estimate_hashrate
from .bip39util import path_string, validate_mnemonic
from .wallet import Wallet, format_howl, parse_howl


BANNER = r"""
  _   _                 _
 | | | | ___   __      | | ___ ___  _ __
 | |_| |/ _ \ / _ \ /\ | |/ __/ _ \| '_ \
 |  _  | (_) | (_|  V  | | (_| (_) | | | |
 |_| |_|\___/ \__,_|_|_|_|\___\___/|_| |_|
        Scrypt meme coin · ticker HOWL
"""


def data_dir(args) -> Path:
    p = Path(args.data_dir).expanduser() if getattr(args, "data_dir", None) else DEFAULT_DATA_DIR
    p.mkdir(parents=True, exist_ok=True)
    return p


def cmd_init(args: argparse.Namespace) -> None:
    print(BANNER)
    dd = data_dir(args)
    chain = Blockchain(dd)
    wallet_path = dd / WALLET_FILE
    if wallet_path.exists() and not args.force:
        wallet = Wallet(wallet_path)
        print(f"Data dir : {dd}")
        print(f"Address  : {wallet.address}")
        print(f"Height   : {chain.height()}")
        print("Wallet already exists (use --force to replace with a NEW mnemonic wallet).")
        if wallet.has_mnemonic:
            print("Mnemonic : present (python3 -m howl mnemonic)")
        else:
            print("Mnemonic : none (legacy hex key — cannot invent a phrase for it)")
        return

    if wallet_path.exists() and args.force:
        old = Wallet(wallet_path)
        backup = old.backup_file()
        print(f"Backed up old wallet → {backup}")
        wallet_path.unlink()

    wallet = Wallet(wallet_path)  # creates BIP39 wallet
    print(f"Data dir : {dd}")
    print(f"Address  : {wallet.address}")
    print(f"Path     : {wallet.derivation}")
    print(f"Height   : {chain.height()}")
    print(f"Tip      : {chain.tip()['hash'][:24]}…")
    print()
    if wallet.has_mnemonic:
        print("═══ BIP39 RECOVERY PHRASE (write this down OFFLINE) ═══")
        print(wallet.mnemonic)
        print("══════════════════════════════════════════════════════")
        print("12 words restore this wallet. Never share them.")
    print()
    print("Next: mine with  python3 -m howl mine")
    print("⚠ wallet.json holds your mnemonic + private key.")


def cmd_wallet(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    wallet = Wallet(dd / WALLET_FILE)
    chain = Blockchain(dd)
    print(f"Address  : {wallet.address}")
    print(f"Balance  : {format_howl(chain.balance(wallet.address))}")
    print(f"Nonce    : {chain.next_nonce(wallet.address)}")
    print(f"Mnemonic : {'yes (BIP39)' if wallet.has_mnemonic else 'no (legacy private-key only)'}")
    if wallet.has_mnemonic:
        print(f"Path     : {wallet.derivation}")
        print(f"Addresses: {len(wallet.keys)} (next index {wallet.next_index})")
    if args.show_keys:
        print(f"PubKey   : {wallet.primary.public_key_hex}")
        print(f"PrivKey  : {wallet.primary.private_key_hex}")
        print("⚠ Never share your private key.")
    if args.show_mnemonic:
        if wallet.has_mnemonic:
            print()
            print("═══ BIP39 MNEMONIC ═══")
            print(wallet.mnemonic)
            print("══════════════════════")
            print("⚠ Anyone with these words can steal your HOWL.")
        else:
            print()
            print("No mnemonic on this wallet (created before BIP39 support).")
            print("Your recovery secret is the private key:")
            print(f"  {wallet.primary.private_key_hex}")
            print("To create a NEW mnemonic wallet (new address):")
            print("  python3 -m howl init --force")
            print("  (backs up the old wallet first; HOWL on old address stays there)")


def cmd_mnemonic(args: argparse.Namespace) -> None:
    """Show recovery phrase, or explain legacy wallets."""
    dd = data_dir(args)
    wallet = Wallet(dd / WALLET_FILE)
    if not wallet.has_mnemonic:
        print("This wallet has no BIP39 mnemonic (legacy hex key).")
        print(f"Address     : {wallet.address}")
        print(f"Private key : {wallet.primary.private_key_hex}")
        print()
        print("A mnemonic cannot be computed from an existing private key.")
        print("Options:")
        print("  • Keep using / backing up the private key above")
        print("  • python3 -m howl init --force   → new 12-word wallet (new address)")
        print("  • python3 -m howl restore \"word1 word2 ... word12\"")
        return
    print(f"Address : {wallet.address}")
    print(f"Path    : {path_string(0)}")
    print()
    print("═══ BIP39 RECOVERY PHRASE ═══")
    print(wallet.mnemonic)
    print("════════════════════════════")
    print("Store offline. Never screenshot / chat / email these words.")


def cmd_restore(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    phrase = " ".join(args.words) if getattr(args, "words", None) else (args.mnemonic or "")
    if not phrase.strip():
        print('Usage: python3 -m howl restore word1 word2 ... word12', file=sys.stderr)
        sys.exit(2)
    if not validate_mnemonic(phrase):
        print("Invalid BIP39 mnemonic (check words / spelling).", file=sys.stderr)
        sys.exit(1)
    wallet_path = dd / WALLET_FILE
    if wallet_path.exists():
        wallet = Wallet(wallet_path)
        backup = wallet.backup_file()
        print(f"Backed up previous wallet → {backup}")
        wallet.restore_from_mnemonic(phrase, passphrase=args.passphrase or "")
    else:
        wallet = Wallet(wallet_path)  # creates ephemeral BIP39
        wallet.restore_from_mnemonic(phrase, passphrase=args.passphrase or "")
    print(BANNER)
    print("Wallet restored from mnemonic.")
    print(f"Address : {wallet.address}")
    print(f"Path    : {wallet.derivation}")
    print("Verify with: python3 -m howl wallet")


def cmd_newaddress(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    wallet = Wallet(dd / WALLET_FILE)
    kp = wallet.new_address()
    print(f"New address: {kp.address}")
    if wallet.has_mnemonic:
        print(f"Derived from mnemonic index {wallet.next_index - 1}")


def cmd_status(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    chain = Blockchain(dd)
    s = chain.summary()
    print(BANNER)
    for k, v in s.items():
        print(f"  {k:22} {v}")
    print()
    print("Subsidy schedule (HOWL per block):")
    for h in (1, 999, 1000, 10000, 50000, 200000, 500000):
        print(f"  height {h:>7} → {block_subsidy_howl(h):>10,} HOWL")


def cmd_mine(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    chain = Blockchain(dd)
    wallet = Wallet(dd / WALLET_FILE)
    address = args.address or wallet.address
    continuous = getattr(args, "continuous", False)
    count = max(1, args.blocks)
    print(BANNER)
    print(f"Miner address: {address}")
    if continuous:
        print("Mining continuously with Scrypt (Ctrl+C to stop)…\n")
        mined = 0
        try:
            while True:
                chain.mine_one(address)
                mined += 1
                print(
                    f"  bag: {format_howl(chain.balance(address))} | "
                    f"height: {chain.height()} | blocks this run: {mined}\n"
                )
        except KeyboardInterrupt:
            print(f"\nStopped after {mined} block(s).")
            print(f"Balance: {format_howl(chain.balance(address))}")
            print(f"Height : {chain.height()}")
        return
    print(f"Mining {count} block(s) with Scrypt…\n")
    for i in range(count):
        chain.mine_one(address)
    print(f"\nBalance: {format_howl(chain.balance(address))}")
    print(f"Height : {chain.height()}")


def cmd_send(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    chain = Blockchain(dd)
    wallet = Wallet(dd / WALLET_FILE)
    amount = parse_howl(args.amount)
    fee = parse_howl(args.fee) if args.fee else 0
    nonce = chain.next_nonce(wallet.address)
    tx = wallet.build_tx(
        to=args.to,
        amount_howlies=amount,
        nonce=nonce,
        fee=fee,
        memo=args.memo or "",
    )
    ok, msg = chain.add_to_mempool(tx)
    if not ok:
        print(f"Rejected: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"Queued tx {msg}")
    print(f"  from   {tx['from']}")
    print(f"  to     {tx['to']}")
    print(f"  amount {format_howl(amount)}")
    print(f"  fee    {format_howl(fee)}")
    print("Mine a block to confirm it:  python3 -m howl mine")


def cmd_balance(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    chain = Blockchain(dd)
    addr = args.address
    if not addr:
        wallet = Wallet(dd / WALLET_FILE)
        addr = wallet.address
    print(f"{addr}")
    print(format_howl(chain.balance(addr)))


def cmd_export(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    chain = Blockchain(dd)
    out = {
        "summary": chain.summary(),
        "tip_block": chain.tip(),
    }
    print(json.dumps(out, indent=2))


def cmd_bench(args: argparse.Namespace) -> None:
    print("Sampling Scrypt hashrate…")
    rate = estimate_hashrate(args.seconds)
    print(f"~{rate:.2f} H/s on this machine (Scrypt N=1024)")
    # rough time to block at current difficulty
    dd = data_dir(args)
    if (dd / "chain.json").exists():
        chain = Blockchain(dd)
        diff = chain.next_difficulty()
        # expected hashes ~ 16^difficulty
        expected = 16 ** diff
        eta = expected / rate if rate else float("inf")
        print(f"Next difficulty: {diff} (≈{expected:.0f} hashes expected)")
        print(f"Est. time/block: {eta:.1f}s at this hashrate")


def cmd_richlist(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    chain = Blockchain(dd)
    items = sorted(chain.balances.items(), key=lambda x: -x[1])[: args.limit]
    print(f"{'Address':<40} Balance")
    print("-" * 60)
    for addr, bal in items:
        print(f"{addr:<40} {format_howl(bal)}")


def cmd_node(args: argparse.Namespace) -> None:
    """Run P2P node + web dashboard (the full local Howlcoin experience)."""
    import signal

    dd = data_dir(args)
    chain = Blockchain(dd)
    wallet = Wallet(dd / WALLET_FILE)
    seeds = list(args.connect or [])
    print(BANNER)
    print(f"Data dir : {dd}")
    print(f"Wallet   : {wallet.address}")
    print(f"Height   : {chain.height()}")
    print(f"P2P      : {args.host}:{args.port}")
    print(f"Dashboard: http://{args.rpc_host}:{args.rpc_port}/")
    if seeds:
        print(f"Seeds    : {', '.join(seeds)}")
    print("\nMine & manage peers in the dashboard. Ctrl+C to stop.\n")

    node = Node(
        chain,
        host=args.host,
        port=args.port,
        seeds=seeds,
    )
    node.start()
    dash = Dashboard(
        chain,
        wallet,
        node=node,
        host=args.rpc_host,
        port=args.rpc_port,
        p2p_port=args.port,
    )

    def _stop(*_a):
        print("\nShutting down Howlcoin node…")
        node.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)
    dash.serve_forever()


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Dashboard only (no P2P). Prefer `howl node` for full features."""
    dd = data_dir(args)
    chain = Blockchain(dd)
    wallet = Wallet(dd / WALLET_FILE)
    print(BANNER)
    dash = Dashboard(
        chain,
        wallet,
        node=None,
        host=args.rpc_host,
        port=args.rpc_port,
        p2p_port=args.port,
    )
    dash.serve_forever()


def cmd_peers(args: argparse.Namespace) -> None:
    dd = data_dir(args)
    peer_file = dd / "peers.json"
    if not peer_file.exists():
        print("No peers saved yet. Start a node and connect someone.")
        return
    print(peer_file.read_text())


def cmd_explorer(args: argparse.Namespace) -> None:
    """Multi-chain block explorer (public + telegram data dirs)."""
    from .explorer import main as explorer_main

    explorer_main(
        host=args.host,
        port=args.port,
        public_dir=args.public_data,
        telegram_dir=args.telegram_data,
    )


def cmd_telegram(args: argparse.Namespace) -> None:
    """Run the Howlcoin Telegram bot (needs HOWL_TELEGRAM_TOKEN)."""
    import os

    if args.token:
        os.environ["HOWL_TELEGRAM_TOKEN"] = args.token
    # Prefer explicit env HOWL_DATA_DIR; only override if user passed non-default --data-dir
    # or if HOWL_DATA_DIR is unset.
    if not os.environ.get("HOWL_DATA_DIR"):
        os.environ["HOWL_DATA_DIR"] = str(
            Path(args.data_dir).expanduser() if args.data_dir else Path.home() / ".howlcoin-telegram"
        )
    elif getattr(args, "data_dir", None) and str(args.data_dir) != str(DEFAULT_DATA_DIR):
        os.environ["HOWL_DATA_DIR"] = str(Path(args.data_dir).expanduser())
    if args.seed:
        os.environ["HOWL_SEED"] = args.seed
    if args.cooldown:
        os.environ["HOWL_MINE_COOLDOWN"] = str(args.cooldown)
    from .telegram_bot import main as tg_main

    tg_main()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="howl",
        description=f"{COIN_NAME} ({TICKER}) — Scrypt PoW meme coin CLI",
    )
    p.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="chain + wallet directory")
    p.add_argument("--version", action="version", version=f"Howlcoin {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="create genesis + BIP39 wallet")
    s.add_argument(
        "--force",
        action="store_true",
        help="replace existing wallet with a NEW mnemonic wallet (backs up old file)",
    )
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("wallet", help="show wallet address & balance")
    s.add_argument("--show-keys", action="store_true", help="print private key (dangerous)")
    s.add_argument(
        "--show-mnemonic",
        action="store_true",
        help="print BIP39 recovery phrase if present (dangerous)",
    )
    s.set_defaults(func=cmd_wallet)

    s = sub.add_parser("mnemonic", help="show BIP39 recovery phrase (or legacy key info)")
    s.set_defaults(func=cmd_mnemonic)

    s = sub.add_parser("restore", help="restore wallet from BIP39 words")
    s.add_argument("words", nargs="*", help="12 or 24 BIP39 words")
    s.add_argument("--mnemonic", default="", help="phrase as a single quoted string")
    s.add_argument("--passphrase", default="", help="optional BIP39 passphrase")
    s.set_defaults(func=cmd_restore)

    s = sub.add_parser("newaddress", help="generate extra receive address")
    s.set_defaults(func=cmd_newaddress)

    s = sub.add_parser("status", help="chain summary")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("mine", help="mine blocks with Scrypt (earn HOWL)")
    s.add_argument("-n", "--blocks", type=int, default=1, help="blocks to mine")
    s.add_argument(
        "-c",
        "--continuous",
        action="store_true",
        help="mine forever until Ctrl+C",
    )
    s.add_argument("--address", help="miner payout address (default: wallet)")
    s.set_defaults(func=cmd_mine)

    s = sub.add_parser("send", help="queue a transfer (confirm by mining)")
    s.add_argument("to", help="destination HOWL address")
    s.add_argument("amount", help="amount in HOWL, e.g. 100 or 12.5")
    s.add_argument("--fee", default="0", help="fee in HOWL")
    s.add_argument("--memo", default="", help="optional memo")
    s.set_defaults(func=cmd_send)

    s = sub.add_parser("balance", help="show balance for an address")
    s.add_argument("address", nargs="?", help="address (default: wallet)")
    s.set_defaults(func=cmd_balance)

    s = sub.add_parser("export", help="dump tip + summary as JSON")
    s.set_defaults(func=cmd_export)

    s = sub.add_parser("bench", help="benchmark Scrypt hashrate")
    s.add_argument("--seconds", type=float, default=3.0)
    s.set_defaults(func=cmd_bench)

    s = sub.add_parser("richlist", help="top balances")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_richlist)

    s = sub.add_parser("node", help="run P2P node + web dashboard")
    s.add_argument("--host", default="0.0.0.0", help="P2P bind host")
    s.add_argument("--port", type=int, default=DEFAULT_P2P_PORT, help="P2P port")
    s.add_argument("--rpc-host", default="127.0.0.1", help="dashboard bind host")
    s.add_argument("--rpc-port", type=int, default=DEFAULT_RPC_PORT, help="dashboard port")
    s.add_argument(
        "--connect",
        action="append",
        default=[],
        help="seed peer host:port (repeatable)",
    )
    s.set_defaults(func=cmd_node)

    s = sub.add_parser("dashboard", help="web UI only (no P2P)")
    s.add_argument("--rpc-host", default="127.0.0.1")
    s.add_argument("--rpc-port", type=int, default=DEFAULT_RPC_PORT)
    s.add_argument("--port", type=int, default=DEFAULT_P2P_PORT, help="shown P2P port label")
    s.set_defaults(func=cmd_dashboard)

    s = sub.add_parser("peers", help="show saved peer list")
    s.set_defaults(func=cmd_peers)

    s = sub.add_parser("telegram", help="run Telegram bot (wallet + mine + status)")
    s.add_argument("--token", help="BotFather token (or set HOWL_TELEGRAM_TOKEN)")
    s.add_argument(
        "--seed",
        default="147.182.223.204:42069",
        help="public seed string shown to users",
    )
    s.add_argument(
        "--cooldown",
        type=int,
        default=120,
        help="seconds between /mine per user (default 120)",
    )
    s.set_defaults(func=cmd_telegram)

    s = sub.add_parser("explorer", help="block explorer for public + telegram chains")
    s.add_argument("--host", default="127.0.0.1", help="bind host (0.0.0.0 for LAN/public)")
    s.add_argument("--port", type=int, default=42080, help="port (default 42080)")
    s.add_argument(
        "--public-data",
        default=None,
        help="public chain data dir (default ~/.howlcoin or HOWL_PUBLIC_DATA)",
    )
    s.add_argument(
        "--telegram-data",
        default=None,
        help="telegram bot chain data dir (default ~/.howlcoin-telegram)",
    )
    s.set_defaults(func=cmd_explorer)

    return p


def main(argv=None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
