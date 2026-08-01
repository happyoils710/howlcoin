# Public seed nodes

List stable Howlcoin P2P seeds here so miners know where to connect.

| Host | Port | Notes |
|------|------|--------|
| `SEED_HOST_HERE` | `42069` | Replace with your public IP or domain |

## Miners connect with

```bash
python3 -m howl node --connect SEED_HOST_HERE:42069
```

Or in the dashboard peer box: `SEED_HOST_HERE:42069`

## Operators

- Run: `python3 -m howl node --host 0.0.0.0 --port 42069`
- Open **TCP 42069** on your router / cloud firewall
- Prefer a VPS with a static IP or a DNS name (e.g. `seed.howlcoin.example`)
- Keep mining wallet keys **off** the public seed box if you can (seed can be a watch-only / separate data dir)
