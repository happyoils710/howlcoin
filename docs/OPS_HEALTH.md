# Howlcoin ops: health, mining self-heal, second seed

## What is automatic (no input)

| Feature | Behavior |
|---------|----------|
| `HOWL_AUTO_MINE=1` | Continuous mining on node start |
| 90s mining slices | Rebuild template so stall/retarget apply |
| 2h stall relief | Easier work if tip is old |
| Retarget safety (h≥300) | Max **2×** up; no up when window already slow |
| `Restart=always` | systemd restarts crashed seed |

## Seed (VPS)

```bash
# status
systemctl status howlcoin howlcoin-explorer
curl -sS https://howlscan.org/api/public/health | python3 -m json.tool

# ensure auto-mine
cat /etc/systemd/system/howlcoin.service.d/automine.conf
# should contain: Environment=HOWL_AUTO_MINE=1

systemctl restart howlcoin
```

Local dashboard while node runs: `http://127.0.0.1:42070/` — **Mining pulse** shows H/s, diff, ETA, template refresh.

## Health check (cron)

```bash
chmod +x /opt/howlcoin/scripts/howl-health-check.sh

# every 15 minutes
*/15 * * * * /opt/howlcoin/scripts/howl-health-check.sh >> /var/log/howl-health.log 2>&1
```

Optional Telegram:

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
export MAX_TIP_AGE=7200   # seconds
/opt/howlcoin/scripts/howl-health-check.sh
```

## Second seed (recommended)

1. Spin a small VPS (or home always-on box).
2. Install Howlcoin, open TCP **42069**.
3. Point it at the primary seed:

```bash
HOWL_AUTO_MINE=1 python3 -m howl node --public --connect 147.182.223.204:42069
# or systemd ExecStart with --connect PRIMARY_IP:42069
```

4. Optionally list both seeds in docs / Run a node page.

Primary seed stays `147.182.223.204:42069`. A second peer lowers “one box quiet = chain looks dead.”

## Public health UI

- **https://howlscan.org/#/health** — tip age, status, block-time & difficulty charts  
- **API:** `GET /api/public/health?window=48`

## Version note

**v0.6.1** retarget safety applies from height **300** (historical blocks unchanged). Miners should run **0.6.1+**.
