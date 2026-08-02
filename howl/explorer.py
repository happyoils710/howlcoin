"""
Howlcoin multi-chain block explorer (read-only).

Default network:
  - public → ~/.howlcoin or HOWL_PUBLIC_DATA (seed / main ledger)

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
<title>Howlscan — Howlcoin Block Explorer</title>
<link rel="icon" href="/assets/howlcoin-logo.jpg"/>
<style>
:root{
  --bg:#0c0f14; --bg2:#12161e; --panel:#161b26; --panel2:#1a2130;
  --border:#252d3d; --text:#e8edf7; --muted:#8b95a8; --link:#4da3ff;
  --green:#3dff9a; --amber:#ffb020; --red:#ff6b7a; --chip:#222a3a;
  --row:#121722; --rowh:#1a2233;
}
*{box-sizing:border-box}
body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);line-height:1.45}
a{color:var(--link);text-decoration:none}
a:hover{text-decoration:underline}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.84rem;word-break:break-all}
.muted{color:var(--muted)}
.topbar{display:flex;align-items:center;gap:16px;padding:12px 20px;border-bottom:1px solid var(--border);
  background:rgba(12,15,20,.92);backdrop-filter:blur(10px);position:sticky;top:0;z-index:20}
.topbar img{width:40px;height:40px;border-radius:50%;object-fit:cover;border:2px solid rgba(61,255,154,.45)}
.brand{font-weight:750;letter-spacing:.02em}
.brand span{color:var(--green)}
.brand small{display:block;font-weight:500;color:var(--muted);font-size:.75rem;margin-top:1px}
.nav{display:flex;gap:6px;flex-wrap:wrap;margin-left:8px}
.nav button,.chipbtn{border:1px solid var(--border);background:var(--chip);color:var(--text);
  border-radius:8px;padding:7px 12px;cursor:pointer;font:inherit;font-size:.85rem;font-weight:600}
.nav button.active,.chipbtn.active{background:rgba(77,163,255,.15);border-color:rgba(77,163,255,.45);color:#9cc9ff}
.nav button:hover,.chipbtn:hover{border-color:#3a4660}
.grow{flex:1}
.hero{padding:28px 20px 10px;max-width:1200px;margin:0 auto}
.hero h2{margin:0 0 6px;font-size:1.55rem;font-weight:750}
.searchwrap{max-width:1200px;margin:0 auto;padding:8px 20px 18px}
.searchbox{display:flex;gap:8px;background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:8px}
.searchbox input{flex:1;border:0;outline:0;background:transparent;color:var(--text);font:inherit;padding:10px 12px;min-width:0}
.searchbox button{border:0;border-radius:9px;background:linear-gradient(180deg,#2f6fed,#1f55c9);color:#fff;
  font-weight:700;padding:10px 18px;cursor:pointer}
.stats{max-width:1200px;margin:0 auto;padding:0 20px 18px;display:grid;
  grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.stat{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:14px 14px 12px}
.stat .k{font-size:.72rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);font-weight:700}
.stat .v{font-size:1.25rem;font-weight:750;margin-top:6px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.stat .s{font-size:.78rem;color:var(--muted);margin-top:4px}
.main{max-width:1200px;margin:0 auto;padding:0 20px 40px}
.cols{display:grid;grid-template-columns:1fr 1fr;gap:14px}
@media(max-width:900px){.cols{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--border);border-radius:14px;overflow:hidden}
.card h3{margin:0;padding:14px 16px;font-size:.95rem;border-bottom:1px solid var(--border);
  display:flex;justify-content:space-between;align-items:center}
.card h3 .more{font-size:.8rem;font-weight:600;color:var(--link)}
table{width:100%;border-collapse:collapse;font-size:.88rem}
th{text-align:left;padding:10px 14px;color:var(--muted);font-size:.72rem;text-transform:uppercase;
  letter-spacing:.05em;border-bottom:1px solid var(--border);background:var(--panel2)}
td{padding:11px 14px;border-bottom:1px solid var(--border);vertical-align:middle}
tr:last-child td{border-bottom:0}
tbody tr{background:var(--row);cursor:pointer}
tbody tr:hover{background:var(--rowh)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.72rem;font-weight:700}
.badge.ok{background:rgba(61,255,154,.12);color:var(--green)}
.badge.warn{background:rgba(255,176,32,.12);color:var(--amber)}
.badge.blue{background:rgba(77,163,255,.12);color:#9cc9ff}
.detail{padding:16px}
.kv{display:grid;grid-template-columns:160px 1fr;gap:8px 12px;margin:10px 0}
.kv .k{color:var(--muted);font-size:.85rem}
.back{border:1px solid var(--border);background:var(--chip);color:var(--text);border-radius:8px;
  padding:8px 12px;cursor:pointer;font:inherit;font-weight:600;margin-bottom:12px}
.footer{max-width:1200px;margin:0 auto;padding:10px 20px 40px;color:var(--muted);font-size:.8rem;
  display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px}
.err{padding:24px;color:var(--amber)}
.amount{font-weight:700;color:var(--green)}
.neg{color:var(--red)}
.skeleton{opacity:.55}
</style>
</head>
<body>
<div class="topbar">
  <img src="/assets/howlcoin-logo.jpg" alt="HOWL"/>
  <div class="brand" style="cursor:pointer" onclick="location.hash='#/'+net">Howl<span>scan</span><small>Howlcoin block explorer</small></div>
  <div class="nav" id="nav"></div>
  <div class="grow"></div>
  <button class="chipbtn" onclick="location.hash='#/'+net+'/richlist'">Richlist</button>
  <button class="chipbtn" onclick="location.hash='#/'+net+'/mempool'">Mempool</button>
  <button class="chipbtn" onclick="location.hash='#/'+net+'/block/0'">Genesis</button>
  <button class="chipbtn" onclick="loadHome()">Refresh</button>
</div>
<div id="app"></div>
<div class="footer">
  <div>Howlscan · Scrypt PoW · not financial advice ·
    <a href="#/public">Home</a> ·
    <a href="#/public/richlist">Richlist</a> ·
    <a href="#/public/mempool">Mempool</a> ·
    <a href="#/public/block/0">Genesis</a>
  </div>
  <div>API <span class="mono">/api/networks</span> · seed <span class="mono">147.182.223.204:42069</span> ·
    <a href="https://t.me/HowlMine_bot" target="_blank" rel="noopener">Bot</a> ·
    <a href="https://github.com/happyoils710/howlcoin" target="_blank" rel="noopener">Code</a>
  </div>
</div>
<script>
let net='public', networks=[];
const $ = s => document.querySelector(s);
const app = () => $('#app');
async function api(p){const r=await fetch(p); const j=await r.json(); if(!r.ok) throw new Error(j.error||r.statusText); return j}
function short(h,n=12){if(!h)return '—'; h=String(h); return h.length<=n?h:h.slice(0,n)+'…'}
function fmtAmt(a){if(a==null||a==='')return '—'; const n=Number(a)/1e8; return n.toLocaleString(undefined,{maximumFractionDigits:8})+' HOWL'}
function fmtCompact(n){
  // short number for stat boxes: 40499998 → 40.5M
  const x=Number(n);
  if(!isFinite(x)) return '—';
  const abs=Math.abs(x);
  const sign=x<0?'-':'';
  if(abs>=1e12) return sign+(abs/1e12).toFixed(2).replace(/\.?0+$/,'')+'T';
  if(abs>=1e9) return sign+(abs/1e9).toFixed(2).replace(/\.?0+$/,'')+'B';
  if(abs>=1e6) return sign+(abs/1e6).toFixed(2).replace(/\.?0+$/,'')+'M';
  if(abs>=1e3) return sign+(abs/1e3).toFixed(1).replace(/\.0$/,'')+'K';
  if(abs>=1) return sign+abs.toFixed(abs>=100?0:2).replace(/\.?0+$/,'');
  return sign+abs.toFixed(4).replace(/\.?0+$/,'');
}
function circulatingShort(s){
  // s.circulating is like "40499998.00000000 HOWL" or use howlies
  if(s.circulating_howlies!=null) return fmtCompact(Number(s.circulating_howlies)/1e8);
  const raw=String(s.circulating||'').replace(/ HOWL/i,'').replace(/,/g,'').trim();
  return fmtCompact(raw);
}
function fmtTime(ts){if(!ts)return '—'; try{return new Date(ts*1000).toLocaleString()}catch(e){return '—'}}

function ago(ts){if(!ts)return ''; const s=Math.max(0,Math.floor(Date.now()/1000-ts));
  if(s<60)return s+'s ago'; if(s<3600)return Math.floor(s/60)+'m ago'; if(s<86400)return Math.floor(s/3600)+'h ago'; return Math.floor(s/86400)+'d ago'}
function esc(s){return String(s??'').replace(/[&<>"']/g,c=>({ '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}
function copyBtn(text){
  const id='c'+Math.random().toString(36).slice(2,9);
  return `<button class="chipbtn" style="padding:2px 8px;font-size:.72rem;margin-left:6px" onclick="event.stopPropagation();navigator.clipboard.writeText('${esc(text).replace(/'/g,"\\'")}');this.textContent='Copied';setTimeout(()=>this.textContent='Copy',1200)">Copy</button>`;
}
function crumbs(parts){
  // parts: [{label, href?}]
  return `<div class="muted" style="margin:0 0 12px;font-size:.85rem">${parts.map((p,i)=>
    p.href?`<a href="${p.href}">${esc(p.label)}</a>`:esc(p.label)
  ).join(' <span style="opacity:.5">/</span> ')}</div>`;
}
function linkBlock(h){return `<a href="#/${net}/block/${encodeURIComponent(h)}">${esc(h)}</a>`}
function linkTx(t){if(!t)return '—'; return `<a class="mono" href="#/${net}/tx/${encodeURIComponent(t)}">${esc(short(t,14))}</a>`}
function linkAddr(a){if(!a||a==='HOWL_GENESIS_BURN') return `<span class="mono">${esc(a||'—')}</span>`;
  return `<a class="mono" href="#/${net}/address/${encodeURIComponent(a)}">${esc(short(a,12))}</a>`}

function renderNav(){
  $('#nav').innerHTML = networks.map(n=>`
    <button class="${n.id===net?'active':''}" onclick="switchNet('${n.id}')">
      ${esc(n.label)} ${n.online?`<span class="badge blue">#${n.height}</span>`:'<span class="badge warn">off</span>'}
    </button>`).join('');
}
function switchNet(id){net=id; location.hash=`#/${net}`}

async function loadNetworks(){
  const d=await api('/api/networks');
  networks=d.networks||[];
  if(!networks.find(n=>n.id===net)) net=(networks[0]&&networks[0].id)||'public';
  renderNav();
}

function shellSearch(extra=''){
  return `<div class="hero">
      <h2>Blockchain explorer for <span style="color:var(--green)">Howlcoin</span></h2>
      <p class="muted">Search blocks, transactions, and addresses across Howlcoin networks</p>
    </div>
    <div class="searchwrap">
      <div class="searchbox">
        <input id="q" placeholder="Search block height / hash, txid, or address (H…)" onkeydown="if(event.key==='Enter')doSearch()"/>
        <button onclick="doSearch()">Search</button>
      </div>
      ${extra}
    </div>`;
}

async function loadHome(){
  await loadNetworks();
  const s=await api(`/api/${net}/summary`);
  if(!s.online){
    app().innerHTML=shellSearch()+`<div class="main"><div class="card detail err">Chain <b>${esc(net)}</b> offline.<br><span class="mono">${esc(s.path||'')}</span><br>${esc(s.note||'')}</div></div>`;
    return;
  }
  const [blocks, txs]=await Promise.all([
    api(`/api/${net}/blocks?limit=15`),
    api(`/api/${net}/txs?limit=15`),
  ]);
  app().innerHTML = shellSearch() + `
  <div class="stats">
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/block/${s.height}'">
      <div class="k">Height</div><div class="v">${s.height}</div><div class="s">click → tip block</div></div>
    <div class="stat"><div class="k">Difficulty</div><div class="v">${s.difficulty}</div><div class="s">Scrypt PoW</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/richlist'" title="${esc(String(s.circulating||''))}">
      <div class="k">Circulating</div><div class="v">${esc(circulatingShort(s))}</div><div class="s">HOWL · click → richlist</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/mempool'">
      <div class="k">Mempool</div><div class="v">${s.mempool}</div><div class="s">click → pending txs</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/richlist'">
      <div class="k">Addresses</div><div class="v">${s.addresses??'—'}</div><div class="s">click → richlist</div></div>
    <div class="stat" style="cursor:pointer" onclick="location.hash='#/${net}/block/${encodeURIComponent(s.tip)}'">
      <div class="k">Tip hash</div><div class="v mono" style="font-size:.85rem">${esc(short(s.tip,14))}</div><div class="s">click → tip block</div></div>
  </div>
  <div class="main" style="padding-bottom:8px">
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:10px">
      <button class="chipbtn" onclick="location.hash='#/${net}/block/0'">Genesis #0</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/block/${s.height}'">Latest #${s.height}</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/richlist'">Top addresses</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/mempool'">Mempool (${s.mempool})</button>
      <button class="chipbtn" style="border-color:rgba(61,255,154,.45);color:var(--green)" onclick="location.hash='#/run'">Run a node / sync</button>
    </div>
  </div>
  <div class="main cols">
    <div class="card">
      <h3>Latest blocks <a class="more" href="#/${net}/block/${s.height}">tip →</a></h3>
      <table>
        <thead><tr><th>Height</th><th>Hash</th><th>Txs</th><th>Miner</th><th>Reward</th><th>Time</th></tr></thead>
        <tbody>
          ${(blocks.blocks||[]).map(b=>`<tr onclick="location.hash='#/${net}/block/${b.height}'">
            <td><b>${linkBlock(b.height)}</b></td>
            <td class="mono">${esc(short(b.hash,12))}</td>
            <td>${b.tx_count}</td>
            <td onclick="event.stopPropagation()">${linkAddr(b.miner)}</td>
            <td class="amount">${fmtAmt(b.reward)}</td>
            <td class="muted" title="${esc(fmtTime(b.timestamp))}">${ago(b.timestamp)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
    <div class="card">
      <h3>Latest transactions <a class="more" href="#/${net}/mempool">mempool →</a></h3>
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>Flow</th><th>Amount</th><th>Status</th></tr></thead>
        <tbody>
          ${(txs.transactions||[]).map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${linkTx(t.txid)}</td>
            <td><span class="badge ${t.type==='coinbase'?'ok':'blue'}">${t.type==='coinbase'?'reward':'transfer'}</span></td>
            <td class="mono" onclick="event.stopPropagation()">${t.type==='coinbase'?'new coins → '+linkAddr(t.to):linkAddr(t.from)+' → '+linkAddr(t.to)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${t.confirmed?`<span class="badge ok" onclick="event.stopPropagation();location.hash='#/${net}/block/${t.block_height}'">#${t.block_height}</span>`:`<span class="badge warn">mempool</span>`}</td>
          </tr>`).join('') || '<tr><td colspan="5" class="muted" style="padding:16px">No transactions yet</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>`;
}

async function showBlock(id){
  await loadNetworks();
  const d=await api(`/api/${net}/block/${encodeURIComponent(id)}`);
  const b=d.block; const txs=b.transactions||[];
  const cb=txs.find(t=>t.type==='coinbase');
  const h=b.height;
  const prev = h>0 ? h-1 : null;
  const next = h; // will link next height; may 404 if tip
  app().innerHTML=`<div class="main" style="padding-top:20px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:esc(net),href:'#/'+net},{label:'Block #'+h}])}
    <div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px">
      <button class="back" onclick="location.hash='#/${net}'">← Home</button>
      ${prev!=null?`<button class="chipbtn" onclick="location.hash='#/${net}/block/${prev}'">← Prev #${prev}</button>`:''}
      <button class="chipbtn" onclick="location.hash='#/${net}/block/${h+1}'">Next #${h+1} →</button>
      <button class="chipbtn" onclick="location.hash='#/${net}/block/0'">Genesis</button>
    </div>
    <div class="card detail">
      <div class="badge blue">Block</div>
      <h2 style="margin:8px 0 4px">Block #${b.height}</h2>
      <div class="mono">${esc(b.hash)}${copyBtn(b.hash)}</div>
      <div class="kv" style="margin-top:16px">
        <div class="k">Height</div><div>${b.height}</div>
        <div class="k">Timestamp</div><div>${esc(fmtTime(b.header.timestamp))} <span class="muted">(${ago(b.header.timestamp)})</span></div>
        <div class="k">Difficulty</div><div>${b.header.difficulty}</div>
        <div class="k">Nonce</div><div class="mono">${b.header.nonce}</div>
        <div class="k">Merkle root</div><div class="mono">${esc(b.header.merkle_root||'—')}</div>
        <div class="k">Previous</div><div class="mono">${b.height>0?linkBlock(b.header.prev_hash)+copyBtn(b.header.prev_hash):'— genesis'}</div>
        <div class="k">Miner</div><div>${linkAddr(cb&&cb.to)}</div>
        <div class="k">Reward</div><div class="amount">${fmtAmt(cb&&cb.amount)}</div>
        <div class="k">Transactions</div><div>${txs.length}</div>
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <h3>Transactions in this block</h3>
      <table>
        <thead><tr><th>Txid</th><th>Type</th><th>Flow</th><th>Amount</th></tr></thead>
        <tbody>
          ${txs.map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${t.txid?linkTx(t.txid):'—'}</td>
            <td><span class="badge ${t.type==='coinbase'?'ok':'blue'}">${t.type==='coinbase'?'mining reward':'transfer'}</span></td>
            <td class="mono" onclick="event.stopPropagation()">${t.type==='coinbase'?'new coins → '+linkAddr(t.to):linkAddr(t.from)+' → '+linkAddr(t.to)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
          </tr>`).join('')}
        </tbody>
      </table>
    </div>
  </div>`;
}

async function showTx(id){
  await loadNetworks();
  const d=await api(`/api/${net}/tx/${encodeURIComponent(id)}`);
  const t=d.tx;
  app().innerHTML=`<div class="main" style="padding-top:20px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:esc(net),href:'#/'+net},{label:'Transaction'}])}
    <button class="back" onclick="location.hash='#/${net}'">← Home</button>
    ${d.confirmed?`<button class="chipbtn" onclick="location.hash='#/${net}/block/${d.block_height}'">Open block #${d.block_height}</button>`:
      `<button class="chipbtn" onclick="location.hash='#/${net}/mempool'">View mempool</button>`}
    <div class="card detail" style="margin-top:12px">
      <div class="badge ${d.confirmed?'ok':'warn'}">${d.confirmed?'Confirmed':'Mempool'}</div>
      <h2 style="margin:8px 0 4px">Transaction</h2>
      <div class="mono">${esc(t.txid||id)}${copyBtn(t.txid||id)}</div>
      <div class="kv" style="margin-top:16px">
        <div class="k">Status</div><div>${d.confirmed?('Block '+linkBlock(d.block_height)):'Unconfirmed'}</div>
        <div class="k">Type</div><div>${esc(t.type||'transfer')}</div>
        ${t.type==='coinbase'?`
          <div class="k">Source</div><div>Mining reward (no sender — new HOWL created)</div>
          <div class="k">Miner (to)</div><div>${linkAddr(t.to)}</div>
          <div class="k">Reward</div><div class="amount">${fmtAmt(t.amount)}</div>
        `:`
          <div class="k">From</div><div>${linkAddr(t.from)}</div>
          <div class="k">To</div><div>${linkAddr(t.to)}</div>
          <div class="k">Amount</div><div class="amount">${fmtAmt(t.amount)}</div>
          <div class="k">Fee</div><div>${fmtAmt(t.fee||0)}</div>
          <div class="k">Nonce</div><div>${t.nonce??'—'}</div>
          <div class="k">Memo</div><div>${esc(t.memo||'—')}</div>
        `}
      </div>
    </div>
  </div>`;
}

async function showAddr(addr){
  await loadNetworks();
  const d=await api(`/api/${net}/address/${encodeURIComponent(addr)}`);
  app().innerHTML=`<div class="main" style="padding-top:20px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:esc(net),href:'#/'+net},{label:'Richlist',href:'#/'+net+'/richlist'},{label:'Address'}])}
    <button class="back" onclick="location.hash='#/${net}'">← Home</button>
    <button class="chipbtn" onclick="location.hash='#/${net}/richlist'">Richlist</button>
    <div class="card detail" style="margin-top:12px">
      <div class="badge blue">Address</div>
      <h2 style="margin:8px 0 4px">Wallet</h2>
      <div class="mono">${esc(d.address)}${copyBtn(d.address)}</div>
      <div class="kv" style="margin-top:16px">
        <div class="k">Balance</div><div class="amount" style="font-size:1.3rem">${esc(d.balance_fmt)}</div>
        <div class="k">Nonce</div><div>${d.nonce}</div>
        <div class="k">Shown txs</div><div>${d.tx_count}</div>
      </div>
    </div>
    <div class="card" style="margin-top:14px">
      <h3>Transaction history</h3>
      <table>
        <thead><tr><th>Dir</th><th>Txid</th><th>Amount</th><th>Block</th></tr></thead>
        <tbody>
          ${(d.transactions||[]).map(t=>`<tr>
            <td><span class="badge ${t.direction==='in'||t.type==='coinbase'?'ok':'warn'}">${esc(t.direction||t.type)}</span></td>
            <td>${t.txid?linkTx(t.txid):'—'}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${t.block_height!=null?linkBlock(t.block_height):'—'}</td>
          </tr>`).join('')||'<tr><td colspan="4" class="muted" style="padding:16px">No transactions</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>`;
}

async function showRichlist(){
  await loadNetworks();
  const d=await api(`/api/${net}/richlist?limit=50`);
  app().innerHTML=`<div class="main" style="padding-top:20px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:esc(net),href:'#/'+net},{label:'Richlist'}])}
    <button class="back" onclick="location.hash='#/${net}'">← Home</button>
    <div class="card" style="margin-top:12px">
      <h3>Top addresses by balance</h3>
      <table>
        <thead><tr><th>#</th><th>Address</th><th>Balance</th></tr></thead>
        <tbody>
          ${(d.richlist||[]).map(r=>`<tr onclick="location.hash='#/${net}/address/${encodeURIComponent(r.address)}'">
            <td>${r.rank}</td>
            <td onclick="event.stopPropagation()">${linkAddr(r.address)}</td>
            <td class="amount">${esc(r.balance_fmt)}</td>
          </tr>`).join('')||'<tr><td colspan="3" class="muted" style="padding:16px">No balances</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>`;
}

async function showMempool(){
  await loadNetworks();
  const d=await api(`/api/${net}/mempool`);
  app().innerHTML=`<div class="main" style="padding-top:20px">
    ${crumbs([{label:'Home',href:'#/'+net},{label:esc(net),href:'#/'+net},{label:'Mempool'}])}
    <button class="back" onclick="location.hash='#/${net}'">← Home</button>
    <div class="card" style="margin-top:12px">
      <h3>Mempool <span class="badge warn">${d.count||0} pending</span></h3>
      <table>
        <thead><tr><th>Txid</th><th>From → To</th><th>Amount</th><th>Fee</th></tr></thead>
        <tbody>
          ${(d.transactions||[]).map(t=>`<tr onclick="location.hash='#/${net}/tx/${encodeURIComponent(t.txid||'')}'">
            <td onclick="event.stopPropagation()">${linkTx(t.txid)}</td>
            <td class="mono" onclick="event.stopPropagation()">${linkAddr(t.from)} → ${linkAddr(t.to)}</td>
            <td class="amount">${fmtAmt(t.amount)}</td>
            <td>${fmtAmt(t.fee||0)}</td>
          </tr>`).join('')||'<tr><td colspan="4" class="muted" style="padding:16px">Mempool empty</td></tr>'}
        </tbody>
      </table>
    </div>
  </div>`;
}

async function showRunNode(){
  await loadNetworks();
  let height='?';
  try{
    const s=await api('/api/public/summary');
    if(s.online) height=s.height;
  }catch(e){}
  const clone = `git clone ${REPO}.git
cd howlcoin
python3 -m pip install -r requirements.txt`;
  const initCmd = `python3 -m howl init`;
  const syncCmd = `python3 -m howl node --connect ${SEED}`;
  const mineCmd = `python3 -m howl mine
# or continuous:
python3 -m howl mine --continuous`;
  const fullCmd = `git clone ${REPO}.git && cd howlcoin && python3 -m pip install -r requirements.txt && python3 -m howl init && python3 -m howl node --connect ${SEED}`;
  app().innerHTML=`<div class="main" style="padding-top:20px">
    ${crumbs([{label:'Home',href:'#/public'},{label:'Run a node'}])}
    <button class="back" onclick="location.hash='#/public'">← Home</button>
    <div class="card detail" style="margin-top:12px">
      <div class="badge ok">Sync to Howlcoin</div>
      <h2 style="margin:8px 0 6px">Run a node on your computer</h2>
      <p class="muted" style="margin:0 0 12px">
        Websites <b>cannot open your Terminal</b> (browser security). Copy these commands into
        Terminal (Mac/Linux) or a terminal on Windows, and you will sync to the live public chain
        (currently height <b>${esc(String(height))}</b>).
      </p>
      <div class="kv">
        <div class="k">Public seed</div><div class="mono">${esc(SEED)}${copyBtn(SEED)}</div>
        <div class="k">Explorer</div><div><a href="https://howlscan.org/">https://howlscan.org/</a></div>
        <div class="k">Source code</div><div><a href="${REPO}" target="_blank" rel="noopener">${esc(REPO)}</a></div>
        <div class="k">Telegram bot</div><div><a href="https://t.me/HowlMine_bot" target="_blank" rel="noopener">@HowlMine_bot</a></div>
      </div>
    </div>
    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">One-line setup + sync</h3>
      <p class="muted">Paste into Terminal, then leave the node running to stay synced.</p>
      ${cmdBox('Clone, install, init, connect to seed', fullCmd)}
    </div>
    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">Step by step</h3>
      ${cmdBox('1) Install Howlcoin', clone)}
      ${cmdBox('2) Create wallet + genesis (first time only)', initCmd)}
      ${cmdBox('3) Sync to the public network (run a node)', syncCmd)}
      <p class="muted">Dashboard while node runs: <span class="mono">http://127.0.0.1:42070/</span></p>
      ${cmdBox('4) Mine blocks (optional)', mineCmd)}
    </div>
    <div class="card detail" style="margin-top:14px">
      <h3 style="margin-top:0">Useful links</h3>
      <p>
        <a class="chipbtn" style="display:inline-block;text-decoration:none;margin:4px" href="${REPO}" target="_blank" rel="noopener">Open GitHub repo</a>
        <a class="chipbtn" style="display:inline-block;text-decoration:none;margin:4px" href="${REPO}/archive/refs/heads/main.zip" target="_blank" rel="noopener">Download ZIP</a>
        <a class="chipbtn" style="display:inline-block;text-decoration:none;margin:4px" href="https://t.me/HowlMine_bot" target="_blank" rel="noopener">Open Telegram bot</a>
        <button class="chipbtn" style="margin:4px" onclick="copyText('python3 -m howl node --connect ${SEED}', this)">Copy connect command</button>
      </p>
      <p class="muted" style="margin-bottom:0">After you connect, your node downloads blocks from the seed until your tip matches Howlscan.</p>
    </div>
  </div>`;
}

function doSearch(){
  const q=($('#q')&&$('#q').value||'').trim();
  if(!q) return loadHome();
  if(/^\d+$/.test(q)) { location.hash=`#/${net}/block/${q}`; return route(); }
  if(q.startsWith('H') && q.length>20){ location.hash=`#/${net}/address/${encodeURIComponent(q)}`; return route(); }
  // try as block hash then tx
  location.hash=`#/${net}/block/${encodeURIComponent(q)}`;
  route().catch(()=>{ location.hash=`#/${net}/tx/${encodeURIComponent(q)}`; return route(); })
    .catch(()=>{ app().innerHTML=`<div class="main"><div class="card detail err">Not found: <span class="mono">${esc(q)}</span></div></div>`; });
}

async function route(){
  const h=(location.hash||'').replace(/^#\/?/,'');
  const parts=h.split('/').filter(Boolean);
  if(parts[0] && networks.length && networks.find(n=>n.id===parts[0])){
    net=parts[0];
  }
  renderNav();
  try{
    if(parts.length>=3 && parts[1]==='block') return await showBlock(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='tx') return await showTx(decodeURIComponent(parts[2]));
    if(parts.length>=3 && parts[1]==='address') return await showAddr(decodeURIComponent(parts[2]));
    if(parts.length>=1 && (parts[0]==='run' || parts[0]==='node' || parts[0]==='sync')) return await showRunNode();
    if(parts.length>=2 && parts[1]==='richlist') return await showRichlist();
    if(parts.length>=2 && parts[1]==='mempool') return await showMempool();
    if(parts.length>=2 && parts[0]==='block') return await showBlock(decodeURIComponent(parts[1]));
    if(parts.length>=1 && parts[0]==='richlist') return await showRichlist();
    if(parts.length>=1 && parts[0]==='mempool') return await showMempool();
    return await loadHome();
  }catch(e){
    app().innerHTML=`<div class="main"><div class="card detail err">${esc(e.message)}</div></div>`;
  }
}
window.addEventListener('hashchange', ()=>route());
loadNetworks().then(route);
setInterval(()=>{ if(!(location.hash||'').includes('/block') && !(location.hash||'').includes('/tx') && !(location.hash||'').includes('/address')) loadHome().catch(()=>{}); }, 20000);
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

                    if rest[0] == "txs":
                        limit = int(qs.get("limit", ["25"])[0])
                        return self._json(
                            200,
                            {"network": net, "transactions": chain.recent_transactions(limit)},
                        )

                    if rest[0] == "mempool":
                        return self._json(
                            200,
                            {
                                "network": net,
                                "count": len(chain.mempool),
                                "transactions": chain.mempool_list(),
                            },
                        )

                    if rest[0] == "richlist":
                        limit = int(qs.get("limit", ["50"])[0])
                        return self._json(
                            200,
                            {"network": net, "richlist": chain.richlist(limit)},
                        )

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
    """Build network map. Telegram chain is optional and off by default."""
    import os

    pub = Path(os.environ.get("HOWL_PUBLIC_DATA", public_dir or DEFAULT_PUBLIC))
    nets: Dict[str, Path] = {"public": pub}
    # Only include telegram if explicitly requested via env or CLI path
    tg_env = os.environ.get("HOWL_TELEGRAM_DATA", "").strip()
    if telegram_dir:
        nets["telegram"] = Path(telegram_dir).expanduser()
    elif tg_env:
        nets["telegram"] = Path(tg_env).expanduser()
    return nets


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
