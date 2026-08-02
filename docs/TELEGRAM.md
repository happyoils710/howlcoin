# Howlcoin Telegram — group + bot + anti-spam

## What you get

| Piece | Role |
|-------|------|
| **Public group** | Community, announcements, help |
| **@HowlcoinBot** (your bot) | `/wallet`, `/mine`, `/status` in **private DMs** |
| **Rose / Combot** | Kick spam, captcha, flood control in the **group** |

Wallet secrets and mining buttons stay in **DMs**. The group stays clean.

---

## 1. Create the bot (@BotFather)

1. Open Telegram → search **@BotFather**
2. `/newbot`
3. Name: `Howlcoin` (display name)
4. Username: e.g. `HowlcoinHOWL_bot` (must end in `bot`)
5. Copy the **token** (`123456:ABC-DEF...`)
6. Optional:
   - `/setdescription` — “Scrypt meme coin · mine + wallet”
   - `/setabouttext` — short about
   - `/setuserpic` — use `assets/howlcoin-logo.jpg`
   - `/setcommands` — paste:

```
start - Open wallet
wallet - Address & balance
mine - Mine 1 Scrypt block
mnemonic - Show 12-word phrase (DM only)
status - Chain height
seed - Public P2P seed
newwallet - New wallet
help - Help
```

---

## 2. Create the group

1. New group → name **Howlcoin** or **Howlcoin HOWL**
2. Add your bot as **member**
3. Promote bot to **admin** (needed only if you later add kick features; optional for reply-only)
4. Add anti-spam bots (below) as **admin** with:
   - Delete messages  
   - Ban users  
   - Invite users (if captcha requires)

Invite link: Group → Add members → Invite via link → Copy  
Put that link in GitHub README (not the bot token).

---

## 3. Best anti-spam bots (use these)

### A. Miss Rose — @MissRose_bot (recommended #1)

Most used open-source group manager.

1. Add **@MissRose_bot** to the group  
2. Make it **admin** (delete + ban)  
3. In group, try:

```
/help
/captcha on
/welcome on
/antiflood on
/blocklists on
```

Useful:

| Command | Effect |
|---------|--------|
| `/captcha on` | New members solve captcha or get kicked |
| `/antiflood on` | Stops spam floods |
| `/locks` | Lock links/forwards if abused |
| `/warn` `/ban` `/mute` | Moderation |
| `/connected` | Connect multiple groups (advanced) |

Docs: https://missrose.org (or Rose’s in-bot help)

### B. Combot — @combot (analytics + antispam)

1. Add **@combot** → admin  
2. Open Combot settings from the bot DM after it’s in the group  
3. Enable **antispam**, **CAS** (Combot Antispam / shared ban list) if available  
4. Captcha / welcome as needed  

Good combo: **Rose for captcha/mod** + **Combot for CAS/stats**.

### C. Optional extras

| Bot | Use |
|-----|-----|
| **@GroupHelpBot** / Shieldy | Captcha alternative |
| **@AntiServiceMessage_bot** | Hide join/leave spam |
| **@ControllerBot** | Channel comments control |

**Don’t** stack 5 captcha bots — they fight each other. Pick **Rose + Combot**.

---

## 4. Run the Howlcoin bot (wallet + mine)

### On your Mac (test)

```bash
cd ~/Desktop/howlcoin
python3 -m pip install --user -r requirements.txt

export HOWL_TELEGRAM_TOKEN='PASTE_BOTFATHER_TOKEN'
export HOWL_SEED='147.182.223.204:42069'
export HOWL_DATA_DIR="$HOME/.howlcoin-telegram"

python3 -m howl telegram
# or: python3 -m howl telegram --token 'PASTE' --seed '147.182.223.204:42069'
```

Leave Terminal open. DM the bot → `/start`.

### On the DigitalOcean VPS (24/7 — recommended)

```bash
ssh -i ~/.ssh/id_ed25519_github root@147.182.223.204

cd /opt/howlcoin && git pull
source /opt/howlcoin-venv/bin/activate
pip install 'python-telegram-bot>=21'

# store token securely
mkdir -p /etc/howlcoin
nano /etc/howlcoin/telegram.env
# contents:
# HOWL_TELEGRAM_TOKEN=123:ABC
# HOWL_SEED=147.182.223.204:42069
# HOWL_DATA_DIR=/var/lib/howlcoin-telegram
# HOWL_MINE_COOLDOWN=120

cat >/etc/systemd/system/howlcoin-telegram.service <<'EOF'
[Unit]
Description=Howlcoin Telegram bot
After=network-online.target howlcoin.service

[Service]
Type=simple
EnvironmentFile=/etc/howlcoin/telegram.env
WorkingDirectory=/opt/howlcoin
ExecStart=/opt/howlcoin-venv/bin/python3 -m howl telegram
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now howlcoin-telegram
systemctl status howlcoin-telegram
```

---

## 5. Bot behavior (by design)

| Command | Private DM | Public group |
|---------|------------|--------------|
| `/start` `/wallet` `/mine` `/mnemonic` `/newwallet` | ✅ | ❌ (redirect to DM) |
| `/status` `/seed` `/help` | ✅ | ✅ |
| Seed-phrase looking spam | — | ⚠️ warning |

- `/mine` is **rate limited** (default 120s/user) — shared server CPU  
- For serious hashrate: desktop miner → public seed  
- Mnemonics **never** answered in groups  

---

## 6. Security notes

- Bot token = full control of the bot → **never** commit to GitHub  
- Bot-created wallets live on the **server** under `HOWL_DATA_DIR/tg_users/`  
- Treat bot wallets as **convenience / meme** storage; big bags → personal offline mnemonic  
- Users who got phrases in DM should still write them down  

---

## 7. Pin message for the group

```
🐺 Howlcoin (HOWL) — Scrypt meme coin

• DM the bot for wallet + mine: @YourBotUsername
• Desktop / public seed: 147.182.223.204:42069
• Code: https://github.com/happyoils710/howlcoin
• Rules: no spam, no seed phrases in chat, no fake giveaways
• Captcha on join via Rose
```

---

## Checklist

- [ ] @BotFather bot + token  
- [ ] Group created + invite link  
- [ ] @MissRose_bot admin + captcha  
- [ ] @combot admin + antispam  
- [ ] Howl bot running (`howl telegram` or systemd)  
- [ ] Test DM: /start → wallet → /mine  
- [ ] Pin rules + GitHub + seed  
