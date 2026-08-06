#!/usr/bin/env python3
"""
Phase 1 agent tx test — HOWL ping-pong between two wallets.

Default is DRY-RUN (sign/build only, no broadcast).
Live mode requires --live and HOWL_AGENTS_TRADE=1.

Examples:
  # Create two test wallets (once)
  python3 -m howl --data-dir ~/.howlcoin/agent-a init
  python3 -m howl --data-dir ~/.howlcoin/agent-b init

  # Dry-run one cycle
  python3 scripts/howl-agent-tx-test.py \\
    --wallet-a ~/.howlcoin/agent-a/wallet.json \\
    --wallet-b ~/.howlcoin/agent-b/wallet.json \\
    --amount 2 --cycles 1

  # Live (env gate + flag)
  HOWL_AGENTS_TRADE=1 python3 scripts/howl-agent-tx-test.py \\
    --wallet-a ~/.howlcoin/agent-a/wallet.json \\
    --wallet-b ~/.howlcoin/agent-b/wallet.json \\
    --amount 2 --cycles 1 --live --api https://howlscan.org
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from howl.agents.tradetest import TradeTestConfig, fund_hint, run_ping_pong  # noqa: E402
from howl.wallet import Wallet  # noqa: E402


def env_truthy(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def main() -> None:
    p = argparse.ArgumentParser(description="Phase 1 HOWL ping-pong tx test for agents")
    p.add_argument("--api", default=os.environ.get("HOWL_AGENTS_API") or "https://howlscan.org")
    p.add_argument("--wallet-a", required=True, help="Path to wallet A JSON")
    p.add_argument("--wallet-b", required=True, help="Path to wallet B JSON")
    p.add_argument("--amount", type=float, default=2.0, help="HOWL amount each leg (default 2)")
    p.add_argument("--fee", type=float, default=1.0, help="Fee HOWL per tx (min 1)")
    p.add_argument("--cycles", type=int, default=1, help="A→B→A cycles (max 50)")
    p.add_argument("--wait", type=float, default=180.0, help="Seconds to wait for confirm per leg")
    p.add_argument("--poll", type=float, default=8.0, help="Poll interval while waiting")
    p.add_argument(
        "--live",
        action="store_true",
        help="Broadcast txs (requires HOWL_AGENTS_TRADE=1)",
    )
    p.add_argument(
        "--data-dir",
        default="",
        help="Optional local chain data dir for broadcast fallback",
    )
    p.add_argument(
        "--state",
        default="",
        help="Write JSON report path (default: ~/.howlcoin/agents/tx-test-last.json)",
    )
    p.add_argument("--show-addresses", action="store_true", help="Print A/B addresses and exit")
    args = p.parse_args()

    wa = Path(args.wallet_a).expanduser()
    wb = Path(args.wallet_b).expanduser()
    if args.show_addresses:
        a = Wallet(wa, create_if_missing=False)
        b = Wallet(wb, create_if_missing=False)
        print(json.dumps({"a": a.primary.address, "b": b.primary.address}, indent=2))
        print(fund_hint(a.primary.address, b.primary.address, args.amount, args.fee))
        return

    dry = not args.live
    if args.live and not env_truthy("HOWL_AGENTS_TRADE"):
        print(
            "Refusing --live without HOWL_AGENTS_TRADE=1\n"
            "  export HOWL_AGENTS_TRADE=1\n"
            "  then re-run with --live",
            file=sys.stderr,
        )
        sys.exit(2)

    state = Path(args.state).expanduser() if args.state else (
        Path.home() / ".howlcoin" / "agents" / "tx-test-last.json"
    )
    cfg = TradeTestConfig(
        api_base=args.api.rstrip("/"),
        wallet_a=wa,
        wallet_b=wb,
        amount_howl=float(args.amount),
        fee_howl=float(args.fee),
        max_cycles=int(args.cycles),
        wait_confirm_sec=float(args.wait),
        poll_sec=float(args.poll),
        dry_run=dry,
        data_dir=Path(args.data_dir).expanduser() if args.data_dir else None,
        state_path=state,
    )

    # Preflight addresses
    a = Wallet(wa, create_if_missing=False)
    b = Wallet(wb, create_if_missing=False)
    print(fund_hint(a.primary.address, b.primary.address, cfg.amount_howl, cfg.fee_howl))
    print(f"mode: {'LIVE' if args.live else 'DRY-RUN'}  api: {cfg.api_base}")

    report = run_ping_pong(cfg)
    print(json.dumps(report, indent=2, default=str))
    if not report.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
