#!/usr/bin/env python3
"""
Howl Swap bridge bootstrap — create HOWL hot wallet + Solana treasury,
write env drop-ins, print fund instructions.

Usage (as root on VPS):
  python3 scripts/howl-bridge-bootstrap.py
  python3 scripts/howl-bridge-bootstrap.py --sol-treasury <base58>
  python3 scripts/howl-bridge-bootstrap.py --howl-per-usdc 1 --howl-per-sol 100000
  python3 scripts/howl-bridge-bootstrap.py --dry-run

Does not enable the bridge alone — pair with install-howl-bridge.sh (systemd).
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _chmod_secret(path: Path) -> None:
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
    except OSError:
        pass


def ensure_howl_hot_wallet(path: Path, force: bool = False) -> dict:
    from howl.wallet import Wallet

    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if path.exists() and not force:
        w = Wallet(path, create_if_missing=False)
    else:
        if path.exists() and force:
            bak = path.with_suffix(path.suffix + ".bak")
            path.replace(bak)
            print(f"  backed up old hot wallet → {bak}")
        w = Wallet(path, create_if_missing=True)
        if not path.exists() or force:
            # Wallet may have auto-created; ensure label
            w.label = "howl-bridge-hot"
            w.save()
        created = True
        _chmod_secret(path)
    return {
        "path": str(path),
        "address": w.address,
        "created": created,
        "has_mnemonic": bool(w.mnemonic),
    }


def generate_solana_treasury(keypair_path: Path, force: bool = False) -> dict:
    """
    Generate Ed25519 Solana keypair (JSON array of 64 bytes) + base58 address.
    Uses PyCryptodome when available (VPS venv).
    """
    keypair_path = keypair_path.expanduser()
    addr_path = keypair_path.with_suffix(".address")
    keypair_path.parent.mkdir(parents=True, exist_ok=True)

    if keypair_path.exists() and not force:
        try:
            raw = json.loads(keypair_path.read_text())
            if isinstance(raw, list) and len(raw) == 64:
                pub = bytes(raw[32:64])
                import base58

                addr = base58.b58encode(pub).decode()
                if not addr_path.exists():
                    addr_path.write_text(addr + "\n")
                return {
                    "path": str(keypair_path),
                    "address": addr,
                    "address_file": str(addr_path),
                    "created": False,
                }
        except Exception:
            pass

    try:
        from Crypto.PublicKey import ECC
        import base58
    except ImportError as e:
        raise SystemExit(
            "Need PyCryptodome + base58 to generate Solana keys "
            f"(pip install pycryptodome base58). Or pass --sol-treasury. ({e})"
        ) from e

    if keypair_path.exists() and force:
        bak = keypair_path.with_suffix(keypair_path.suffix + ".bak")
        keypair_path.replace(bak)
        print(f"  backed up old Solana keypair → {bak}")

    k = ECC.generate(curve="Ed25519")
    seed = bytes.fromhex(k.seed) if isinstance(k.seed, str) else bytes(k.seed)
    if len(seed) != 32:
        raise RuntimeError(f"unexpected Ed25519 seed length {len(seed)}")
    pub = k.public_key().export_key(format="raw")
    if len(pub) != 32:
        raise RuntimeError(f"unexpected Ed25519 pubkey length {len(pub)}")
    arr = list(seed + pub)
    keypair_path.write_text(json.dumps(arr))
    _chmod_secret(keypair_path)
    addr = base58.b58encode(pub).decode()
    addr_path.write_text(addr + "\n")
    _chmod_secret(addr_path)
    return {
        "path": str(keypair_path),
        "address": addr,
        "address_file": str(addr_path),
        "created": True,
    }


def write_env_file(
    path: Path,
    *,
    data_dir: Path,
    sol_treasury: str,
    hot_wallet: Path,
    howl_per_sol: float,
    howl_per_usdc: float,
    fee_bps: int,
    min_sol: float,
    max_sol: float,
    admin_secret: str,
    node_rpc: str,
    solana_rpc: str,
    enabled: bool,
) -> Path:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"HOWL_BRIDGE_ENABLED={'1' if enabled else '0'}",
        f"HOWL_BRIDGE_SOL_TREASURY={sol_treasury}",
        f"HOWL_BRIDGE_USDC_TREASURY={sol_treasury}",
        f"HOWL_BRIDGE_HOWL_PER_SOL={howl_per_sol:g}",
        f"HOWL_BRIDGE_HOWL_PER_USDC={howl_per_usdc:g}",
        f"HOWL_BRIDGE_FEE_BPS={fee_bps}",
        f"HOWL_BRIDGE_MIN_SOL={min_sol:g}",
        f"HOWL_BRIDGE_MAX_SOL={max_sol:g}",
        f"HOWL_BRIDGE_DATA={data_dir}",
        f"HOWL_PUBLIC_DATA={data_dir}",
        f"HOWL_BRIDGE_HOT_WALLET={hot_wallet}",
        f"HOWL_BRIDGE_ADMIN_SECRET={admin_secret}",
        f"HOWL_NODE_RPC={node_rpc}",
        f"SOLANA_RPC={solana_rpc}",
        "",
    ]
    path.write_text("\n".join(lines))
    _chmod_secret(path)
    return path


def write_systemd_dropin(path: Path, env_file: Path) -> Path:
    """
    EnvironmentFile drop-in for howlcoin-explorer / howl-bridge-relayer.
    """
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "[Service]\n"
        f"EnvironmentFile=-{env_file}\n"
    )
    return path


def write_status_json(path: Path, payload: dict) -> None:
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Howl Swap bridge bootstrap")
    ap.add_argument(
        "--data-dir",
        default=os.environ.get("HOWL_PUBLIC_DATA")
        or os.environ.get("HOWL_BRIDGE_DATA")
        or "/var/lib/howlcoin",
    )
    ap.add_argument(
        "--hot-wallet",
        default="",
        help="Path for HOWL hot wallet.json (default: DATA/bridge-hot-wallet.json)",
    )
    ap.add_argument(
        "--sol-keypair",
        default="",
        help="Path for Solana treasury keypair JSON (default: DATA/bridge-sol-treasury.json)",
    )
    ap.add_argument(
        "--sol-treasury",
        default=os.environ.get("HOWL_BRIDGE_SOL_TREASURY", ""),
        help="Existing Solana treasury address (skip keygen)",
    )
    ap.add_argument("--howl-per-sol", type=float, default=100_000.0)
    ap.add_argument(
        "--howl-per-usdc",
        type=float,
        default=float(os.environ.get("HOWL_BRIDGE_HOWL_PER_USDC", "10")),
        help="HOWL per 1 USDC (1 = $1/HOWL, 10 = $0.10/HOWL)",
    )
    ap.add_argument("--fee-bps", type=int, default=100)
    ap.add_argument("--min-sol", type=float, default=0.01)
    ap.add_argument("--max-sol", type=float, default=10.0)
    ap.add_argument(
        "--node-rpc",
        default=os.environ.get("HOWL_NODE_RPC", "http://127.0.0.1:42070"),
    )
    ap.add_argument(
        "--solana-rpc",
        default=os.environ.get("SOLANA_RPC", "https://api.mainnet-beta.solana.com"),
    )
    ap.add_argument(
        "--admin-secret",
        default=os.environ.get("HOWL_BRIDGE_ADMIN_SECRET", ""),
        help="Admin secret (generated if empty)",
    )
    ap.add_argument(
        "--disable",
        action="store_true",
        help="Write env with HOWL_BRIDGE_ENABLED=0",
    )
    ap.add_argument("--force", action="store_true", help="Recreate wallets if present")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Print plan only; do not write secrets",
    )
    ap.add_argument(
        "--systemd-dropin-dir",
        default="/etc/systemd/system",
        help="Where to write unit drop-ins (empty to skip)",
    )
    args = ap.parse_args()

    data = Path(args.data_dir).expanduser()
    hot = Path(args.hot_wallet or (data / "bridge-hot-wallet.json"))
    sol_kp = Path(args.sol_keypair or (data / "bridge-sol-treasury.json"))
    env_file = data / "bridge.env"
    status_file = data / "bridge-bootstrap.json"
    enabled = not args.disable
    admin = args.admin_secret or secrets.token_hex(24)

    print("== Howl Swap bridge bootstrap ==")
    print(f"  data_dir     {data}")
    print(f"  hot_wallet   {hot}")
    print(f"  sol_keypair  {sol_kp}")
    print(f"  rates        {args.howl_per_sol:g} HOWL/SOL · {args.howl_per_usdc:g} HOWL/USDC")
    print(
        f"  index USD    ~${1.0 / args.howl_per_usdc:.4g}/HOWL"
        if args.howl_per_usdc
        else "  index USD    —"
    )
    print(f"  enabled      {enabled}")
    if args.dry_run:
        print("  (dry-run — no files written)")
        return 0

    data.mkdir(parents=True, exist_ok=True)

    # 1) HOWL hot wallet
    print("-- HOWL hot wallet --")
    howl = ensure_howl_hot_wallet(hot, force=args.force)
    print(f"  {'created' if howl['created'] else 'exists'}  {howl['address']}")
    print(f"  file  {howl['path']}")

    # 2) Solana treasury
    print("-- Solana treasury --")
    sol_addr = (args.sol_treasury or "").strip()
    sol_meta: dict
    if sol_addr:
        sol_meta = {
            "path": None,
            "address": sol_addr,
            "address_file": None,
            "created": False,
            "external": True,
        }
        print(f"  using provided address  {sol_addr}")
    else:
        sol_meta = generate_solana_treasury(sol_kp, force=args.force)
        sol_addr = sol_meta["address"]
        print(f"  {'created' if sol_meta['created'] else 'exists'}  {sol_addr}")
        print(f"  keypair  {sol_meta['path']}  (chmod 600 — KEEP SECRET)")
        print(f"  address  {sol_meta.get('address_file')}")

    # 3) env file
    print("-- env file --")
    write_env_file(
        env_file,
        data_dir=data,
        sol_treasury=sol_addr,
        hot_wallet=hot,
        howl_per_sol=args.howl_per_sol,
        howl_per_usdc=args.howl_per_usdc,
        fee_bps=args.fee_bps,
        min_sol=args.min_sol,
        max_sol=args.max_sol,
        admin_secret=admin,
        node_rpc=args.node_rpc,
        solana_rpc=args.solana_rpc,
        enabled=enabled,
    )
    print(f"  wrote {env_file}")

    # 4) systemd drop-ins (best-effort)
    dropin_dir = (args.systemd_dropin_dir or "").strip()
    if dropin_dir and os.geteuid() == 0:
        for unit in ("howlcoin-explorer", "howl-bridge-relayer"):
            d = Path(dropin_dir) / f"{unit}.service.d"
            write_systemd_dropin(d / "bridge.conf", env_file)
            print(f"  drop-in {d / 'bridge.conf'}")
    elif dropin_dir:
        print("  skip systemd drop-ins (not root) — install-howl-bridge.sh as root")

    # 5) status summary
    usd_per = (1.0 / args.howl_per_usdc) if args.howl_per_usdc else None
    payload = {
        "ok": True,
        "enabled": enabled,
        "data_dir": str(data),
        "env_file": str(env_file),
        "howl_hot_wallet": howl,
        "sol_treasury": sol_meta,
        "rates": {
            "howl_per_sol": args.howl_per_sol,
            "howl_per_usdc": args.howl_per_usdc,
            "usd_per_howl": usd_per,
            "fee_bps": args.fee_bps,
            "min_sol": args.min_sol,
            "max_sol": args.max_sol,
        },
        "next_steps": [
            f"Fund HOWL hot wallet {howl['address']} with native HOWL (inventory + fees)",
            f"Users will send SOL/USDC to Solana treasury {sol_addr}",
            "Run: bash scripts/install-howl-bridge.sh",
            "Verify: curl -sS https://howlscan.org/api/public/bridge | python3 -m json.tool",
            "Test with min 0.01 SOL after relayer is running",
        ],
    }
    write_status_json(status_file, payload)

    print()
    print("== Bootstrap files ready ==")
    print(f"  HOWL hot address : {howl['address']}")
    print(f"  SOL treasury     : {sol_addr}")
    print(f"  Env              : {env_file}")
    print(f"  Status JSON      : {status_file}")
    print()
    print("NEXT (required before swaps work):")
    print(f"  1) Fund HOWL hot wallet with inventory:")
    print(f"       {howl['address']}")
    print(f"     e.g. enough for N min swaps (~990 HOWL each at default rates)")
    print(f"  2) Install + start services (as root):")
    print(f"       bash {ROOT}/scripts/install-howl-bridge.sh")
    print(f"  3) Confirm live:")
    print(f"       curl -sS https://howlscan.org/api/public/bridge | python3 -m json.tool")
    print()
    print("SECURITY:")
    print("  · bridge-hot-wallet.json holds BIP39 — back up offline, never commit")
    print("  · bridge-sol-treasury.json is the Solana private key — back up offline")
    print("  · Admin secret is in bridge.env — treat as root secret")
    print("  · Start with low HOWL_BRIDGE_MAX_SOL until proven")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
