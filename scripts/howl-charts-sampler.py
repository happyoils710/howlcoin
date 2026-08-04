#!/usr/bin/env python3
"""
Howl Charts 24/7 sampler — on-chain spots → Howlscan sample history.

Run once (systemd oneshot / timer):
  HOWL_PUBLIC_DATA=/var/lib/howlcoin python3 scripts/howl-charts-sampler.py

Loop (optional):
  python3 scripts/howl-charts-sampler.py --loop --interval 300
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

# Allow `python3 scripts/howl-charts-sampler.py` from repo root or /opt/howlcoin
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser(description="Howl Charts on-chain price sampler")
    ap.add_argument(
        "--loop",
        action="store_true",
        help="Keep sampling (prefer systemd timer + oneshot instead)",
    )
    ap.add_argument(
        "--interval",
        type=int,
        default=int(os.environ.get("HOWL_CHARTS_SAMPLE_INTERVAL", "60")),
        help="Seconds between samples in --loop mode (default 60)",
    )
    ap.add_argument(
        "--min-gap",
        type=int,
        default=int(os.environ.get("HOWL_CHARTS_MIN_GAP", "60")),
        help="Minimum seconds between stored samples per asset (default 60)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Always write a sample even if min-gap not elapsed",
    )
    ap.add_argument("--json", action="store_true", help="Print full JSON result")
    args = ap.parse_args()

    # Default data dir on public seed / explorer hosts
    if not os.environ.get("HOWL_PUBLIC_DATA") and not os.environ.get("HOWL_DATA_DIR"):
        if Path("/var/lib/howlcoin").is_dir():
            os.environ["HOWL_PUBLIC_DATA"] = "/var/lib/howlcoin"

    from howl.explorer import sample_howl_charts

    def once() -> dict:
        return sample_howl_charts(force=args.force, min_gap=args.min_gap)

    if not args.loop:
        result = once()
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(
                f"Howl Charts sampler · recorded {result.get('recorded')}/{result.get('sampled')}"
                f" · path={result.get('path')}"
            )
            if result.get("errors"):
                for e in result["errors"][:8]:
                    print(f"  warn: {e}", file=sys.stderr)
        return 0 if result.get("sampled") else 1

    print(
        f"Howl Charts sampler loop every {args.interval}s (min_gap={args.min_gap})",
        flush=True,
    )
    while True:
        try:
            result = once()
            print(
                f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                f"recorded {result.get('recorded')}/{result.get('sampled')} "
                f"err={len(result.get('errors') or [])}",
                flush=True,
            )
        except Exception as e:
            print(f"sampler error: {e}", file=sys.stderr, flush=True)
        time.sleep(max(30, int(args.interval)))


if __name__ == "__main__":
    raise SystemExit(main())
