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
from .config import DEFAULT_DATA_DIR, WALLET_FILE
from .wallet import Wallet, format_howl

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
    ):
        self.data_dir = data_dir
        self.users_dir = data_dir / "tg_users"
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self.seed = seed
        self.mine_cooldown = max(30, mine_cooldown)
        self.chain = Blockchain(data_dir)
        self.chain_lock = threading.RLock()
        self.mine_lock = threading.Lock()
        self.last_mine: Dict[int, float] = {}
        self.admins = _admin_ids()
        self._meta_path = data_dir / "tg_meta.json"
        self.meta = self._load_meta()

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
            f"/wallet — address + balance\n"
            f"/mnemonic — 12-word phrase *(DM only, once carefully)*\n"
            f"/mine — mine 1 Scrypt block (rate limited)\n"
            f"/status — network height\n"
            f"/seed — public P2P seed\n"
            f"/newwallet — rotate to a new wallet\n"
            f"/help — full help\n\n"
            f"⚠ Save /mnemonic offline. Never share it in the group."
        )
        kb = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("💼 Wallet", callback_data="wallet"),
                    InlineKeyboardButton("⛏ Mine 1", callback_data="mine"),
                ],
                [
                    InlineKeyboardButton("📡 Status", callback_data="status"),
                    InlineKeyboardButton("🌱 Seed", callback_data="seed"),
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
            "*Private chat (wallet + mine)*\n"
            "/start /wallet /mnemonic /mine /newwallet\n\n"
            "*Anywhere*\n"
            "/status /seed /help\n\n"
            f"*Public seed*\n`{self.seed}`\n"
            "Desktop miner: github.com/happyoils710/howlcoin\n\n"
            "Group spam protection: use @MissRose\\_bot / Combot — see docs/TELEGRAM.md"
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

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return
        with self.chain_lock:
            s = self.chain.summary()
        await update.message.reply_text(
            f"🐺 *Howlcoin status*\n"
            f"Height: `{s['height']}`\n"
            f"Diff: `{s['difficulty']}`\n"
            f"Supply: `{s['circulating']}`\n"
            f"Tip: `{s['tip'][:20]}…`\n"
            f"Algo: `{s['algo']}`\n"
            f"Seed: `{self.seed}`",
            parse_mode=ParseMode.MARKDOWN,
        )

    async def cmd_wallet(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message or not update.effective_user:
            return
        if not self.is_private(update):
            await update.message.reply_text("🔒 Open a *private chat* with me for /wallet.", parse_mode=ParseMode.MARKDOWN)
            return
        uid = update.effective_user.id
        w = self.get_wallet(uid, create=True)
        assert w
        with self.chain_lock:
            bal = format_howl(self.chain.balance(w.address))
        await update.message.reply_text(
            f"💼 *Your wallet*\nAddress:\n`{w.address}`\n\nBalance: *{bal}*\n"
            f"Mnemonic: {'yes — /mnemonic' if w.has_mnemonic else 'legacy key only'}",
            parse_mode=ParseMode.MARKDOWN,
        )

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
        if not q or not update.effective_user:
            return
        await q.answer()
        data = q.data or ""
        # synthesize a fake path via editing
        class R:
            pass

        if data == "wallet":
            if q.message and q.message.chat.type != ChatType.PRIVATE:
                await q.message.reply_text("🔒 Wallet only in private chat.")
                return
            uid = update.effective_user.id
            w = self.get_wallet(uid, create=True)
            assert w
            with self.chain_lock:
                bal = format_howl(self.chain.balance(w.address))
            await q.message.reply_text(
                f"💼 `{w.address}`\nBalance: *{bal}*",
                parse_mode=ParseMode.MARKDOWN,
            )
        elif data == "mine":
            if q.message and q.message.chat.type != ChatType.PRIVATE:
                await q.message.reply_text("⛏ Mine only in private chat.")
                return
            await self._do_mine(update, update.effective_user.id, q.message.reply_text)
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
    app.add_handler(CommandHandler("wallet", bot.cmd_wallet))
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
    bot = HowlBot(data_dir=data, seed=seed, mine_cooldown=cooldown)
    app = build_app(token, bot)
    log.info("Howlcoin telegram bot starting · data=%s seed=%s", data, seed)
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
