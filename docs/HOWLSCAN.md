# howlscan.org — live Howlcoin explorer

Public block explorer for the **public** Howlcoin seed chain.

## 1. Domain + DNS

Register **howlscan.org** and add A records to `147.182.223.204` (see main setup notes).

## 2. On the VPS

```bash
ssh -i ~/.ssh/id_ed25519_github root@147.182.223.204

cd /opt/howlcoin && git pull
systemctl restart howlcoin-explorer
systemctl status howlcoin-explorer
```

Explorer unit runs:

```text
python3 -m howl explorer --host 127.0.0.1 --port 42080 --public-data /var/lib/howlcoin
```

nginx proxies **howlscan.org** → `127.0.0.1:42080` with HTTPS (certbot).

## 3. Verify

```bash
curl -s https://howlscan.org/api/networks
```

Browser: **https://howlscan.org/**

## 4. Share

```
🔍 Explorer: https://howlscan.org
🌱 Seed: 147.182.223.204:42069
📦 Code: https://github.com/happyoils710/howlcoin
```
