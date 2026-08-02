"""
Howlcoin Telegram bot — wallets in DMs, optional mining, group-safe commands.

Security:
  - Mnemonics / keys only in private chat
  - Group chat: public info only (status, seed, help)
  - Mining is rate-limited (shared server CPU)

Env:
  HOWL_TELEGRAM_TOKEN   BotFather token (required)
  HOWL_DATA_DIR         chain + bot state (default: ~/.howlcoin-telegram)
  HOWL_SEED             public seed shown to users (default: 147.182.223.204:42069)
  HOWL_MINE_COOLDOWN    seconds between mines per user (default: 120)
  HOWL_ADMIN_IDS        comma-separated Telegram user ids allowed extra commands
  HOWL_EXPLORER_URL     web explorer base URL (optional), e.g. http://127.0.0.1:42080
  HOWL_PUBLIC_DATA      optional path to public chain for /explorer dual view
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Set

from .blockchain import Blockchain
from .config import WALLET_FILE
from .crypto import is_valid_address
from .wallet import Wallet, format_howl, parse_howl

log = logging.getLogger("howl.telegram")

try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.constants import ChatType, ParseMode
    from telegram.ext import (
        Application,
        CallbackQueryHandler,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )
except ImportError as e:  # pragma: no cover
    raise SystemExit(
        "Install telegram deps: pip install 'python-telegram-bot>=21'\n" + str(e)
    )


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _admin_ids() -> Set[int]:
    raw = _env("HOWL_ADMIN_IDS", "")
    out: Set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            out.add(int(part))
    return out


class HowlBot:
    def __init__(
        self,
        data_dir: Path,
        seed: str = "147.182.223.204:42069",
        mine_cooldown: int = 120,
        explorer_url: str = "",
        public_data: Optional[Path] = None,
    ):
        self.data_dir = data_dir
        self.users_dir = data_dir / "tg_users"
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.explorer_url = (explorer_url or "").rstrip("/")
        self.mine_cooldown = max(30, mine_cooldown)
        self.chain = Blockchain(data_dir)
        self.chain_lock = threading.RLock()
        self.mine_lock = threading.Lock()
        self.last_mine: Dict[int, float] = {}
        self.admins = _admin_ids()
        self._meta_path = data_dir / "tg_meta.json"
        self.meta = self._load_meta()
        # user_id -> pending send {to, amount_howlies, fee}
        self.pending_sends: Dict[int, Dict[str, Any]] = {}
        # optional second chain (public seed ledger) for explorer commands
        self.public_chain: Optional[Blockchain] = None
        self.public_path = public_data
        if public_data and (Path(public_data).expanduser() / "chain.json").exists():
            try:
                self.public_chain = Blockchain(Path(public_data).expanduser())
            except Exception:
                self.public_chain = None

    def _load_meta(self) -> Dict[str, Any]:
        if self._meta_path.exists():
            return json.loads(self._meta_path.read_text())
        return {"welcome_once": {}}

    def _save_meta(self) -> None:
        self._meta_path.write_text(json.dumps(self.meta, indent=2))

    def user_wallet_path(self, user_id: int) -> Path:
        d = self.users_dir / str(user_id)
        d.mkdir(parents=True, exist_ok=True)
        return d / WALLET_FILE

    def get_wallet(self, user_id: int, create: bool = True) -> Optional[Wallet]:
        path = self.user_wallet_path(user_id)
        if path.exists():
            return Wallet(path, create_if_missing=False)
        if create:
            return Wallet(path, create_if_missing=True)
        return None

    def is_private(self, update: Update) -> bool:
        chat = update.effective_chat
        return bool(chat and chat.type == ChatType.PRIVATE)

    # ----- handlers -----

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.effective_user or not update.message:
            return
        uid = update.effective_user.id
        name = update.effective_user.first_name or "fren"

        if not self.is_private(update):
            await update.message.reply_text(
                "👋 Howlcoin bot here.\n"
                "For a wallet, message me in **private chat** (tap my name → Message).\n"
                "In groups I only answer /status /seed /help.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        w = self.get_wallet(uid, create=True)
        assert w is not None
        with self.chain_lock:
            bal = format_howl(self.chain.balance(w.address))
            height = self.chain.height()

        text = (
            f"🐺 *Howlcoin* — Scrypt meme coin\n\n"
            f"Hey {name}. Your wallet is ready.\n\n"
            f"*Address*\n`{w.address}`\n\n"
            f"*Balance* {bal}\n"
            f"*Chain height* {height}\n\n"
            f"*Commands*\n"
            f"/wallet /receive — address + balance\n"
            f"/send `H…addr` `amount` — send HOWL\n"
            f"/mnemonic — 12-word phrase *(DM only)*\n"
            f"/mine — mine 1 block (confirms pending sends)\n"
            f"/status /seed /help\n\n"
            f"⚠ Save /mnemonic offline. Never share it in the group."
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("💼 Wallet", callback_data="wallet"),
                    InlineKeyboardButton("📥 Receive", callback_data="receive"),
                ],
                [
                    InlineKeyboardButton("⛏ Mine 1", callback_data="mine"),
                    InlineKeyboardButton("📡 Status", callback_data="status"),
                ],
            ]
        )
        await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)

        if w.has_mnemonic and str(uid) not in self.meta.get("welcome_once", {}):
            await update.message.reply_text(
                "🔐 *Write these 12 words down NOW* (only shown in this private chat):\n\n"
                f"`{w.mnemonic}`\n\n"
                "Anyone with these words owns your HOWL.",
                parse_mode=ParseMode.MARKDOWN,
            )
            self.meta.setdefault("welcome_once", {})[str(uid)] = int(time.time())
            self._save_meta()

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        msg = (
            "*Howlcoin bot*\n\n"
            "*Private chat*\n"
            "/start /wallet /receive — your address\n"
            "/send `ADDRESS` `AMOUNT` — e.g. `/send Hxxx… 1000`\n"
            "/mnemonic /mine /newwallet\n\n"
            "*Explorer (anywhere)*\n"
            "/status /seed /explorer\n"
            "/blocks · /block `N` · /tx `id` · /addr `H…`\n\n"
            "Sends sit in the mempool until someone `/mine`s a block.\n\n"
            f"*Public seed*\n`{self.seed}`\n"
            "Desktop: github.com/happyoils710/howlcoin"
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    async def cmd_seed(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        await update.message.reply_text(
            f"🌐 *Public P2P seed*\n`{self.seed}`\n\n"
            f"```\npython3 -m howl node --connect {self.seed}\n```",
            parse_mode=ParseMode.MARKDOWN,
        )

    def _fmt_status(self, label: str, s: Dict[str, Any]) -> str:
        return (
            f"*{label}*\n"
            f"Height: `{s['height']}` · Diff: `{s['difficulty']}`\n"
            f"Supply: `{s['circulating']}`\n"
            f"Mempool: `{s['mempool']}`\n"
            f"Tip: `{s['tip'][:24]}…`"
        )

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        with self.chain_lock:
            try:
                self.chain.reload_from_disk()
            except Exception:
                pass
            s = self.chain.summary()
        parts = ["🐺 *Howlcoin status*\n\n", self._fmt_status("Telegram bot chain", s)]
        if self.public_chain:
            try:
                self.public_chain.reload_from_disk()
                ps = self.public_chain.summary()
                parts.append("\n\n" + self._fmt_status("Public / seed chain", ps))
            except Exception:
                pass
        parts.append(f"\n\nSeed: `{self.seed}`")
        if self.explorer_url:
            parts.append(f"\nWeb explorer: {self.explorer_url}")
        parts.append("\n\n/block /tx /addr /blocks /explorer")
        await update.message.reply_text("".join(parts), parse_mode=ParseMode.MARKDOWN)

    def _pick_chain(self, name: Optional[str] = None):
        n = (name or "bot").lower()
        if n in ("public", "pub", "seed", "main") and self.public_chain:
            try:
                self.public_chain.reload_from_disk()
            except Exception:
                pass
            return "public", self.public_chain
        with self.chain_lock:
            try:
                self.chain.reload_from_disk()
            except Exception:
                pass
            return "telegram", self.chain

    async def cmd_explorer(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        lines = [
            "🔍 *Howlcoin explorer*\n\n",
            "*In this bot*\n",
            "`/blocks` — recent blocks\n",
            "`/block 81` — by height or hash\n",
            "`/tx <txid>` — transaction\n",
            "`/addr H…` — address balance + history\n",
            "`/status` — tip(s)\n",
        ]
        if self.public_chain:
            lines.append(
                "\n*Public chain*\n"
                "`/block 81 public` · `/blocks public` · `/addr H… public`\n"
            )
        if self.explorer_url:
            lines.append(f"\n*Web UI*\n{self.explorer_url}\n")
        else:
            lines.append(
                "\n*Web UI (desktop)*\n"
                "`python3 -m howl explorer` → http://127.0.0.1:42080/\n"
            )
        await update.message.reply_text("".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_blocks(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        args = context.args or []
        net = args[0] if args and args[0].isalpha() else None
        label, chain = self._pick_chain(net)
        recent = chain.recent_blocks(10)
        lines = [f"📦 *Recent blocks* ({label})\n"]
        for b in recent:
            lines.append(
                f"`#{b['height']}` `{b['hash'][:14]}…` · "
                f"{b['tx_count']} tx · {format_howl(int(b.get('reward') or 0))}\n"
            )
        lines.append("\n`/block <height>` for detail")
        await update.message.reply_text("".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_block(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        args = list(context.args or [])
        if not args:
            await update.message.reply_text(
                "Usage: `/block 81` or `/block <hash>`\nOptional: `/block 81 public`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        net = None
        if len(args) >= 2 and args[-1].lower() in ("public", "pub", "seed", "main", "telegram", "bot"):
            net = args.pop()
        label, chain = self._pick_chain(net)
        b = chain.get_block(args[0])
        if not b:
            await update.message.reply_text(
                f"Block not found on *{label}*: `{args[0]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        txs = b.get("transactions") or []
        cb = next((t for t in txs if t.get("type") == "coinbase"), None)
        lines = [
            f"📦 *Block #{b['height']}* ({label})\n",
            f"`{b['hash']}`\n",
            f"Diff `{b['header'].get('difficulty')}` · nonce `{b['header'].get('nonce')}`\n",
            f"Txs: `{len(txs)}`\n",
        ]
        if cb:
            lines.append(
                f"Miner: `{cb.get('to')}`\n"
                f"Reward: *{format_howl(int(cb.get('amount') or 0))}*\n"
            )
        lines.append("\n*Transactions*\n")
        for t in txs[:12]:
            if t.get("type") == "coinbase":
                lines.append(
                    f"· coinbase → `{str(t.get('to'))[:14]}…` "
                    f"{format_howl(int(t.get('amount') or 0))}\n"
                )
            else:
                tid = (t.get("txid") or "")[:14]
                lines.append(
                    f"· `{tid}…` {format_howl(int(t.get('amount') or 0))}\n"
                    f"  `{str(t.get('from'))[:10]}…` → `{str(t.get('to'))[:10]}…`\n"
                )
        await update.message.reply_text("".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_tx(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        args = list(context.args or [])
        if not args:
            await update.message.reply_text("Usage: `/tx <txid>`", parse_mode=ParseMode.MARKDOWN)
            return
        net = None
        if len(args) >= 2 and args[-1].lower() in ("public", "pub", "seed", "main", "telegram", "bot"):
            net = args.pop()
        label, chain = self._pick_chain(net)
        found = chain.find_tx(args[0])
        if not found:
            await update.message.reply_text(f"Tx not found on *{label}*", parse_mode=ParseMode.MARKDOWN)
            return
        t = found["tx"]
        conf = "confirmed" if found.get("confirmed") else "mempool (unconfirmed)"
        lines = [
            f"🧾 *Transaction* ({label})\n",
            f"`{t.get('txid')}`\n",
            f"Status: *{conf}*\n",
        ]
        if found.get("block_height") is not None:
            lines.append(f"Block: `#{found['block_height']}`\n")
        if t.get("type") == "coinbase":
            lines.append(
                f"Coinbase → `{t.get('to')}`\n*{format_howl(int(t.get('amount') or 0))}*\n"
            )
        else:
            lines.append(
                f"From: `{t.get('from')}`\n"
                f"To: `{t.get('to')}`\n"
                f"Amount: *{format_howl(int(t.get('amount') or 0))}*\n"
                f"Fee: {format_howl(int(t.get('fee') or 0))}\n"
            )
            if t.get("memo"):
                lines.append(f"Memo: {t.get('memo')}\n")
        await update.message.reply_text("".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_addr_lookup(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        args = list(context.args or [])
        if not args:
            await update.message.reply_text(
                "Usage: `/addr HYourAddress…`\nYour deposit address: /receive",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        net = None
        if len(args) >= 2 and args[-1].lower() in ("public", "pub", "seed", "main", "telegram", "bot"):
            net = args.pop()
        addr = args[0].strip()
        label, chain = self._pick_chain(net)
        hist = chain.address_history(addr, limit=12)
        lines = [
            f"💼 *Address* ({label})\n",
            f"`{hist['address']}`\n",
            f"Balance: *{hist['balance_fmt']}*\n",
            f"Nonce: `{hist['nonce']}`\n\n*Recent activity*\n",
        ]
        for t in hist.get("transactions") or []:
            if t.get("type") == "coinbase":
                lines.append(
                    f"· in coinbase #{t.get('block_height')} "
                    f"{format_howl(int(t.get('amount') or 0))}\n"
                )
            else:
                lines.append(
                    f"· {t.get('direction')} #{t.get('block_height')} "
                    f"{format_howl(int(t.get('amount') or 0))}\n"
                )
        if not hist.get("transactions"):
            lines.append("_no txs_\n")
        await update.message.reply_text("".join(lines), parse_mode=ParseMode.MARKDOWN)

    async def cmd_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text("🔒 Open a *private chat* with me for /wallet.", parse_mode=ParseMode.MARKDOWN)
            return
        await self._reply_wallet(update.effective_user.id, update.message.reply_text)

    async def cmd_receive(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Show deposit address — others /send to this address."""
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text(
                "📥 DM me for your receive address (keeps it out of the group).",
            )
            return
        await self._reply_receive(update.effective_user.id, update.message.reply_text)

    async def _reply_wallet(self, uid: int, reply) -> None:
        w = self.get_wallet(uid, create=True)
        assert w
        with self.chain_lock:
            bal = format_howl(self.chain.balance(w.address))
            pending = sum(
                1
                for t in self.chain.mempool
                if t.get("from") == w.address or t.get("to") == w.address
            )
        await reply(
            f"💼 *Your wallet*\n"
            f"Address:\n`{w.address}`\n\n"
            f"Balance: *{bal}*\n"
            f"Mempool txs touching you: {pending}\n"
            f"Receive: /receive · Send: /send",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def _reply_receive(self, uid: int, reply) -> None:
        w = self.get_wallet(uid, create=True)
        assert w
        with self.chain_lock:
            bal = format_howl(self.chain.balance(w.address))
        # QR via public image API (address only — safe)
        qr = f"https://api.qrserver.com/v1/create-qr-code/?size=220x220&data={w.address}"
        await reply(
            f"📥 *Receive HOWL*\n\n"
            f"Share this address:\n`{w.address}`\n\n"
            f"Balance: *{bal}*\n\n"
            f"Someone can send with:\n"
            f"`/send {w.address} 1000`\n\n"
            f"[QR code]({qr})",
            parse_mode=ParseMode.MARKDOWN,
            disable_web_page_preview=False,
        )

    async def cmd_send(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """
        /send <address> <amount> [fee]
        Queues a signed tx; mine a block to confirm.
        """
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text("🔒 /send only works in *private chat* with me.", parse_mode=ParseMode.MARKDOWN)
            return
        uid = update.effective_user.id
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text(
                "Usage:\n`/send ADDRESS AMOUNT`\n"
                "Example:\n`/send HJt1gm6PvAu5mcvkan9fsSbQtVVdBg9bQ6 500`\n"
                "Optional fee: `/send ADDR 500 1`",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        to_addr = args[0].strip()
        try:
            amount = parse_howl(args[1])
            fee = parse_howl(args[2]) if len(args) >= 3 else 0
        except ValueError as e:
            await update.message.reply_text(f"Bad amount: {e}")
            return
        if not is_valid_address(to_addr):
            await update.message.reply_text("❌ Invalid Howlcoin address (should start with H…).")
            return
        if amount <= 0:
            await update.message.reply_text("Amount must be positive.")
            return

        w = self.get_wallet(uid, create=True)
        assert w
        if to_addr == w.address:
            await update.message.reply_text("That's your own address.")
            return
        with self.chain_lock:
            bal = self.chain.balance(w.address)
        if bal < amount + fee:
            await update.message.reply_text(
                f"Insufficient balance.\nHave *{format_howl(bal)}*, need *{format_howl(amount + fee)}*.\n"
                f"Mine first with /mine if you're new.",
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        self.pending_sends[uid] = {
            "to": to_addr,
            "amount": amount,
            "fee": fee,
            "ts": time.time(),
        }
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("✅ Confirm send", callback_data="send_yes"),
                    InlineKeyboardButton("❌ Cancel", callback_data="send_no"),
                ]
            ]
        )
        await update.message.reply_text(
            f"📤 *Confirm send*\n\n"
            f"To:\n`{to_addr}`\n"
            f"Amount: *{format_howl(amount)}*\n"
            f"Fee: *{format_howl(fee)}*\n"
            f"From:\n`{w.address}`\n\n"
            f"After confirm, run /mine (or wait for a miner) to *confirm* on-chain.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=kb,
        )

    async def _execute_send(self, uid: int, reply) -> None:
        pending = self.pending_sends.pop(uid, None)
        if not pending:
            await reply("No pending send. Use `/send ADDRESS AMOUNT` first.")
            return
        if time.time() - pending.get("ts", 0) > 600:
            await reply("Pending send expired (10 min). Start again with /send.")
            return
        w = self.get_wallet(uid, create=False)
        if not w:
            await reply("No wallet — /start first.")
            return
        to_addr = pending["to"]
        amount = int(pending["amount"])
        fee = int(pending["fee"])
        try:
            with self.chain_lock:
                nonce = self.chain.next_nonce(w.address)
                tx = w.build_tx(to_addr, amount, nonce, fee=fee, memo="tg-send")
                ok, msg = self.chain.add_to_mempool(tx)
            if not ok:
                await reply(f"❌ Rejected: {msg}")
                return
            await reply(
                f"✅ *Queued transfer*\n"
                f"txid: `{msg[:20]}…`\n"
                f"{format_howl(amount)} → `{to_addr[:14]}…`\n\n"
                f"Still in *mempool* until a block is mined.\n"
                f"Tap /mine to confirm now (or wait for another miner).",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            await reply(f"❌ Send failed: {e}")

    async def cmd_mnemonic(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text("🚫 Mnemonics only in private chat.")
            return
        w = self.get_wallet(update.effective_user.id, create=True)
        assert w
        if not w.has_mnemonic:
            await update.message.reply_text("No BIP39 phrase on this wallet. Use /newwallet.")
            return
        await update.message.reply_text(
            "🔐 *BIP39 recovery phrase* — store offline, never share:\n\n"
            f"`{w.mnemonic}`\n\n"
            f"Address: `{w.address}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_newwallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text("🔒 /newwallet only in private chat.")
            return
        uid = update.effective_user.id
        path = self.user_wallet_path(uid)
        if path.exists():
            old = Wallet(path)
            bak = old.backup_file()
            path.unlink()
            note = f"Old wallet backed up as `{bak.name}` on server."
        else:
            note = "Fresh wallet."
        w = Wallet(path, create_if_missing=True)
        self.meta.setdefault("welcome_once", {}).pop(str(uid), None)
        self._save_meta()
        await update.message.reply_text(
            f"✨ New wallet created.\n{note}\n\n"
            f"Address:\n`{w.address}`\n\n"
            f"Phrase:\n`{w.mnemonic}`\n\n"
            "Save it offline. Old funds stay on the old address.",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_mine(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text(
                "⛏ Mining from the bot is *private chat only* (so rewards go to your wallet).\n"
                "DM me → /mine\nOr run a desktop miner on the public seed: /seed",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        await self._do_mine(update, update.effective_user.id, update.message.reply_text)

    async def _do_mine(self, update: Update, uid: int, reply) -> None:
        now = time.time()
        last = self.last_mine.get(uid, 0)
        wait = self.mine_cooldown - (now - last)
        if wait > 0 and uid not in self.admins:
            await reply(f"⏳ Cooldown: try again in {int(wait)}s (shared CPU).")
            return
        if not self.mine_lock.acquire(blocking=False):
            await reply("⏳ Someone else is mining on this bot — try again in a minute.")
            return
        w = self.get_wallet(uid, create=True)
        assert w
        await reply(f"⛏ Mining 1 Scrypt block to `{w.address[:12]}…` — can take ~30–90s…", parse_mode=ParseMode.MARKDOWN)

        def work():
            with self.chain_lock:
                block = self.chain.mine_one(w.address)
                bal = self.chain.balance(w.address)
                return block, bal

        try:
            block, bal = await asyncio.get_event_loop().run_in_executor(None, work)
            self.last_mine[uid] = time.time()
            await reply(
                f"✅ Block `#{block['height']}`\n"
                f"Hash `{block['hash'][:18]}…`\n"
                f"Balance *{format_howl(bal)}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            await reply(f"❌ Mine failed: {e}")
        finally:
            self.mine_lock.release()

    async def on_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        q = update.callback_query
        if not q or not update.effective_user or not q.message:
            return
        await q.answer()
        data = q.data or ""
        uid = update.effective_user.id
        private = q.message.chat.type == ChatType.PRIVATE

        if data == "wallet":
            if not private:
                await q.message.reply_text("🔒 Wallet only in private chat.")
                return
            await self._reply_wallet(uid, q.message.reply_text)
        elif data == "receive":
            if not private:
                await q.message.reply_text("🔒 Receive address only in private chat.")
                return
            await self._reply_receive(uid, q.message.reply_text)
        elif data == "mine":
            if not private:
                await q.message.reply_text("⛏ Mine only in private chat.")
                return
            await self._do_mine(update, uid, q.message.reply_text)
        elif data == "send_yes":
            if not private:
                return
            await self._execute_send(uid, q.message.reply_text)
        elif data == "send_no":
            self.pending_sends.pop(uid, None)
            await q.message.reply_text("Cancelled. Nothing sent.")
        elif data == "status":
            with self.chain_lock:
                s = self.chain.summary()
            await q.message.reply_text(
                f"Height `{s['height']}` · {s['circulating']}",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "seed":
            await q.message.reply_text(f"Seed: `{self.seed}`", parse_mode=ParseMode.MARKDOWN)

    async def block_group_secrets(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """If someone pastes a mnemonic-looking blob in a group, warn (light)."""
        if not update.message or not update.message.text:
            return
        if self.is_private(update):
            return
        words = update.message.text.strip().split()
        if len(words) in (12, 24) and all(w.isalpha() for w in words):
            await update.message.reply_text(
                "⚠️ That looks like a seed phrase. *Never* post recovery words in a group. "
                "If those were real, move funds immediately.",
                parse_mode=ParseMode.MARKDOWN,
            )


def build_app(token: str, bot: HowlBot) -> Application:
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", bot.cmd_start))
    app.add_handler(CommandHandler("help", bot.cmd_help))
    app.add_handler(CommandHandler("seed", bot.cmd_seed))
    app.add_handler(CommandHandler("status", bot.cmd_status))
    app.add_handler(CommandHandler(["explorer", "explore"], bot.cmd_explorer))
    app.add_handler(CommandHandler("blocks", bot.cmd_blocks))
    app.add_handler(CommandHandler("block", bot.cmd_block))
    app.add_handler(CommandHandler("tx", bot.cmd_tx))
    app.add_handler(CommandHandler(["addr", "lookup"], bot.cmd_addr_lookup))
    app.add_handler(CommandHandler("wallet", bot.cmd_wallet))
    app.add_handler(CommandHandler(["receive", "deposit", "address"], bot.cmd_receive))
    app.add_handler(CommandHandler("send", bot.cmd_send))
    app.add_handler(CommandHandler("mnemonic", bot.cmd_mnemonic))
    app.add_handler(CommandHandler("newwallet", bot.cmd_newwallet))
    app.add_handler(CommandHandler("mine", bot.cmd_mine))
    app.add_handler(CallbackQueryHandler(bot.on_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, bot.block_group_secrets))
    return app


def main() -> None:
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        level=logging.INFO,
    )
    token = _env("HOWL_TELEGRAM_TOKEN") or _env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "Set HOWL_TELEGRAM_TOKEN (from @BotFather).\n"
            "Example:\n  export HOWL_TELEGRAM_TOKEN='123:ABC'\n  python3 -m howl telegram"
        )
    data = Path(_env("HOWL_DATA_DIR", str(Path.home() / ".howlcoin-telegram"))).expanduser()
    seed = _env("HOWL_SEED", "147.182.223.204:42069")
    cooldown = int(_env("HOWL_MINE_COOLDOWN", "120") or "120")
    explorer_url = _env("HOWL_EXPLORER_URL", "")
    public_raw = _env("HOWL_PUBLIC_DATA", str(Path.home() / ".howlcoin"))
    public_data = Path(public_raw).expanduser() if public_raw else None
    bot = HowlBot(
        data_dir=data,
        seed=seed,
        mine_cooldown=cooldown,
        explorer_url=explorer_url,
        public_data=public_data,
    )
    app = build_app(token, bot)
    log.info(
        "Howlcoin telegram bot starting · data=%s seed=%s explorer=%s public=%s",
        data,
        seed,
        explorer_url or "(none)",
        public_data if bot.public_chain else "(not loaded)",
    )
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
