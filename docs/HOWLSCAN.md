# howlscan.org — live Howlcoin explorer

Public block explorer for **public** + **telegram** chains.

## 1. Buy the domain

Register **howlscan.org** at e.g.:

- https://www.cloudflare.com/products/registrar/
- https://porkbun.com/
- https://www.namecheap.com/

## 2. DNS (point at your VPS)

At your DNS provider, create:

| Type | Name | Content |
|------|------|---------|
| A | `@` | `147.182.223.204` |
| A | `www` | `147.182.223.204` |

Wait until:

```bash
dig +short howlscan.org
# → 147.182.223.204
```

## 3. On the VPS (after DNS works)

```bash
ssh -i ~/.ssh/id_ed25519_github root@147.182.223.204

cd /opt/howlcoin && git pull
source /opt/howlcoin-venv/bin/activate

# Explorer systemd unit
cat >/etc/systemd/system/howlcoin-explorer.service <<'EOF'
[Unit]
Description=Howlcoin explorer (howlscan.org)
After=network-online.target howlcoin.service

[Service]
Type=simple
WorkingDirectory=/opt/howlcoin
Environment=PATH=/opt/howlcoin-venv/bin:/usr/bin
ExecStart=/opt/howlcoin-venv/bin/python3 -m howl explorer \
  --host 127.0.0.1 --port 42080 \
  --public-data /var/lib/howlcoin \
  --telegram-data /var/lib/howlcoin-telegram
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now howlcoin-explorer

# nginx
apt update && apt install -y nginx certbot python3-certbot-nginx

cat >/etc/nginx/sites-available/howlscan <<'EOF'
server {
    listen 80;
    server_name howlscan.org www.howlscan.org;

    location / {
        proxy_pass http://127.0.0.1:42080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
EOF

ln -sf /etc/nginx/sites-available/howlscan /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
ufw allow 80/tcp
ufw allow 443/tcp
ufw status

# HTTPS
certbot --nginx -d howlscan.org -d www.howlscan.org
```

## 4. Verify

```bash
curl -s https://howlscan.org/api/networks
```

Browser: **https://howlscan.org/**

## 5. Wire Telegram bot

```bash
export HOWL_EXPLORER_URL='https://howlscan.org'
```

Pin in group:

```
🔍 Explorer: https://howlscan.org
🌱 Seed: 147.182.223.204:42069
🤖 Bot: https://t.me/HowlMine_bot
📦 Code: https://github.com/happyoils710/howlcoin
```
