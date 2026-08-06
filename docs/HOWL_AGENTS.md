# Howl Autonomous Agents

Multi-agent system that **monitors Howlchain**, **coordinates by consensus**, **settles on-chain**, and can **bootstrap full nodes** through local + DePIN compute markets.

## Goals

| Capability | How |
|---|---|
| Monitor opportunities, anomalies, security, oracles, protocol health | Role monitors (`health`, `security`, `oracle`, `opportunity`) |
| Multi-agent coordination + consensus | `Council` quorum voting |
| On-chain settlement | Oracle txs under `howl.agent.consensus.*` / `howl.agent.finding.*` |
| Economic autonomy | Soft `AgentTreasury` budget, max-tx, min-reserve |
| DePIN / decentralized compute | Local, Akash SDL, Nosana job specs |
| Bootstrap full nodes at scale | `InfraGovernor` fleet + launch scripts / systemd / compose |
| Closed-loop self-governance | Agents write `governance.json` + fleet inventory they re-read |

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     AgentRuntime (loop)                      │
│  health │ security │ oracle │ opportunity  →  Findings       │
│              Coordinator → Proposals → Council votes         │
│         approved → settle oracle tx +/or bootstrap nodes     │
│         AgentTreasury  │  InfraGovernor (DePIN providers)    │
└─────────────────────────────────────────────────────────────┘
```

### Monitors

- **health** — tip age, height stall, API reachability  
- **security** — `/api/public/security`, wrap/bridge misconfig, freeze authority  
- **oracle** — oracle feed integrity, agent settlements, price flips  
- **opportunity** — mempool depth, wrap backlog, low peers, slow blocks  

Findings at **medium+** severity become proposals.

### Consensus

In-process council (extensible to multi-host later):

1. Coordinator submits `Proposal` (`report` | `alert` | `bootstrap_node` | …)  
2. Role agents cast auto-votes (affinity by role + related findings)  
3. Quorum: `yes >= required_votes` and `yes > no` (default quorum **2**)

### Settlement

When `--settle` and a funded wallet are set:

- Consensus payloads → oracle key `howl.agent.consensus.<proposal_id>`  
- Critical findings → `howl.agent.finding.<finding_id>`  
- Fee: minimum **1 HOWL** per oracle tx (chain rule)  
- Treasury must have soft budget remaining  

### DePIN providers

| Provider | Behavior |
|---|---|
| **local** | Writes `launch.sh`, systemd unit, docker-compose; can spawn process if `--live-infra` |
| **akash** | Generates Akash SDL (`deploy.yml`); live submit when Akash env set |
| **nosana** | Generates Nosana job JSON; API key optional |

Default is **dry-run**: manifests and inventory only — safe for continuous ops. Use `--live-infra` to actually start local nodes.

## Run

```bash
# Single observation tick (safe)
python3 scripts/howl-agent-runtime.py --once --api https://howlscan.org

# Continuous monitoring
python3 scripts/howl-agent-runtime.py --api https://howlscan.org --interval 60

# Status snapshot
python3 scripts/howl-agent-runtime.py --status

# Set soft budget to 500 HOWL
python3 scripts/howl-agent-runtime.py --budget 500 --status

# On-chain settlement of high+ consensus
python3 scripts/howl-agent-runtime.py --settle --wallet ~/.howlcoin/wallet.json --once

# Bootstrap local full-node processes when proposals approve
python3 scripts/howl-agent-runtime.py --live-infra --once
```

Or via CLI:

```bash
python3 -m howl agents --once
python3 -m howl agents --status
```

## Environment

| Variable | Meaning |
|---|---|
| `HOWL_AGENTS_API` | Explorer base (default `https://howlscan.org`) |
| `HOWL_AGENTS_STATE` | State dir (default `~/.howlcoin/agents`) |
| `HOWL_AGENTS_WALLET` | Wallet for settlement |
| `HOWL_AGENTS_SETTLE` | `1` to settle on-chain |
| `HOWL_AGENTS_DRY_RUN` | `0` to live-deploy infra |
| `HOWL_AGENTS_INTERVAL` | Seconds between ticks |
| `HOWL_AGENTS_QUORUM` | Required yes votes |
| `HOWL_AGENTS_SEED` | P2P seed for new nodes |
| `HOWL_AGENTS_SETTLE_SEVERITY` | Min severity to settle (`high` default) |
| `AKASH_*` / `NOSANA_API_KEY` | Optional DePIN credentials |
| `HOWL_AKASH_HOWL_PER_DAY` | Soft cost estimate for treasury |

## State layout

```
~/.howlcoin/agents/
  status.json          # latest public status
  history.json         # findings + consensus results
  treasury.json        # economic autonomy ledger
  infra/
    fleet.json         # bootstrapped nodes
    governance.json    # self-written infra policy
    depin/local|akash|nosana/...
```

## API

`GET /api/public/agents` — agent system description + optional live `status.json` if the explorer host runs the runtime with shared state.

## systemd

```bash
# On VPS
cp deploy/howl-agents.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now howl-agents
journalctl -u howl-agents -f
```

## Public seeds

Agents maintain a **public seed directory** so the network is not a single hardcoded IP forever.

| Piece | Path / API |
|-------|------------|
| Registry file | `/var/lib/howlcoin/public_seeds.json` (or `HOWL_SEEDS_FILE`) |
| HTTP API | `GET /api/public/seeds` |
| Docs | [SEEDS.md](../SEEDS.md) |

Every tick, agents:

1. Ensure the **primary** seed (`147.182.223.204:42069`) is registered  
2. Re-publish any **fleet** nodes that have a public announce address  
3. Expose counts in agent `status.json` → `/api/public/agents` → `live.public_seeds`

When bootstrapping nodes with a public host:

```bash
# On agent host — advertise this IP:port for new nodes
export HOWL_PUBLIC_NODE_HOST=203.0.113.10
# optional extra static seeds
export HOWL_PUBLIC_SEEDS=203.0.113.10:42069
```

Local/`127.0.0.1` endpoints are **not** listed as public. Live infra + open firewall still required for a new seed to show `status: up`.

## Phase 1 tx test (HOWL ping-pong)

Automated **test** transfers between two agent wallets (no SOL, no wrap).

```bash
# Create two test wallets
python3 -m howl --data-dir ~/.howlcoin/agent-a init
python3 -m howl --data-dir ~/.howlcoin/agent-b init

# Show addresses (fund them with HOWL from your main wallet / faucet / mine)
python3 scripts/howl-agent-tx-test.py \
  --wallet-a ~/.howlcoin/agent-a/wallet.json \
  --wallet-b ~/.howlcoin/agent-b/wallet.json \
  --show-addresses

# Dry-run (default) — builds flow, no broadcast
python3 scripts/howl-agent-tx-test.py \
  --wallet-a ~/.howlcoin/agent-a/wallet.json \
  --wallet-b ~/.howlcoin/agent-b/wallet.json \
  --amount 2 --cycles 1 --api https://howlscan.org

# Live — requires env gate
HOWL_AGENTS_TRADE=1 python3 scripts/howl-agent-tx-test.py \
  --wallet-a ~/.howlcoin/agent-a/wallet.json \
  --wallet-b ~/.howlcoin/agent-b/wallet.json \
  --amount 2 --cycles 1 --live --api https://howlscan.org
```

| Guard | Detail |
|-------|--------|
| Default | **Dry-run** |
| Live | `--live` **and** `HOWL_AGENTS_TRADE=1` |
| Caps | amount ≤ 1000 HOWL/leg, ≤ 50 cycles |
| Fee | min 1 HOWL per leg (chain rule) |
| Report | `~/.howlcoin/agents/tx-test-last.json` |

Each cycle: **A → B** then **B → A**. Confirmations need miners on the public chain (or local mine if using `--data-dir`).

Module: `howl/agents/tradetest.py`

## Safety

- **Dry-run by default** for infrastructure.  
- Settlement is **opt-in** and costs real HOWL fees.  
- Tx-test is **opt-in** (`HOWL_AGENTS_TRADE=1` + `--live`).  
- Treasury caps prevent runaway spend.  
- No private keys in DePIN manifests.  
- Agents do not control mint authority or bridge hot wallets unless you point `--wallet` at those files (not recommended for production mint authority).  
- Public seed registry only lists endpoints; it does not grant mint/wallet control.

## Roadmap

- Multi-host council over P2P / signed votes  
- Live Akash `tx deployment create` automation  
- Nosana job submit SDK  
- Pack-wallet alerts for critical severity  
- Agent-governed funding pools (on-chain multisig budgets)  
- Cross-chain agent messaging (allowlisted)  
- Auto-promote healthy community seeds after multi-agent attestation
