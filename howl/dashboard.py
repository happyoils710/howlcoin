"""Howlcoin web dashboard — status, wallet, mine, peers (stdlib only)."""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional

from .blockchain import Blockchain
from .config import COIN_NAME, DEFAULT_RPC_PORT, TICKER, VERSION, WALLET_FILE
from .network import Node
from .wallet import Wallet, format_howl, parse_howl

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Howlcoin · HOWL</title>
<link rel="icon" href="/assets/howlcoin-logo-meme-pup-coin.jpg"/>
<style>
  :root {
    --bg: #0b1020;
    --panel: #121a2e;
    --border: #1e2a44;
    --text: #e8eef7;
    --muted: #8b9bb8;
    --green: #3dff9a;
    --amber: #ffb020;
    --danger: #ff5c7a;
    --moon: #c5d4f0;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0;
    font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
    background:
      radial-gradient(1200px 600px at 10% -10%, #1a2744 0%, transparent 55%),
      radial-gradient(900px 500px at 100% 0%, #152238 0%, transparent 50%),
      var(--bg);
    color: var(--text);
    min-height: 100vh;
  }
  header {
    display: flex; align-items: center; gap: 16px;
    padding: 20px 28px; border-bottom: 1px solid var(--border);
    backdrop-filter: blur(8px);
    position: sticky; top: 0; background: rgba(11,16,32,0.85); z-index: 10;
  }
  header img {
    width: 56px; height: 56px; border-radius: 50%;
    border: 2px solid var(--green); box-shadow: 0 0 24px rgba(61,255,154,0.25);
    object-fit: cover;
  }
  header h1 { margin: 0; font-size: 1.45rem; letter-spacing: 0.02em; }
  header p { margin: 2px 0 0; color: var(--muted); font-size: 0.9rem; }
  .tag {
    margin-left: auto; padding: 6px 12px; border-radius: 999px;
    background: rgba(61,255,154,0.12); color: var(--green);
    border: 1px solid rgba(61,255,154,0.35); font-size: 0.8rem; font-weight: 600;
  }
  main {
    max-width: 1100px; margin: 0 auto; padding: 24px;
    display: grid; gap: 18px;
    grid-template-columns: repeat(12, 1fr);
  }
  .card {
    background: linear-gradient(180deg, rgba(24,34,56,0.95), rgba(18,26,46,0.98));
    border: 1px solid var(--border); border-radius: 16px; padding: 18px 20px;
    box-shadow: 0 10px 40px rgba(0,0,0,0.25);
  }
  .card h2 {
    margin: 0 0 12px; font-size: 0.85rem; text-transform: uppercase;
    letter-spacing: 0.08em; color: var(--muted); font-weight: 600;
  }
  .span-4 { grid-column: span 4; }
  .span-6 { grid-column: span 6; }
  .span-8 { grid-column: span 8; }
  .span-12 { grid-column: span 12; }
  @media (max-width: 860px) {
    .span-4, .span-6, .span-8, .span-12 { grid-column: span 12; }
  }
  .stat { font-size: 1.65rem; font-weight: 700; color: var(--green); word-break: break-all; }
  .stat.small { font-size: 1.05rem; color: var(--moon); }
  .muted { color: var(--muted); font-size: 0.85rem; }
  .row { display: flex; gap: 10px; flex-wrap: wrap; align-items: center; margin-top: 10px; }
  input, button, select {
    font: inherit; border-radius: 10px; border: 1px solid var(--border);
    background: #0d1424; color: var(--text); padding: 10px 12px;
  }
  input { flex: 1; min-width: 140px; }
  button {
    cursor: pointer; background: linear-gradient(180deg, #1f3d32, #163028);
    border-color: rgba(61,255,154,0.4); color: var(--green); font-weight: 600;
  }
  button:hover { filter: brightness(1.1); }
  button.secondary { background: #151e32; color: var(--moon); border-color: var(--border); }
  button.danger { color: var(--danger); border-color: rgba(255,92,122,0.4); background: #2a1520; }
  button:disabled { opacity: 0.5; cursor: wait; }
  table { width: 100%; border-collapse: collapse; font-size: 0.9rem; }
  th, td { text-align: left; padding: 8px 6px; border-bottom: 1px solid var(--border); }
  th { color: var(--muted); font-weight: 600; font-size: 0.75rem; text-transform: uppercase; }
  .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 0.82rem; }
  .ok { color: var(--green); }
  .warn { color: var(--amber); }
  #log {
    max-height: 180px; overflow: auto; background: #0a0f1c; border-radius: 10px;
    padding: 10px; font-family: ui-monospace, monospace; font-size: 0.78rem;
    color: var(--muted); border: 1px solid var(--border);
  }
  #log div { margin-bottom: 4px; }
  footer { text-align: center; color: var(--muted); padding: 24px; font-size: 0.85rem; }
  .pill { display:inline-block; padding: 2px 8px; border-radius: 999px; background: #1a2438; font-size: 0.75rem; }
</style>
</head>
<body>
<header>
  <img src="/assets/howlcoin-logo-meme-pup-coin.jpg" alt="Howlcoin logo"/>
  <div>
    <h1>Howlcoin <span style="color:var(--green)">HOWL</span></h1>
    <p>Scrypt till the moon howls · local node dashboard</p>
  </div>
  <div class="tag" id="liveTag">● LIVE</div>
</header>
<main>
  <section class="card span-4">
    <h2>Height</h2>
    <div class="stat" id="height">—</div>
    <div class="muted" id="algo">scrypt</div>
  </section>
  <section class="card span-4">
    <h2>Difficulty</h2>
    <div class="stat" id="diff">—</div>
    <div class="muted">next: <span id="nextDiff">—</span></div>
  </section>
  <section class="card span-4">
    <h2>Circulating</h2>
    <div class="stat small" id="supply">—</div>
    <div class="muted">mempool: <span id="mempool">0</span> tx</div>
  </section>

  <section class="card span-6">
    <h2>Your wallet</h2>
    <div class="mono" id="address">—</div>
    <div class="stat" style="margin-top:8px" id="balance">—</div>
    <div class="row">
      <button class="secondary" onclick="copyAddr()">Copy address</button>
      <button onclick="mine(1)" id="mineBtn">Mine 1 block</button>
      <button class="secondary" onclick="mine(3)">Mine 3</button>
    </div>
    <p class="muted" style="margin-top:10px">Coinbase pays to this address. Mining uses Scrypt on this machine.</p>
  </section>

  <section class="card span-6">
    <h2>Send HOWL</h2>
    <div class="row">
      <input id="to" placeholder="Destination H… address"/>
    </div>
    <div class="row">
      <input id="amount" placeholder="Amount (e.g. 1000)"/>
      <button onclick="sendTx()">Queue send</button>
    </div>
    <p class="muted">Transfers sit in the mempool until someone mines a block.</p>
  </section>

  <section class="card span-6">
    <h2>P2P peers</h2>
    <div class="row">
      <input id="peer" placeholder="host:port (e.g. 192.168.1.20:42069)"/>
      <button onclick="connectPeer()">Connect</button>
    </div>
    <div class="row">
      <span class="pill">P2P port <span id="p2pPort">—</span></span>
      <span class="pill">RPC <span id="rpcPort">—</span></span>
      <span class="pill" id="peerCount">0 peers</span>
    </div>
    <table style="margin-top:12px">
      <thead><tr><th>Peer</th><th>Dir</th><th>Height</th><th>Status</th></tr></thead>
      <tbody id="peersBody"><tr><td colspan="4" class="muted">No peers yet — connect a friend</td></tr></tbody>
    </table>
  </section>

  <section class="card span-6">
    <h2>Tip & richlist</h2>
    <div class="muted">Tip hash</div>
    <div class="mono" id="tip">—</div>
    <table style="margin-top:12px">
      <thead><tr><th>Address</th><th>Balance</th></tr></thead>
      <tbody id="richBody"></tbody>
    </table>
  </section>

  <section class="card span-12">
    <h2>Event log</h2>
    <div id="log"></div>
  </section>
</main>
<footer>Howlcoin v<span id="ver"></span> · much chain · very scrypt · awoo</footer>
<script>
let lastAddr = '';
function log(msg, cls) {
  const el = document.getElementById('log');
  const d = document.createElement('div');
  d.textContent = new Date().toLocaleTimeString() + '  ' + msg;
  if (cls) d.className = cls;
  el.prepend(d);
}
async function api(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
async function refresh() {
  try {
    const s = await api('/api/status');
    document.getElementById('height').textContent = s.height;
    document.getElementById('diff').textContent = s.difficulty;
    document.getElementById('nextDiff').textContent = s.next_difficulty;
    document.getElementById('supply').textContent = s.circulating;
    document.getElementById('mempool').textContent = s.mempool;
    document.getElementById('tip').textContent = s.tip;
    document.getElementById('algo').textContent = s.algo + ' · target ' + s.block_time_target;
    document.getElementById('address').textContent = s.wallet.address;
    document.getElementById('balance').textContent = s.wallet.balance;
    document.getElementById('p2pPort').textContent = s.p2p_port;
    document.getElementById('rpcPort').textContent = s.rpc_port;
    document.getElementById('ver').textContent = s.version;
    lastAddr = s.wallet.address;
    document.getElementById('liveTag').textContent = s.node_running ? '● NODE LIVE' : '○ NODE OFF';
    document.getElementById('liveTag').style.color = s.node_running ? 'var(--green)' : 'var(--amber)';

    const peers = s.peers || [];
    document.getElementById('peerCount').textContent = peers.length + ' peers';
    const tb = document.getElementById('peersBody');
    if (!peers.length) {
      tb.innerHTML = '<tr><td colspan="4" class="muted">No peers yet — connect a friend</td></tr>';
    } else {
      tb.innerHTML = peers.map(p => `<tr>
        <td class="mono">${p.host}:${p.port}</td>
        <td>${p.inbound ? 'in' : 'out'}</td>
        <td>${p.height}</td>
        <td class="${p.alive ? 'ok' : 'warn'}">${p.alive ? 'alive' : 'down'}</td>
      </tr>`).join('');
    }
    const rich = s.richlist || [];
    document.getElementById('richBody').innerHTML = rich.map(r =>
      `<tr><td class="mono">${r.address.slice(0,10)}…${r.address.slice(-6)}</td><td>${r.balance}</td></tr>`
    ).join('') || '<tr><td colspan="2" class="muted">empty</td></tr>';
  } catch (e) {
    document.getElementById('liveTag').textContent = '● ERROR';
    document.getElementById('liveTag').style.color = 'var(--danger)';
  }
}
function copyAddr() {
  if (!lastAddr) return;
  navigator.clipboard.writeText(lastAddr);
  log('Copied ' + lastAddr, 'ok');
}
async function mine(n) {
  const btn = document.getElementById('mineBtn');
  btn.disabled = true;
  log('Mining ' + n + ' block(s) with Scrypt… this can take a bit');
  try {
    const j = await api('/api/mine', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({blocks: n})
    });
    log('Mined to height ' + j.height + ' · balance ' + j.balance, 'ok');
    refresh();
  } catch (e) {
    log('Mine failed: ' + e.message, 'warn');
  } finally {
    btn.disabled = false;
  }
}
async function sendTx() {
  const to = document.getElementById('to').value.trim();
  const amount = document.getElementById('amount').value.trim();
  try {
    const j = await api('/api/send', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({to, amount})
    });
    log('Queued tx ' + j.txid.slice(0,16) + '… — mine to confirm', 'ok');
    refresh();
  } catch (e) {
    log('Send failed: ' + e.message, 'warn');
  }
}
async function connectPeer() {
  const peer = document.getElementById('peer').value.trim();
  try {
    const j = await api('/api/connect', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({peer})
    });
    log(j.message, 'ok');
    refresh();
  } catch (e) {
    log('Connect failed: ' + e.message, 'warn');
  }
}
refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>
"""


class Dashboard:
    def __init__(
        self,
        chain: Blockchain,
        wallet: Wallet,
        node: Optional[Node] = None,
        host: str = "127.0.0.1",
        port: int = DEFAULT_RPC_PORT,
        p2p_port: int = 42069,
    ):
        self.chain = chain
        self.wallet = wallet
        self.node = node
        self.host = host
        self.port = port
        self.p2p_port = p2p_port
        self._mine_lock = threading.Lock()
        self._httpd: Optional[ThreadingHTTPServer] = None

    def _status(self) -> Dict[str, Any]:
        s = self.chain.summary()
        rich = sorted(self.chain.balances.items(), key=lambda x: -x[1])[:8]
        s["wallet"] = {
            "address": self.wallet.address,
            "balance": format_howl(self.chain.balance(self.wallet.address)),
            "balance_howlies": self.chain.balance(self.wallet.address),
        }
        s["peers"] = self.node.peer_status() if self.node else []
        s["node_running"] = self.node is not None
        s["p2p_port"] = self.p2p_port
        s["rpc_port"] = self.port
        s["version"] = VERSION
        s["richlist"] = [
            {"address": a, "balance": format_howl(b)} for a, b in rich
        ]
        return s

    def make_handler(self):
        dash = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return  # quiet

            def _json(self, code: int, obj: Any):
                body = json.dumps(obj).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                self.wfile.write(body)

            def _bytes(self, code: int, data: bytes, ctype: str):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path in ("/", "/index.html"):
                    return self._bytes(200, DASHBOARD_HTML.encode(), "text/html; charset=utf-8")
                if path.startswith("/assets/"):
                    name = path[len("/assets/") :]
                    # prevent path traversal
                    if ".." in name or name.startswith("/"):
                        return self._json(400, {"error": "bad path"})
                    f = ASSETS_DIR / name
                    if not f.is_file():
                        return self._json(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    return self._bytes(200, f.read_bytes(), ctype)
                if path == "/api/status":
                    return self._json(200, dash._status())
                return self._json(404, {"error": "not found"})

            def do_POST(self):
                path = urllib.parse.urlparse(self.path).path
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b"{}"
                try:
                    body = json.loads(raw.decode() or "{}")
                except json.JSONDecodeError:
                    return self._json(400, {"error": "invalid json"})

                if path == "/api/mine":
                    n = max(1, min(20, int(body.get("blocks", 1))))
                    if not dash._mine_lock.acquire(blocking=False):
                        return self._json(409, {"error": "mining already in progress"})
                    try:
                        for _ in range(n):
                            if dash.node:
                                with dash.node.chain_lock:
                                    block = dash.chain.mine_one(dash.wallet.address)
                                dash.node.announce_block(block)
                            else:
                                block = dash.chain.mine_one(dash.wallet.address)
                        return self._json(
                            200,
                            {
                                "ok": True,
                                "height": dash.chain.height(),
                                "balance": format_howl(
                                    dash.chain.balance(dash.wallet.address)
                                ),
                            },
                        )
                    except Exception as e:
                        return self._json(500, {"error": str(e)})
                    finally:
                        dash._mine_lock.release()

                if path == "/api/send":
                    try:
                        to = body["to"]
                        amount = parse_howl(str(body["amount"]))
                        fee = parse_howl(str(body["fee"])) if body.get("fee") else 0
                        nonce = dash.chain.next_nonce(dash.wallet.address)
                        tx = dash.wallet.build_tx(to, amount, nonce, fee=fee)
                        if dash.node:
                            with dash.node.chain_lock:
                                ok, msg = dash.chain.add_to_mempool(tx)
                            if ok:
                                dash.node.announce_tx(tx)
                        else:
                            ok, msg = dash.chain.add_to_mempool(tx)
                        if not ok:
                            return self._json(400, {"error": msg})
                        return self._json(200, {"ok": True, "txid": msg})
                    except Exception as e:
                        return self._json(400, {"error": str(e)})

                if path == "/api/connect":
                    if not dash.node:
                        return self._json(400, {"error": "P2P node not running — start with: howl node"})
                    peer = body.get("peer", "").strip()
                    if not peer:
                        return self._json(400, {"error": "peer required"})
                    try:
                        dash.node.add_seed(peer)
                        return self._json(200, {"ok": True, "message": f"connecting to {peer}"})
                    except Exception as e:
                        return self._json(400, {"error": str(e)})

                return self._json(404, {"error": "not found"})

        return Handler

    def serve_forever(self) -> None:
        handler = self.make_handler()
        self._httpd = ThreadingHTTPServer((self.host, self.port), handler)
        print(f"Howlcoin dashboard → http://{self.host}:{self.port}/")
        print(f"  chain height {self.chain.height()} · wallet {self.wallet.address}")
        self._httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, name="howl-dashboard", daemon=True)
        t.start()
        return t
