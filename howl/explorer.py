"""
Howlcoin multi-chain block explorer (read-only).

Default networks:
  - public   → ~/.howlcoin or HOWL_PUBLIC_DATA
  - telegram → ~/.howlcoin-telegram or HOWL_TELEGRAM_DATA

Run:
  python3 -m howl explorer
  open http://127.0.0.1:42080/
"""

from __future__ import annotations

import json
import mimetypes
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from .blockchain import Blockchain
from .config import DEFAULT_DATA_DIR
from .crypto import is_valid_address
from .wallet import format_howl

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

DEFAULT_PUBLIC = Path.home() / ".howlcoin"
DEFAULT_TELEGRAM = Path.home() / ".howlcoin-telegram"


def _chain_or_none(data_dir: Path) -> Optional[Blockchain]:
    data_dir = data_dir.expanduser()
    if not (data_dir / "chain.json").exists():
        return None
    try:
        return Blockchain(data_dir)
    except Exception:
        return None


class ExplorerHub:
    """Holds multiple named chains and reloads them from disk on demand."""

    def __init__(self, networks: Dict[str, Path]):
        self.paths = {k: Path(v).expanduser() for k, v in networks.items()}
        self._chains: Dict[str, Blockchain] = {}
        self.refresh_all()

    def refresh_all(self) -> None:
        for name, path in self.paths.items():
            c = _chain_or_none(path)
            if c:
                self._chains[name] = c
            elif name in self._chains:
                # keep stale if temporarily missing
                pass

    def refresh(self, name: str) -> Optional[Blockchain]:
        path = self.paths.get(name)
        if not path:
            return None
        if name in self._chains:
            try:
                self._chains[name].reload_from_disk()
                return self._chains[name]
            except Exception:
                pass
        c = _chain_or_none(path)
        if c:
            self._chains[name] = c
        return self._chains.get(name)

    def list_networks(self) -> List[Dict[str, Any]]:
        self.refresh_all()
        out = []
        for name, path in self.paths.items():
            c = self._chains.get(name)
            if c:
                try:
                    c.reload_from_disk()
                except Exception:
                    pass
                s = c.summary()
                out.append(
                    {
                        "id": name,
                        "label": name.replace("_", " ").title(),
                        "path": str(path),
                        "online": True,
                        "height": s["height"],
                        "tip": s["tip"],
                        "circulating": s["circulating"],
                        "difficulty": s["difficulty"],
                        "mempool": s["mempool"],
                    }
                )
            else:
                out.append(
                    {
                        "id": name,
                        "label": name.replace("_", " ").title(),
                        "path": str(path),
                        "online": False,
                        "height": None,
                        "tip": None,
                        "note": "No chain.json yet — mine or sync first",
                    }
                )
        return out

    def get(self, name: str) -> Optional[Blockchain]:
        return self.refresh(name)


EXPLORER_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Howlcoin Explorer</title>
<link rel="icon" href="/assets/howlcoin-logo.jpg"/>
<style>
  :root {
    --bg:#0b1020; --panel:#121a2e; --border:#1e2a44; --text:#e8eef7;
    --muted:#8b9bb8; --green:#3dff9a; --amber:#ffb020; --moon:#c5d4f0;
  }
  *{box-sizing:border-box}
  body{margin:0;font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;
    background:radial-gradient(900px 500px at 0% 0%,#1a2744,transparent 50%),var(--bg);color:var(--text)}
  header{display:flex;align-items:center;gap:14px;padding:16px 22px;border-bottom:1px solid var(--border);
    position:sticky;top:0;background:rgba(11,16,32,.9);backdrop-filter:blur(8px);z-index:5}
  header img{width:48px;height:48px;border-radius:50%;border:2px solid var(--green);object-fit:cover}
  header h1{margin:0;font-size:1.2rem}
  header p{margin:2px 0 0;color:var(--muted);font-size:.85rem}
  main{max-width:1100px;margin:0 auto;padding:20px}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
  .tab{padding:8px 14px;border-radius:999px;border:1px solid var(--border);background:#0d1424;
    color:var(--moon);cursor:pointer;font-weight:600}
  .tab.active{border-color:rgba(61,255,154,.5);color:var(--green);background:rgba(61,255,154,.1)}
  .card{background:linear-gradient(180deg,#182238,#121a2e);border:1px solid var(--border);
    border-radius:14px;padding:16px 18px;margin-bottom:14px}
  .row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
  input,button,select{font:inherit;border-radius:10px;border:1px solid var(--border);
    background:#0d1424;color:var(--text);padding:10px 12px}
  input{flex:1;min-width:180px}
  button{cursor:pointer;background:#163028;border-color:rgba(61,255,154,.4);color:var(--green);font-weight:600}
  table{width:100%;border-collapse:collapse;font-size:.9rem}
  th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--border);vertical-align:top}
  th{color:var(--muted);font-size:.75rem;text-transform:uppercase}
  a{color:var(--green);text-decoration:none}
  a:hover{text-decoration:underline}
  .mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.82rem;word-break:break-all}
  .muted{color:var(--muted);font-size:.85rem}
  .stat{font-size:1.4rem;font-weight:700;color:var(--green)}
  .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(180px,1fr))}
  pre{background:#0a0f1c;padding:12px;border-radius:10px;overflow:auto;font-size:.78rem;border:1px solid var(--border)}
  .pill{display:inline-block;padding:2px 8px;border-radius:999px;background:#1a2438;font-size:.75rem}
  .offline{color:var(--amber)}
</style>
</head>
<body>
<header>
  <img src="/assets/howlcoin-logo.jpg" alt="HOWL"/>
  <div>
    <h1>Howlcoin Explorer</h1>
    <p>Multi-chain · Public seed + Telegram bot</p>
  </div>
</header>
<main>
  <div class="tabs" id="tabs"></div>
  <div class="card">
    <div class="row">
      <input id="q" placeholder="Search height, block hash, txid, or address (H…)"/>
      <button onclick="search()">Search</button>
      <button class="tab" style="background:#151e32;color:var(--moon)" onclick="loadHome()">Refresh</button>
    </div>
  </div>
  <div id="view"></div>
</main>
<script>
let net = 'public';
let networks = [];

async function api(path){
  const r = await fetch(path);
  const j = await r.json();
  if(!r.ok) throw new Error(j.error || r.statusText);
  return j;
}
function el(id){return document.getElementById(id)}
function short(h,n=12){if(!h)return '—'; return h.slice(0,n)+'…'}
function fmtAmt(a){
  if(a==null) return '—';
  const n = Number(a)/1e8;
  return n.toLocaleString(undefined,{maximumFractionDigits:8})+' HOWL';
}
function linkBlock(h){return `<a href="#" onclick="showBlock('${h}');return false">${h}</a>`}
function linkTx(t){return `<a class="mono" href="#" onclick="showTx('${t}');return false">${short(t,16)}</a>`}
function linkAddr(a){if(!a||a==='HOWL_GENESIS_BURN') return `<span class="mono">${a||'—'}</span>`;
  return `<a class="mono" href="#" onclick="showAddr('${a}');return false">${short(a,10)}</a>`}

async function loadTabs(){
  const data = await api('/api/networks');
  networks = data.networks || [];
  const tabs = el('tabs');
  tabs.innerHTML = networks.map(n =>
    `<button class="tab ${n.id===net?'active':''}" onclick="switchNet('${n.id}')">
      ${n.label} ${n.online?`<span class="pill">h ${n.height}</span>`:'<span class="offline">offline</span>'}
    </button>`
  ).join('');
}
function switchNet(id){net=id; loadHome()}

async function loadHome(){
  await loadTabs();
  const s = await api(`/api/${net}/summary`);
  if(!s.online){
    el('view').innerHTML = `<div class="card"><h3>${s.label}</h3>
      <p class="muted">No chain at <span class="mono">${s.path}</span></p>
      <p class="muted">${s.note||''}</p></div>`;
    return;
  }
  const blocks = await api(`/api/${net}/blocks?limit=20`);
  el('view').innerHTML = `
    <div class="grid">
      <div class="card"><div class="muted">Network</div><div class="stat" style="font-size:1.1rem">${s.label}</div>
        <div class="muted mono">${s.path}</div></div>
      <div class="card"><div class="muted">Height</div><div class="stat">${s.height}</div></div>
      <div class="card"><div class="muted">Difficulty</div><div class="stat">${s.difficulty}</div></div>
      <div class="card"><div class="muted">Circulating</div><div class="stat" style="font-size:1rem">${s.circulating}</div></div>
      <div class="card"><div class="muted">Mempool</div><div class="stat">${s.mempool}</div></div>
    </div>
    <div class="card">
      <div class="muted">Tip</div>
      <div class="mono">${s.tip}</div>
    </div>
    <div class="card">
      <h3 style="margin:0 0 10px;font-size:.9rem;color:var(--muted)">Recent blocks</h3>
      <table>
        <thead><tr><th>Height</th><th>Hash</th><th>Txs</th><th>Miner</th><th>Reward</th><th>Time</th></tr></thead>
        <tbody>
          ${(blocks.blocks||[]).map(b=>`<tr>
            <td>${linkBlock(b.height)}</td>
            <td class="mono"><a href="#" onclick="showBlock('${b.hash}');return false">${short(b.hash,14)}</a></td>
            <td>${b.tx_count}</td>
            <td>${linkAddr(b.miner)}</td>
            <td>${fmtAmt(b.reward)}</td>
            <td class="muted">${b.timestamp?new Date(b.timestamp*1000).toLocaleString():'—'}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>`;
}

async function showBlock(id){
  await loadTabs();
  const d = await api(`/api/${net}/block/${encodeURIComponent(id)}`);
  const b = d.block;
  const txs = b.transactions||[];
  el('view').innerHTML = `
    <div class="card">
      <button onclick="loadHome()">← Back</button>
      <h2 style="margin:12px 0 4px">Block #${b.height}</h2>
      <div class="mono">${b.hash}</div>
      <p class="muted">diff ${b.header.difficulty} · nonce ${b.header.nonce} ·
        ${b.header.timestamp?new Date(b.header.timestamp*1000).toLocaleString():''}</p>
      <p class="muted">prev: <span class="mono">${b.header.prev_hash}</span></p>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Transactions (${txs.length})</h3>
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>From / To</th><th>Amount</th></tr></thead>
        <tbody>
          ${txs.map(t=>`<tr>
            <td>${t.txid?linkTx(t.txid):'—'}</td>
            <td>${t.type||'transfer'}</td>
            <td class="mono">${t.type==='coinbase'
              ? 'coinbase → '+linkAddr(t.to)
              : linkAddr(t.from)+' → '+linkAddr(t.to)}</td>
            <td>${fmtAmt(t.amount)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card"><pre>${JSON.stringify(b,null,2)}</pre></div>`;
}

async function showTx(id){
  await loadTabs();
  const d = await api(`/api/${net}/tx/${encodeURIComponent(id)}`);
  const t = d.tx;
  el('view').innerHTML = `
    <div class="card">
      <button onclick="loadHome()">← Back</button>
      <h2 style="margin:12px 0 4px">Transaction</h2>
      <div class="mono">${t.txid||id}</div>
      <p>${d.confirmed
        ? `Confirmed in block ${linkBlock(d.block_height)}`
        : '<span class="offline">Unconfirmed (mempool)</span>'}</p>
    </div>
    <div class="card"><pre>${JSON.stringify(d,null,2)}</pre></div>`;
}

async function showAddr(addr){
  await loadTabs();
  const d = await api(`/api/${net}/address/${encodeURIComponent(addr)}`);
  el('view').innerHTML = `
    <div class="card">
      <button onclick="loadHome()">← Back</button>
      <h2 style="margin:12px 0 4px">Address</h2>
      <div class="mono">${d.address}</div>
      <p class="stat" style="font-size:1.2rem;margin:8px 0">${d.balance_fmt}</p>
      <p class="muted">nonce ${d.nonce} · ${d.tx_count} recent txs shown</p>
    </div>
    <div class="card">
      <table>
        <thead><tr><th>Dir</th><th>Tx</th><th>Amount</th><th>Block</th></tr></thead>
        <tbody>
          ${(d.transactions||[]).map(t=>`<tr>
            <td>${t.direction||t.type}</td>
            <td>${t.txid?linkTx(t.txid):'—'}</td>
            <td>${fmtAmt(t.amount)}</td>
            <td>${t.block_height!=null?linkBlock(t.block_height):'—'}</td>
          </tr>`).join('') || '<tr><td colspan="4" class="muted">No txs</td></tr>'}
        </tbody>
      </table>
    </div>`;
}

async function search(){
  const q = el('q').value.trim();
  if(!q) return loadHome();
  if(/^\d+$/.test(q)) return showBlock(q);
  if(q.startsWith('H') && q.length>20) return showAddr(q);
  // try block then tx
  try { return await showBlock(q); } catch(e) {}
  try { return await showTx(q); } catch(e) {}
  try { return await showAddr(q); } catch(e) {
    el('view').innerHTML = `<div class="card">Not found on <b>${net}</b>: <span class="mono">${q}</span></div>`;
  }
}

loadHome().catch(e => el('view').innerHTML = `<div class="card">Error: ${e.message}</div>`);
setInterval(()=>{ if(!el('q').value) loadHome().catch(()=>{}); }, 15000);
</script>
</body>
</html>
"""


class ExplorerServer:
    def __init__(
        self,
        hub: ExplorerHub,
        host: str = "127.0.0.1",
        port: int = 42080,
    ):
        self.hub = hub
        self.host = host
        self.port = port

    def make_handler(self):
        hub = self.hub

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                return

            def _json(self, code: int, obj: Any):
                body = json.dumps(obj, default=str).encode()
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

            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                path = parsed.path
                qs = urllib.parse.parse_qs(parsed.query)

                if path in ("/", "/index.html"):
                    return self._bytes(200, EXPLORER_HTML.encode(), "text/html; charset=utf-8")

                if path.startswith("/assets/"):
                    name = path[len("/assets/") :]
                    if ".." in name:
                        return self._json(400, {"error": "bad path"})
                    f = ASSETS_DIR / name
                    if not f.is_file():
                        return self._json(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    return self._bytes(200, f.read_bytes(), ctype)

                if path == "/api/networks":
                    return self._json(200, {"networks": hub.list_networks()})

                # /api/<net>/...
                parts = path.strip("/").split("/")
                if len(parts) >= 2 and parts[0] == "api":
                    net = parts[1]
                    if net not in hub.paths:
                        return self._json(404, {"error": f"unknown network {net}"})
                    chain = hub.get(net)
                    rest = parts[2:] if len(parts) > 2 else []

                    if not rest or rest == ["summary"]:
                        nets = {n["id"]: n for n in hub.list_networks()}
                        info = nets.get(net, {"id": net, "online": False})
                        if chain:
                            s = chain.summary()
                            info = {**info, **s, "online": True, "label": info.get("label", net)}
                        return self._json(200, info)

                    if not chain:
                        return self._json(404, {"error": "chain offline", "network": net})

                    if rest[0] == "blocks":
                        limit = int(qs.get("limit", ["25"])[0])
                        return self._json(200, {"network": net, "blocks": chain.recent_blocks(limit)})

                    if rest[0] == "block" and len(rest) >= 2:
                        key = urllib.parse.unquote("/".join(rest[1:]))
                        b = chain.get_block(key)
                        if not b:
                            return self._json(404, {"error": "block not found"})
                        return self._json(200, {"network": net, "block": b})

                    if rest[0] == "tx" and len(rest) >= 2:
                        key = urllib.parse.unquote("/".join(rest[1:]))
                        t = chain.find_tx(key)
                        if not t:
                            return self._json(404, {"error": "tx not found"})
                        return self._json(200, {"network": net, **t})

                    if rest[0] == "address" and len(rest) >= 2:
                        addr = urllib.parse.unquote(rest[1])
                        if not is_valid_address(addr) and addr != "HOWL_GENESIS_BURN":
                            # still allow lookup of known strings
                            pass
                        return self._json(200, {"network": net, **chain.address_history(addr)})

                return self._json(404, {"error": "not found"})

        return Handler

    def serve_forever(self) -> None:
        httpd = ThreadingHTTPServer((self.host, self.port), self.make_handler())
        print(f"Howlcoin Explorer → http://{self.host}:{self.port}/")
        for n in self.hub.list_networks():
            status = f"height {n['height']}" if n.get("online") else "offline"
            print(f"  · {n['id']}: {status} ({n['path']})")
        httpd.serve_forever()


def default_networks(
    public_dir: Optional[Path] = None,
    telegram_dir: Optional[Path] = None,
) -> Dict[str, Path]:
    import os

    pub = Path(os.environ.get("HOWL_PUBLIC_DATA", public_dir or DEFAULT_PUBLIC))
    tg = Path(os.environ.get("HOWL_TELEGRAM_DATA", telegram_dir or DEFAULT_TELEGRAM))
    return {"public": pub, "telegram": tg}


def main(
    host: str = "127.0.0.1",
    port: int = 42080,
    public_dir: Optional[str] = None,
    telegram_dir: Optional[str] = None,
) -> None:
    nets = default_networks(
        Path(public_dir) if public_dir else None,
        Path(telegram_dir) if telegram_dir else None,
    )
    hub = ExplorerHub(nets)
    ExplorerServer(hub, host=host, port=port).serve_forever()
