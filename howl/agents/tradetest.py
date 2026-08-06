"""Phase 1 trade-test: HOWL ping-pong between two agent wallets.

Does NOT touch SOL, wrap, or mint. Env-gated. Tiny amounts only.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..config import COIN, DEFAULT_TX_FEE_HOWLIES, MIN_TX_FEE_HOWLIES
from ..wallet import Wallet, format_howl
from .settlement import broadcast_tx, next_nonce, parse_wanted_nonce


@dataclass
class TradeTestConfig:
    api_base: str = "https://howlscan.org"
    wallet_a: Path = Path()
    wallet_b: Path = Path()
    amount_howl: float = 2.0  # transfer amount (not including fee)
    fee_howl: float = 1.0
    max_cycles: int = 1
    wait_confirm_sec: float = 120.0
    poll_sec: float = 8.0
    dry_run: bool = True
    data_dir: Optional[Path] = None
    memo_prefix: str = "howl.agent.tx-test"
    state_path: Optional[Path] = None


@dataclass
class CycleResult:
    cycle: int
    a_to_b: Dict[str, Any] = field(default_factory=dict)
    b_to_a: Dict[str, Any] = field(default_factory=dict)
    ok: bool = False
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cycle": self.cycle,
            "a_to_b": self.a_to_b,
            "b_to_a": self.b_to_a,
            "ok": self.ok,
            "error": self.error,
        }


def _howlies(howl: float) -> int:
    return int(round(float(howl) * COIN))


def _http_get(api_base: str, path: str) -> dict:
    import urllib.request

    url = api_base.rstrip("/") + path
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "HowlAgents/1.0 (+https://howlscan.org; tradetest)"},
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def address_snapshot(api_base: str, address: str) -> Dict[str, Any]:
    j = _http_get(api_base, f"/api/public/address/{address}")
    bal = j.get("balance")
    if bal is None and isinstance(j.get("account"), dict):
        bal = j["account"].get("balance")
    nonce = j.get("nonce")
    if nonce is None and isinstance(j.get("account"), dict):
        nonce = j["account"].get("nonce")
    return {
        "address": address,
        "balance": int(bal or 0),
        "nonce": int(nonce or 0),
        "raw": j,
    }


def wait_nonce_advanced(
    api_base: str,
    address: str,
    prev_nonce: int,
    *,
    timeout: float,
    poll: float,
) -> bool:
    """Wait until chain nonce > prev_nonce (tx confirmed and applied)."""
    deadline = time.time() + max(5.0, float(timeout))
    while time.time() < deadline:
        try:
            snap = address_snapshot(api_base, address)
            if int(snap["nonce"]) > int(prev_nonce):
                return True
        except Exception:
            pass
        time.sleep(max(2.0, float(poll)))
    return False


def build_transfer(
    wallet: Wallet,
    *,
    to: str,
    amount_howlies: int,
    fee_howlies: int,
    nonce: int,
    memo: str = "",
) -> Dict[str, Any]:
    return wallet.build_tx(
        to=to,
        amount_howlies=int(amount_howlies),
        nonce=int(nonce),
        fee=int(fee_howlies),
        memo=memo or "",
        key=wallet.primary,
    )


# In-process reserved next-nonce per address (same process, multi-leg / multi-cycle)
_local_reserved: Dict[str, int] = {}


def send_leg(
    *,
    from_wallet: Wallet,
    to_address: str,
    amount_howlies: int,
    fee_howlies: int,
    api_base: str,
    memo: str,
    dry_run: bool,
    data_dir: Optional[Path],
    wait_confirm_sec: float,
    poll_sec: float,
) -> Dict[str, Any]:
    from_addr = from_wallet.primary.address
    try:
        snap = address_snapshot(api_base, from_addr)
    except Exception as e:
        snap = {"address": from_addr, "balance": 0, "nonce": 0, "error": str(e)}
    need = int(amount_howlies) + int(fee_howlies)
    if not dry_run and int(snap.get("balance") or 0) < need:
        raise RuntimeError(
            f"insufficient balance on {from_addr[:16]}… "
            f"have {format_howl(int(snap.get('balance') or 0))}, need {format_howl(need)}"
        )

    # Chain expected nonce (address API + mempool), then local reservation
    nonce = next_nonce(api_base, from_addr, data_dir)
    if from_addr in _local_reserved:
        nonce = max(nonce, int(_local_reserved[from_addr]))

    def _sign(n: int) -> Dict[str, Any]:
        return build_transfer(
            from_wallet,
            to=to_address,
            amount_howlies=amount_howlies,
            fee_howlies=fee_howlies,
            nonce=int(n),
            memo=memo,
        )

    tx = _sign(nonce)
    out: Dict[str, Any] = {
        "from": from_addr,
        "to": to_address,
        "amount_howlies": amount_howlies,
        "fee_howlies": fee_howlies,
        "nonce": nonce,
        "txid": tx.get("txid"),
        "dry_run": dry_run,
        "confirmed": False,
        "balance_before": int(snap.get("balance") or 0),
    }
    if dry_run:
        out["status"] = "dry_run"
        out["note"] = "not broadcast — pass --live with HOWL_AGENTS_TRADE=1"
        _local_reserved[from_addr] = int(nonce) + 1
        return out

    txid_out = ""
    last_err = ""
    for attempt in range(4):
        try:
            txid_out = broadcast_tx(api_base, tx, data_dir)
            last_err = ""
            break
        except Exception as e:
            last_err = str(e)
            wanted = parse_wanted_nonce(last_err)
            if wanted is not None:
                nonce = int(wanted)
                out["nonce"] = nonce
                out["nonce_retry"] = attempt + 1
                out["nonce_error"] = last_err
                tx = _sign(nonce)
                out["txid"] = tx.get("txid")
                continue
            if "nonce" in last_err.lower() and attempt < 3:
                # Re-fetch and try again
                nonce = next_nonce(api_base, from_addr, data_dir)
                if from_addr in _local_reserved:
                    nonce = max(nonce, int(_local_reserved[from_addr]))
                out["nonce"] = nonce
                tx = _sign(nonce)
                out["txid"] = tx.get("txid")
                continue
            raise RuntimeError(last_err) from e
    if last_err and not txid_out:
        raise RuntimeError(last_err)

    # Reserve only while we believe the nonce is taken (mempool or confirmed).
    # If the tx drops without confirming, re-sync from chain so we don't skip to want N+1.
    _local_reserved[from_addr] = int(nonce) + 1
    out["txid"] = txid_out or tx.get("txid")
    out["status"] = "broadcast"
    # Confirm: chain next-nonce advanced past the nonce we used
    ok = wait_nonce_advanced(
        api_base,
        from_addr,
        prev_nonce=int(nonce),
        timeout=wait_confirm_sec,
        poll=poll_sec,
    )
    if not ok:
        try:
            after = address_snapshot(api_base, from_addr)
            if int(after["balance"]) < int(snap.get("balance") or 0):
                ok = True
        except Exception:
            pass
    out["confirmed"] = bool(ok)
    if not ok:
        out["status"] = "pending_timeout"
        out["hint"] = (
            "Tx may still be in mempool waiting for a miner. "
            f"Do not force another send with a higher nonce until chain next-nonce > {nonce}. "
            "If mempool dropped the tx, re-run — client will re-sync nonce from the API."
        )
        # Re-sync reservation from live API+mempool (fixes "want 9" after false +1)
        try:
            _local_reserved[from_addr] = next_nonce(api_base, from_addr, data_dir)
        except Exception:
            # safest: allow reuse of same nonce on next attempt
            _local_reserved[from_addr] = int(nonce)
    else:
        out["status"] = "confirmed"
        try:
            _local_reserved[from_addr] = max(
                int(nonce) + 1,
                next_nonce(api_base, from_addr, data_dir),
            )
        except Exception:
            _local_reserved[from_addr] = int(nonce) + 1
    return out


def run_ping_pong(cfg: TradeTestConfig) -> Dict[str, Any]:
    """
    Run up to max_cycles of A→B then B→A transfers.
    dry_run=True builds signed intents but does not broadcast.
    """
    global _local_reserved
    _local_reserved = {}

    if not cfg.wallet_a or not Path(cfg.wallet_a).is_file():
        raise FileNotFoundError(f"wallet A not found: {cfg.wallet_a}")
    if not cfg.wallet_b or not Path(cfg.wallet_b).is_file():
        raise FileNotFoundError(f"wallet B not found: {cfg.wallet_b}")

    amount = _howlies(cfg.amount_howl)
    fee = _howlies(cfg.fee_howl)
    if fee < MIN_TX_FEE_HOWLIES:
        fee = int(MIN_TX_FEE_HOWLIES)
    if amount <= 0:
        raise ValueError("amount_howl must be > 0")
    # safety cap for phase-1 test bot
    if amount > 1000 * COIN:
        raise ValueError("amount_howl too large for phase-1 test (max 1000 HOWL)")
    if cfg.max_cycles > 50:
        raise ValueError("max_cycles capped at 50 for safety")

    wa = Wallet(Path(cfg.wallet_a), create_if_missing=False)
    wb = Wallet(Path(cfg.wallet_b), create_if_missing=False)
    addr_a = wa.primary.address
    addr_b = wb.primary.address
    if addr_a == addr_b:
        raise ValueError("wallet A and B must be different addresses")

    report: Dict[str, Any] = {
        "system": "howl-agents/tx-test/v1",
        "phase": 1,
        "mode": "howl_ping_pong",
        "dry_run": cfg.dry_run,
        "api_base": cfg.api_base,
        "amount_howl": cfg.amount_howl,
        "fee_howl": fee / COIN,
        "max_cycles": cfg.max_cycles,
        "wallet_a": addr_a,
        "wallet_b": addr_b,
        "started_at": time.time(),
        "cycles": [],
        "ok": False,
    }

    try:
        report["balances_start"] = {
            "a": address_snapshot(cfg.api_base, addr_a),
            "b": address_snapshot(cfg.api_base, addr_b),
        }
    except Exception as e:
        report["balances_start_error"] = str(e)

    cycles: List[CycleResult] = []
    for i in range(1, int(cfg.max_cycles) + 1):
        cr = CycleResult(cycle=i)
        try:
            memo_ab = f"{cfg.memo_prefix}.c{i}.a2b"
            cr.a_to_b = send_leg(
                from_wallet=wa,
                to_address=addr_b,
                amount_howlies=amount,
                fee_howlies=fee,
                api_base=cfg.api_base,
                memo=memo_ab,
                dry_run=cfg.dry_run,
                data_dir=cfg.data_dir,
                wait_confirm_sec=cfg.wait_confirm_sec,
                poll_sec=cfg.poll_sec,
            )
            if not cfg.dry_run and not cr.a_to_b.get("confirmed"):
                cr.error = "A→B not confirmed in time (may still be in mempool — mine a block)"
                # still try reverse only if broadcast succeeded
                if cr.a_to_b.get("status") not in ("broadcast", "pending_timeout", "confirmed"):
                    cycles.append(cr)
                    break

            memo_ba = f"{cfg.memo_prefix}.c{i}.b2a"
            cr.b_to_a = send_leg(
                from_wallet=wb,
                to_address=addr_a,
                amount_howlies=amount,
                fee_howlies=fee,
                api_base=cfg.api_base,
                memo=memo_ba,
                dry_run=cfg.dry_run,
                data_dir=cfg.data_dir,
                wait_confirm_sec=cfg.wait_confirm_sec,
                poll_sec=cfg.poll_sec,
            )
            if cfg.dry_run:
                cr.ok = True
            else:
                cr.ok = bool(cr.a_to_b.get("txid") and cr.b_to_a.get("txid"))
                if not cr.b_to_a.get("confirmed") and not cr.error:
                    cr.error = "B→A not confirmed in time (may need mining)"
        except Exception as e:
            cr.error = str(e)
            cr.ok = False
        cycles.append(cr)
        report["cycles"].append(cr.to_dict())
        if not cr.ok and not cfg.dry_run:
            break

    report["finished_at"] = time.time()
    report["ok"] = all(c.ok for c in cycles) and len(cycles) > 0
    try:
        report["balances_end"] = {
            "a": address_snapshot(cfg.api_base, addr_a),
            "b": address_snapshot(cfg.api_base, addr_b),
        }
    except Exception as e:
        report["balances_end_error"] = str(e)

    if cfg.state_path:
        path = Path(cfg.state_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, indent=2, default=str))
        report["state_path"] = str(path)

    return report


def fund_hint(addr_a: str, addr_b: str, amount_howl: float, fee_howl: float = 1.0) -> str:
    # each cycle: A pays amount+fee, B pays amount+fee
    per = amount_howl + fee_howl
    return (
        f"Fund each wallet with at least ~{per * 2:.2f} HOWL for a few cycles "
        f"(amount {amount_howl} + fee {fee_howl} each direction).\n"
        f"  A: {addr_a}\n"
        f"  B: {addr_b}\n"
        f"Confirmations need miners: python3 -m howl mine  (or public seed mining)"
    )
