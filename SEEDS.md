# Public seed nodes

Anyone on the internet can join Howlcoin by connecting to a **public seed** (full node with P2P open).

## Quick start

```bash
git clone https://github.com/happyoils710/howlcoin.git
cd howlcoin
python3 -m pip install --user -r requirements.txt
python3 -m howl init
python3 -m howl node --connect 147.182.223.204:42069
# or:
python3 -m howl node --public --auto-mine
```

Dashboard: http://127.0.0.1:42070/ → **Mine**

## Live seed directory (API)

Howlscan publishes a dynamic list (primary + agent/operator-registered peers):

```bash
curl -sS https://howlscan.org/api/public/seeds | python3 -m json.tool
# skip TCP probes:
curl -sS 'https://howlscan.org/api/public/seeds?probe=0'
```

| Field | Meaning |
|-------|---------|
| `primary` | Canonical seed endpoint |
| `seeds[]` | Directory entries (`endpoint`, `role`, `source`, `status`) |
| `status` | `up` / `down` / `local` / `unknown` (when `probe=1`) |
| `source` | `static` · `env` · `agent` · `file` / operator |

Agents re-publish the primary seed every tick and register new public nodes they bootstrap (when a public host is configured).

## Known seeds

| Host | Port | Notes |
|------|------|--------|
| **`147.182.223.204`** | **`42069`** | DigitalOcean VPS seed (primary, 24/7) |

Agent **dry-run** plans are **not** public seeds. Only live, registered endpoints appear in the API.

### Add a second public node

See **[docs/PUBLIC_NODE2.md](docs/PUBLIC_NODE2.md)** — preferred on a **second machine**; same-host requires an explicit safety override.

```bash
bash scripts/howl-public-node2-bootstrap.sh   # see docs for env vars
bash scripts/howl-cleanup-agent-fleet.sh      # wipe dry-run fleet noise
```

Additional seeds appear in the API when operators or agents register them in:

- `/var/lib/howlcoin/public_seeds.json` (VPS)
- `HOWL_SEEDS_FILE` path
- `HOWL_PUBLIC_SEEDS=host:port,host2:port2` env

## Run your own public seed

```bash
# On a VPS with a public IP — open TCP 42069
python3 -m howl node --host 0.0.0.0 --port 42069 --public --auto-mine \
  --connect 147.182.223.204:42069
```

Register it (operator or agent host):

```bash
python3 - <<'PY'
from pathlib import Path
from howl.seeds import register_public_seed
register_public_seed(
    "YOUR_PUBLIC_IP:42069",
    path=Path("/var/lib/howlcoin/public_seeds.json"),
    source="operator",
    notes="community seed",
    public=True,
)
print("registered")
PY
```

Or set on the agent host:

```bash
export HOWL_PUBLIC_NODE_HOST=YOUR_PUBLIC_IP   # agents advertise this host when bootstrapping
export HOWL_PUBLIC_SEEDS=YOUR_PUBLIC_IP:42069
```

## Operators (primary seed)

```bash
ssh -i ~/.ssh/id_ed25519_howl root@147.182.223.204
systemctl status howlcoin
journalctl -u howlcoin -f
systemctl status howl-agents
curl -sS http://127.0.0.1:42080/api/public/seeds | python3 -m json.tool
```

| Service | Role |
|---------|------|
| `howlcoin` | Primary P2P seed |
| `howlcoin-explorer` | howlscan.org + `/api/public/seeds` |
| `howl-agents` | Monitor network; maintain seed registry |

Data: `/var/lib/howlcoin` · Code: `/opt/howlcoin`

See also: [docs/HOWL_AGENTS.md](docs/HOWL_AGENTS.md)
