#!/usr/bin/env python3
"""
Howl autonomous multi-agent runtime.

Monitors Howlchain (health, security, oracle, opportunities), reaches multi-agent
consensus, settles results on-chain, and can bootstrap full nodes via local/DePIN.

Examples:
  # One tick (dry-run infra, no on-chain settle)
  python3 scripts/howl-agent-runtime.py --once

  # Continuous loop against public explorer
  python3 scripts/howl-agent-runtime.py --api https://howlscan.org --interval 60

  # Settle high+ findings on-chain (needs funded wallet, 1 HOWL fee/tx)
  HOWL_AGENTS_SETTLE=1 HOWL_AGENTS_WALLET=/path/wallet.json \\
    python3 scripts/howl-agent-runtime.py --settle --once

  # Actually spawn local nodes (not dry-run)
  python3 scripts/howl-agent-runtime.py --live-infra --once
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from howl.agents.runtime import AgentRuntime  # noqa: E402


def main() -> None:
    p = argparse.ArgumentParser(description="Howl multi-agent autonomous runtime")
    p.add_argument(
        "--api",
        default=os.environ.get("HOWL_AGENTS_API")
        or os.environ.get("HOWL_EXPLORER_URL")
        or "https://howlscan.org",
        help="Explorer / node HTTP API base",
    )
    p.add_argument(
        "--state-dir",
        default=os.environ.get("HOWL_AGENTS_STATE")
        or str(Path.home() / ".howlcoin" / "agents"),
        help="Agent state, treasury, fleet, history",
    )
    p.add_argument(
        "--wallet",
        default=os.environ.get("HOWL_AGENTS_WALLET")
        or os.environ.get("HOWL_BRIDGE_HOT_WALLET")
        or "",
        help="Wallet JSON for on-chain oracle settlement",
    )
    p.add_argument(
        "--data-dir",
        default=os.environ.get("HOWL_AGENTS_DATA_DIR")
        or os.environ.get("HOWL_PUBLIC_DATA")
        or "",
        help="Optional local chain data dir for broadcast fallback",
    )
    p.add_argument(
        "--seed",
        default=os.environ.get("HOWL_AGENTS_SEED") or "147.182.223.204:42069",
        help="P2P seed for bootstrapped nodes",
    )
    p.add_argument("--interval", type=float, default=float(os.environ.get("HOWL_AGENTS_INTERVAL") or 60))
    p.add_argument("--quorum", type=int, default=int(os.environ.get("HOWL_AGENTS_QUORUM") or 2))
    p.add_argument(
        "--settle",
        action="store_true",
        help="Post consensus to chain as oracle txs (requires --wallet)",
    )
    p.add_argument(
        "--live-infra",
        action="store_true",
        help="Actually start local nodes / mark DePIN jobs live (default: dry-run manifests only)",
    )
    p.add_argument(
        "--settle-severity",
        default=os.environ.get("HOWL_AGENTS_SETTLE_SEVERITY") or "high",
        help="Minimum severity to settle on-chain (default high)",
    )
    p.add_argument("--once", action="store_true", help="Run a single tick and exit")
    p.add_argument("--status", action="store_true", help="Print status JSON and exit")
    p.add_argument(
        "--budget",
        type=float,
        default=None,
        help="Set treasury soft budget in HOWL",
    )
    args = p.parse_args()

    rt = AgentRuntime(
        api_base=args.api,
        state_dir=Path(args.state_dir),
        wallet_path=Path(args.wallet) if args.wallet else None,
        data_dir=Path(args.data_dir) if args.data_dir else None,
        seed=args.seed,
        settle=bool(args.settle),
        dry_run_infra=not args.live_infra,
        required_votes=args.quorum,
        settle_min_severity=args.settle_severity,
        interval=args.interval,
        howl_root=ROOT,
    )
    if args.budget is not None:
        rt.treasury.budget_howl = float(args.budget)
        from howl.agents.economy import save_treasury

        save_treasury(rt.state_dir / "treasury.json", rt.treasury)

    if args.status:
        print(json.dumps(rt.status(), indent=2))
        return

    if args.once:
        out = rt.tick()
        print(json.dumps(out, indent=2, default=str))
        return

    rt.run_forever()


if __name__ == "__main__":
    main()
