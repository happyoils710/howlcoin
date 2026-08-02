"""Howlcoin local wallet app + node dashboard (stdlib only).

Advanced wallet: send/receive, network fees to miners,
BIP39 backup, PIN + WebAuthn (Touch ID / Face ID) unlock.
"""

from __future__ import annotations

import json
import mimetypes
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional

from .blockchain import Blockchain
from .config import (
    COIN_NAME,
    DEFAULT_RPC_PORT,
    DEFAULT_TX_FEE_HOWLIES,
    MIN_TX_FEE_HOWLIES,
    TICKER,
    VERSION,
    WALLET_FILE,
)
from .network import Node
from .wallet import Wallet, format_howl, parse_howl

ASSETS_DIR = Path(__file__).resolve().parents[1] / "assets"

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"/>
<meta name="apple-mobile-web-app-capable" content="yes"/>
<meta name="theme-color" content="#0b1020"/>
<title>Howlcoin Wallet</title>
<link rel="icon" href="/assets/howlcoin-logo-meme-pup-coin.jpg"/>
<style>
:root{
  --bg:#070b14; --card:#121a2e; --card2:#0e1526; --border:#1e2a44;
  --text:#e8eef7; --muted:#8b9bb8; --green:#3dff9a; --amber:#ffb020;
  --danger:#ff5c7a; --blue:#4da3ff; --safe-b:env(safe-area-inset-bottom,0px);
  --safe-t:env(safe-area-inset-top,0px); --nav-h:64px;
}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;height:100%;font-family:-apple-system,BlinkMacSystemFont,Inter,ui-sans-serif,system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;
  background:var(--bg);color:var(--text);line-height:1.45}
button,input,select{font:inherit}
a{color:var(--blue);text-decoration:none}
.hidden{display:none!important}
/* Lock */
#lock{
  min-height:100%;display:flex;flex-direction:column;align-items:center;justify-content:center;
  padding:calc(24px + var(--safe-t)) 24px calc(24px + var(--safe-b));
  background:radial-gradient(800px 400px at 50% 0%,rgba(61,255,154,.12),transparent 60%),var(--bg);
}
#lock img{width:88px;height:88px;border-radius:50%;border:2px solid rgba(61,255,154,.5);
  box-shadow:0 0 40px rgba(61,255,154,.25);object-fit:cover;margin-bottom:16px}
#lock h1{margin:0;font-size:1.5rem;font-weight:750}
#lock p{color:var(--muted);margin:8px 0 24px;text-align:center;max-width:300px}
.lock-card{width:100%;max-width:360px;background:var(--card);border:1px solid var(--border);
  border-radius:20px;padding:20px}
.lock-card input{width:100%;padding:14px 16px;border-radius:12px;border:1px solid var(--border);
  background:var(--card2);color:var(--text);font-size:16px;margin-bottom:12px}
.btn{display:flex;align-items:center;justify-content:center;gap:8px;width:100%;
  padding:14px 16px;border-radius:14px;border:1px solid rgba(61,255,154,.4);
  background:linear-gradient(180deg,#1f3d32,#163028);color:var(--green);
  font-weight:700;cursor:pointer;min-height:50px;margin-bottom:10px}
.btn:active{opacity:.85}
.btn.secondary{background:var(--card2);color:var(--text);border-color:var(--border)}
.btn.danger{background:#2a1520;color:var(--danger);border-color:rgba(255,92,122,.4)}
.btn:disabled{opacity:.5;cursor:wait}
.err{color:var(--danger);font-size:.88rem;margin:0 0 10px;min-height:1.2em}
/* App shell */
#app{min-height:100%;display:flex;flex-direction:column;padding-bottom:calc(var(--nav-h) + var(--safe-b))}
.top{
  position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:12px;
  padding:12px 16px;padding-top:calc(12px + var(--safe-t));
  background:rgba(7,11,20,.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);
}
.top img{width:40px;height:40px;border-radius:50%;object-fit:cover;border:2px solid rgba(61,255,154,.4)}
.top .title{font-weight:750;font-size:1.05rem}
.top .title span{color:var(--green)}
.top .sub{font-size:.72rem;color:var(--muted)}
.grow{flex:1}
.chip{border:1px solid var(--border);background:var(--card2);color:var(--muted);
  border-radius:999px;padding:6px 10px;font-size:.75rem;font-weight:650}
.chip.ok{color:var(--green);border-color:rgba(61,255,154,.35)}
.content{flex:1;padding:16px;max-width:520px;margin:0 auto;width:100%}
.hero-bal{text-align:center;padding:20px 8px 8px}
.hero-bal .lbl{color:var(--muted);font-size:.85rem;font-weight:600;text-transform:uppercase;letter-spacing:.06em}
.hero-bal .amt{font-size:2.2rem;font-weight:800;color:var(--green);margin:8px 0 4px;word-break:break-all}
.hero-bal .addr{font-family:ui-monospace,Menlo,monospace;font-size:.78rem;color:var(--muted);
  word-break:break-all;padding:0 12px}
.actions{display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;margin:18px 0}
.action{display:flex;flex-direction:column;align-items:center;gap:6px;padding:14px 8px;
  background:var(--card);border:1px solid var(--border);border-radius:16px;cursor:pointer;color:var(--text)}
.action .ico{font-size:1.35rem}
.action .t{font-size:.78rem;font-weight:700}
.card{background:var(--card);border:1px solid var(--border);border-radius:16px;padding:16px;margin-bottom:12px}
.card h2{margin:0 0 12px;font-size:.75rem;text-transform:uppercase;letter-spacing:.07em;color:var(--muted)}
.card h3{margin:0 0 8px;font-size:1rem}
label{display:block;font-size:.78rem;color:var(--muted);font-weight:650;margin:0 0 6px}
.field{width:100%;padding:13px 14px;border-radius:12px;border:1px solid var(--border);
  background:var(--card2);color:var(--text);font-size:16px;margin-bottom:12px}
.field:focus{outline:2px solid rgba(61,255,154,.35);border-color:rgba(61,255,154,.45)}
.row{display:flex;gap:8px;align-items:center}
.row .field{margin-bottom:0;flex:1}
.hint{font-size:.8rem;color:var(--muted);margin:-4px 0 12px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.82rem;word-break:break-all}
.list-item{display:flex;justify-content:space-between;gap:10px;padding:12px 0;border-bottom:1px solid var(--border)}
.list-item:last-child{border-bottom:0}
.list-item .l{min-width:0}
.list-item .r{text-align:right;flex-shrink:0;font-weight:700;color:var(--green)}
.list-item .m{color:var(--muted);font-size:.78rem;margin-top:2px}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:.7rem;font-weight:700;
  background:rgba(77,163,255,.12);color:#9cc9ff}
.badge.ok{background:rgba(61,255,154,.12);color:var(--green)}
.badge.warn{background:rgba(255,176,32,.12);color:var(--amber)}
.qr-wrap{display:flex;justify-content:center;padding:12px;background:#fff;border-radius:16px;margin:12px 0}
.qr-wrap img{width:200px;height:200px;display:block}
.words{display:grid;grid-template-columns:1fr 1fr;gap:8px;margin:12px 0}
.word{background:var(--card2);border:1px solid var(--border);border-radius:10px;padding:10px;
  font-family:ui-monospace,monospace;font-size:.85rem}
.word b{color:var(--muted);margin-right:6px;font-weight:600}
.toast{position:fixed;left:50%;bottom:calc(var(--nav-h) + var(--safe-b) + 16px);transform:translateX(-50%);
  background:#1a2438;border:1px solid var(--border);color:var(--text);padding:10px 16px;border-radius:12px;
  font-size:.88rem;font-weight:600;z-index:50;opacity:0;pointer-events:none;transition:opacity .2s;max-width:90%}
.toast.show{opacity:1}
.bottom{
  position:fixed;left:0;right:0;bottom:0;z-index:30;
  display:grid;grid-template-columns:repeat(5,1fr);
  background:rgba(7,11,20,.96);backdrop-filter:blur(14px);border-top:1px solid var(--border);
  padding:6px 2px calc(6px + var(--safe-b));
}
.tab{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:2px;
  border:0;background:transparent;color:var(--muted);font-size:.62rem;font-weight:650;
  padding:6px 2px;min-height:52px;cursor:pointer}
.tab .ico{font-size:1.15rem}
.tab.active{color:var(--green)}
.page{display:none}
.page.active{display:block}
.fee-box{display:flex;justify-content:space-between;align-items:center;padding:12px 14px;
  background:var(--card2);border-radius:12px;border:1px solid var(--border);margin-bottom:12px}
.fee-box .v{font-weight:750;color:var(--amber)}
.total-box{display:flex;justify-content:space-between;padding:4px 2px 12px;font-weight:700}
.seg{display:flex;gap:6px;margin-bottom:12px}
.seg button{flex:1;padding:10px;border-radius:10px;border:1px solid var(--border);
  background:var(--card2);color:var(--muted);font-weight:650;cursor:pointer}
.seg button.on{border-color:rgba(61,255,154,.45);color:var(--green);background:rgba(61,255,154,.08)}
@media(min-width:720px){
  .content{max-width:560px;padding:24px}
  .hero-bal .amt{font-size:2.6rem}
}
</style>
</head>
<body>
<!-- LOCK / ONBOARD -->
<div id="lock">
  <img src="/assets/howlcoin-logo-meme-pup-coin.jpg" alt="HOWL"/>
  <h1>Howlcoin Wallet</h1>
  <p id="lockSub">Secure wallet for HOWL · send, receive, backup</p>
  <div class="lock-card" id="lockCard">
    <div class="err" id="lockErr"></div>
    <div id="lockSetup" class="hidden">
      <label>Create a PIN (4–8 digits)</label>
      <input class="field" id="setupPin" type="password" inputmode="numeric" maxlength="8" placeholder="••••" autocomplete="new-password"/>
      <label>Confirm PIN</label>
      <input class="field" id="setupPin2" type="password" inputmode="numeric" maxlength="8" placeholder="••••" autocomplete="new-password"/>
      <button class="btn" type="button" onclick="finishSetup()">Create wallet lock</button>
      <button class="btn secondary" type="button" id="bioSetupBtn" onclick="setupBiometric()">Enable Touch ID / Face ID</button>
      <p class="hint">PIN + biometrics protect this app on your device. Your seed is stored in the local node wallet file.</p>
    </div>
    <div id="lockUnlock" class="hidden">
      <button class="btn" type="button" id="bioUnlockBtn" onclick="unlockBiometric()">Unlock with Face ID / Touch ID</button>
      <label>Or enter PIN</label>
      <input class="field" id="unlockPin" type="password" inputmode="numeric" maxlength="8" placeholder="PIN" autocomplete="current-password"
        onkeydown="if(event.key==='Enter')unlockPin()"/>
      <button class="btn secondary" type="button" onclick="unlockPin()">Unlock</button>
    </div>
  </div>
</div>

<!-- APP -->
<div id="app" class="hidden">
  <div class="top">
    <img src="/assets/howlcoin-logo-meme-pup-coin.jpg" alt=""/>
    <div>
      <div class="title">Howl<span>coin</span></div>
      <div class="sub" id="netSub">Local wallet</div>
    </div>
    <div class="grow"></div>
    <span class="chip" id="liveChip">● …</span>
    <button class="chip" type="button" onclick="lockNow()" title="Lock">Lock</button>
  </div>
  <div class="content">
    <!-- HOME -->
    <div class="page active" id="page-home">
      <div class="hero-bal">
        <div class="lbl">Available balance</div>
        <div class="amt" id="bal">—</div>
        <div class="addr" id="addr">—</div>
      </div>
      <div class="actions">
        <button class="action" type="button" onclick="showPage('send')"><span class="ico">↑</span><span class="t">Send</span></button>
        <button class="action" type="button" onclick="showPage('receive')"><span class="ico">↓</span><span class="t">Receive</span></button>
        <button class="action" type="button" onclick="showPage('security')"><span class="ico">🛡</span><span class="t">Backup</span></button>
      </div>
      <div class="card">
        <h2>Network</h2>
        <div class="list-item"><div class="l">Height</div><div class="r" id="height" style="color:var(--text)">—</div></div>
        <div class="list-item"><div class="l">Peers</div><div class="r" id="peers" style="color:var(--text)">—</div></div>
        <div class="list-item"><div class="l">Mempool</div><div class="r" id="mempool" style="color:var(--text)">—</div></div>
        <div class="list-item"><div class="l">Min fee</div><div class="r" id="minFee" style="color:var(--amber)">1 HOWL</div></div>
      </div>
      <div class="card">
        <h2>Recent activity</h2>
        <div id="activity"><div class="hint">Loading…</div></div>
      </div>
    </div>

    <!-- SEND -->
    <div class="page" id="page-send">
      <div class="card">
        <h2>Send HOWL</h2>
        <label>To address</label>
        <input class="field" id="sendTo" placeholder="H…" autocomplete="off" spellcheck="false"/>
        <label>Amount</label>
        <input class="field" id="sendAmt" placeholder="0.00" inputmode="decimal"/>
        <div class="seg" id="feeSeg">
          <button type="button" class="on" data-fee="1" onclick="setFeePreset(1,this)">Standard · 1 HOWL</button>
          <button type="button" data-fee="5" onclick="setFeePreset(5,this)">Fast · 5 HOWL</button>
        </div>
        <label>Network fee (paid to miner)</label>
        <input class="field" id="sendFee" value="1" inputmode="decimal"/>
        <p class="hint">Fees go to the miner who includes your transaction — helps secure the network.</p>
        <div class="fee-box"><span>You send</span><span class="v" id="sendPreview">—</span></div>
        <div class="total-box"><span>Total debit</span><span id="sendTotal">—</span></div>
        <label>Memo (optional)</label>
        <input class="field" id="sendMemo" placeholder="Note"/>
        <button class="btn" type="button" id="sendBtn" onclick="doSend()">Review &amp; send</button>
        <p class="hint" id="sendStatus"></p>
      </div>
    </div>

    <!-- RECEIVE -->
    <div class="page" id="page-receive">
      <div class="card" style="text-align:center">
        <h2>Receive HOWL</h2>
        <h3>Your address</h3>
        <div class="qr-wrap"><img id="qr" alt="QR code"/></div>
        <div class="mono" id="recvAddr" style="margin:8px 0 14px">—</div>
        <button class="btn" type="button" onclick="copyRecv()">Copy address</button>
        <button class="btn secondary" type="button" onclick="newAddr()">Generate new address</button>
        <p class="hint">Only send HOWL on the Howlcoin network to this address.</p>
      </div>
      <div class="card">
        <h2>Addresses in this wallet</h2>
        <div id="addrList"></div>
      </div>
    </div>

    <!-- SECURITY -->
    <div class="page" id="page-security">
      <div class="card">
        <h2>Backup phrase</h2>
        <p class="hint">Your 12-word recovery phrase restores this wallet. Never share it. Never enter it on a website you don't trust.</p>
        <button class="btn secondary" type="button" onclick="revealSeed()">Reveal recovery phrase</button>
        <div id="seedBox" class="hidden">
          <div class="words" id="seedWords"></div>
          <button class="btn" type="button" onclick="copySeed()">Copy phrase</button>
          <button class="btn secondary" type="button" onclick="hideSeed()">Hide phrase</button>
        </div>
        <p class="hint" id="seedMeta"></p>
      </div>
      <div class="card">
        <h2>App lock</h2>
        <button class="btn secondary" type="button" onclick="setupBiometric()">Enable / re-bind Face ID · Touch ID</button>
        <button class="btn secondary" type="button" onclick="changePinPrompt()">Change PIN</button>
        <button class="btn danger" type="button" onclick="lockNow()">Lock wallet now</button>
      </div>
      <div class="card">
        <h2>Safety</h2>
        <p class="hint" style="margin:0">
          Keys live in your local node data directory (<span class="mono">~/.howlcoin/wallet.json</span>).
          Biometrics only lock this browser app — keep your machine secure and back up your phrase offline.
        </p>
      </div>
    </div>

    <!-- MORE / NODE -->
    <div class="page" id="page-more">
      <div class="card">
        <h2>Mine</h2>
        <p class="hint">Earn block rewards + claim fees from transfers you include.</p>
        <div class="row" style="margin-bottom:12px">
          <button class="btn" type="button" id="mineBtn" onclick="mine(1)" style="margin:0">Mine 1 block</button>
          <button class="btn secondary" type="button" onclick="mine(3)" style="margin:0">Mine 3</button>
        </div>
        <p class="hint" id="mineStatus"></p>
      </div>
      <div class="card">
        <h2>Connect peer</h2>
        <div class="row" style="margin-bottom:12px">
          <input class="field" id="peer" placeholder="147.182.223.204:42069" style="margin:0"/>
        </div>
        <button class="btn secondary" type="button" onclick="connectPeer()">Connect</button>
        <div id="peerList" style="margin-top:12px"></div>
      </div>
      <div class="card">
        <h2>About</h2>
        <div class="list-item"><div class="l">Version</div><div class="r" id="ver" style="color:var(--text)">—</div></div>
        <div class="list-item"><div class="l">Explorer</div><div class="r" style="color:var(--text)"><a href="https://howlscan.org" target="_blank" rel="noopener">howlscan.org</a></div></div>
        <div class="list-item"><div class="l">White paper</div><div class="r" style="color:var(--text)"><a href="https://howlscan.org/whitepaper" target="_blank" rel="noopener">Open</a></div></div>
      </div>
    </div>
  </div>
  <nav class="bottom">
    <button type="button" class="tab active" data-p="home" onclick="showPage('home')"><span class="ico">⌂</span>Home</button>
    <button type="button" class="tab" data-p="send" onclick="showPage('send')"><span class="ico">↑</span>Send</button>
    <button type="button" class="tab" data-p="receive" onclick="showPage('receive')"><span class="ico">↓</span>Receive</button>
    <button type="button" class="tab" data-p="security" onclick="showPage('security')"><span class="ico">🛡</span>Security</button>
    <button type="button" class="tab" data-p="more" onclick="showPage('more')"><span class="ico">☰</span>Node</button>
  </nav>
</div>
<div class="toast" id="toast"></div>

<script>
const LS_PIN = 'howl_wallet_pin_hash_v1';
const LS_BIO = 'howl_wallet_bio_v1';
const LS_UNLOCKED = 'howl_wallet_session';
let state = null;
let feePreset = 1;

async function shaPin(pin){
  const data = new TextEncoder().encode('howlcoin-pin:' + pin);
  const buf = await crypto.subtle.digest('SHA-256', data);
  return Array.from(new Uint8Array(buf)).map(b=>b.toString(16).padStart(2,'0')).join('');
}
function toast(msg){
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(()=>t.classList.remove('show'), 2200);
}
function lockErr(msg){ document.getElementById('lockErr').textContent = msg || ''; }

function hasPin(){ return !!localStorage.getItem(LS_PIN); }
function isSession(){
  const s = sessionStorage.getItem(LS_UNLOCKED);
  return s === '1';
}
function setSession(on){
  if(on) sessionStorage.setItem(LS_UNLOCKED,'1');
  else sessionStorage.removeItem(LS_UNLOCKED);
}

function showApp(){
  document.getElementById('lock').classList.add('hidden');
  document.getElementById('app').classList.remove('hidden');
  refresh();
  setInterval(refresh, 4000);
}
function showLock(){
  document.getElementById('app').classList.add('hidden');
  document.getElementById('lock').classList.remove('hidden');
  const setup = !hasPin();
  document.getElementById('lockSetup').classList.toggle('hidden', !setup);
  document.getElementById('lockUnlock').classList.toggle('hidden', setup);
  document.getElementById('lockSub').textContent = setup
    ? 'Create a PIN to protect this wallet app on your device.'
    : 'Unlock with Face ID, Touch ID, or your PIN.';
  const bio = localStorage.getItem(LS_BIO);
  document.getElementById('bioUnlockBtn').classList.toggle('hidden', !bio);
  document.getElementById('bioSetupBtn').classList.toggle('hidden', !window.PublicKeyCredential);
}

async function finishSetup(){
  lockErr('');
  const a = document.getElementById('setupPin').value.trim();
  const b = document.getElementById('setupPin2').value.trim();
  if(!/^\d{4,8}$/.test(a)){ lockErr('PIN must be 4–8 digits'); return; }
  if(a !== b){ lockErr('PINs do not match'); return; }
  localStorage.setItem(LS_PIN, await shaPin(a));
  setSession(true);
  toast('PIN created');
  showApp();
}
async function unlockPin(){
  lockErr('');
  const pin = document.getElementById('unlockPin').value.trim();
  const hash = await shaPin(pin);
  if(hash !== localStorage.getItem(LS_PIN)){ lockErr('Wrong PIN'); return; }
  setSession(true);
  showApp();
}
function lockNow(){
  setSession(false);
  showLock();
  toast('Wallet locked');
}
async function changePinPrompt(){
  const cur = prompt('Current PIN');
  if(cur==null) return;
  if(await shaPin(cur) !== localStorage.getItem(LS_PIN)){ toast('Wrong PIN'); return; }
  const n = prompt('New PIN (4–8 digits)');
  if(n==null) return;
  if(!/^\d{4,8}$/.test(n)){ toast('Invalid PIN'); return; }
  localStorage.setItem(LS_PIN, await shaPin(n));
  toast('PIN updated');
}

/* WebAuthn — platform authenticator (Touch ID / Face ID where available) */
function bufToB64(buf){
  return btoa(String.fromCharCode(...new Uint8Array(buf)));
}
function b64ToBuf(b64){
  const s = atob(b64);
  const u = new Uint8Array(s.length);
  for(let i=0;i<s.length;i++) u[i]=s.charCodeAt(i);
  return u.buffer;
}
async function setupBiometric(){
  lockErr('');
  if(!window.PublicKeyCredential){ toast('Biometrics not supported in this browser'); return; }
  try{
    const challenge = crypto.getRandomValues(new Uint8Array(32));
    const userId = crypto.getRandomValues(new Uint8Array(16));
    const cred = await navigator.credentials.create({
      publicKey: {
        challenge,
        rp: { name: 'Howlcoin Wallet', id: location.hostname || 'localhost' },
        user: { id: userId, name: 'howl-wallet', displayName: 'Howlcoin' },
        pubKeyCredParams: [{alg:-7, type:'public-key'},{alg:-257, type:'public-key'}],
        authenticatorSelection: { authenticatorAttachment:'platform', userVerification:'required', residentKey:'preferred' },
        timeout: 60000,
      }
    });
    if(!cred) throw new Error('No credential');
    localStorage.setItem(LS_BIO, JSON.stringify({
      id: bufToB64(cred.rawId),
      // rawId is enough for get() allowCredentials
    }));
    toast('Face ID / Touch ID enabled');
  }catch(e){
    lockErr(e.message || String(e));
  }
}
async function unlockBiometric(){
  lockErr('');
  const raw = localStorage.getItem(LS_BIO);
  if(!raw){ lockErr('Biometric not set up'); return; }
  try{
    const { id } = JSON.parse(raw);
    const challenge = crypto.getRandomValues(new Uint8Array(32));
    const assertion = await navigator.credentials.get({
      publicKey: {
        challenge,
        allowCredentials: [{ id: b64ToBuf(id), type:'public-key' }],
        userVerification: 'required',
        timeout: 60000,
      }
    });
    if(!assertion) throw new Error('Cancelled');
    setSession(true);
    showApp();
  }catch(e){
    lockErr(e.message || 'Biometric unlock failed');
  }
}

function showPage(name){
  document.querySelectorAll('.page').forEach(p=>p.classList.toggle('active', p.id==='page-'+name));
  document.querySelectorAll('.tab').forEach(t=>t.classList.toggle('active', t.dataset.p===name));
  if(name==='receive' && state) updateReceive();
  if(name==='send') updateSendPreview();
}

async function api(path, opts){
  const r = await fetch(path, opts);
  const j = await r.json().catch(()=>({}));
  if(!r.ok) throw new Error(j.error || r.statusText);
  return j;
}

function setFeePreset(n, el){
  feePreset = n;
  document.getElementById('sendFee').value = String(n);
  document.querySelectorAll('#feeSeg button').forEach(b=>b.classList.toggle('on', b===el));
  updateSendPreview();
}
function updateSendPreview(){
  const amt = parseFloat(document.getElementById('sendAmt').value)||0;
  const fee = parseFloat(document.getElementById('sendFee').value)||0;
  document.getElementById('sendPreview').textContent = amt.toLocaleString(undefined,{maximumFractionDigits:8}) + ' HOWL';
  document.getElementById('sendTotal').textContent = (amt+fee).toLocaleString(undefined,{maximumFractionDigits:8}) + ' HOWL';
}
['sendAmt','sendFee'].forEach(id=>{
  document.addEventListener('input', e=>{ if(e.target && e.target.id===id) updateSendPreview(); });
});

async function refresh(){
  try{
    const s = await api('/api/status');
    state = s;
    document.getElementById('bal').textContent = s.wallet.balance.replace(' HOWL','') + ' HOWL';
    document.getElementById('addr').textContent = s.wallet.address;
    document.getElementById('height').textContent = s.height;
    document.getElementById('peers').textContent = (s.peers||[]).length;
    document.getElementById('mempool').textContent = s.mempool;
    document.getElementById('minFee').textContent = s.fees?.min_fee || '1.00000000 HOWL';
    document.getElementById('ver').textContent = s.version;
    document.getElementById('netSub').textContent = s.node_running ? 'Node · height '+s.height : 'Wallet';
    const chip = document.getElementById('liveChip');
    chip.textContent = s.node_running ? '● LIVE' : '○ OFF';
    chip.className = 'chip' + (s.node_running ? ' ok' : '');
    if(document.getElementById('sendFee').value==='' || document.getElementById('sendFee').dataset.init!=='1'){
      const def = s.fees?.default_fee_howl ?? 1;
      document.getElementById('sendFee').value = String(def);
      document.getElementById('sendFee').dataset.init = '1';
    }
    updateReceive();
    updateActivity(s.activity||[]);
    updatePeers(s.peers||[]);
    updateAddrList(s.wallet.addresses||[]);
    updateSendPreview();
  }catch(e){
    document.getElementById('liveChip').textContent = '● ERR';
  }
}
function updateReceive(){
  if(!state) return;
  const a = state.wallet.address;
  document.getElementById('recvAddr').textContent = a;
  document.getElementById('qr').src = 'https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=' + encodeURIComponent(a);
}
function updateAddrList(addrs){
  const el = document.getElementById('addrList');
  if(!addrs.length){ el.innerHTML = '<div class="hint">No addresses</div>'; return; }
  el.innerHTML = addrs.map((x,i)=>`<div class="list-item">
    <div class="l"><div>${i===0?'Primary':'Address '+(i+1)}</div><div class="m mono">${x.address}</div></div>
    <div class="r">${x.balance.replace(' HOWL','')}</div>
  </div>`).join('');
}
function updateActivity(items){
  const el = document.getElementById('activity');
  if(!items.length){ el.innerHTML = '<div class="hint">No transactions yet — mine or receive HOWL</div>'; return; }
  el.innerHTML = items.map(t=>{
    const dir = t.direction === 'in' || t.type==='coinbase' ? 'Received' : 'Sent';
    const badge = t.type==='coinbase' ? 'reward' : (t.confirmed ? 'ok' : 'pending');
    return `<div class="list-item">
      <div class="l">
        <div>${dir} <span class="badge ${t.confirmed||t.type==='coinbase'?'ok':'warn'}">${badge}</span></div>
        <div class="m">${t.block_height!=null?'Block #'+t.block_height:'Mempool'} · ${(t.txid||'').slice(0,12)}…</div>
      </div>
      <div class="r">${t.amount_fmt || ''}</div>
    </div>`;
  }).join('');
}
function updatePeers(peers){
  const el = document.getElementById('peerList');
  if(!peers.length){ el.innerHTML = '<div class="hint">No peers — connect the public seed</div>'; return; }
  el.innerHTML = peers.map(p=>`<div class="list-item">
    <div class="l mono">${p.host}:${p.port}</div>
    <div class="r" style="color:${p.alive?'var(--green)':'var(--amber)'}">${p.alive?'#'+p.height:'down'}</div>
  </div>`).join('');
}

async function doSend(){
  const to = document.getElementById('sendTo').value.trim();
  const amount = document.getElementById('sendAmt').value.trim();
  const fee = document.getElementById('sendFee').value.trim();
  const memo = document.getElementById('sendMemo').value.trim();
  const st = document.getElementById('sendStatus');
  st.textContent = '';
  if(!to || !amount){ st.textContent = 'Enter address and amount'; return; }
  const total = (parseFloat(amount)||0) + (parseFloat(fee)||0);
  if(!confirm(`Send ${amount} HOWL to\\n${to}\\n\\nNetwork fee: ${fee} HOWL (to miner)\\nTotal debit: ${total} HOWL`)) return;
  const btn = document.getElementById('sendBtn');
  btn.disabled = true;
  try{
    const j = await api('/api/send', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({to, amount, fee, memo})
    });
    st.textContent = 'Queued ' + (j.txid||'').slice(0,16) + '… — mine or wait for a miner to confirm';
    toast('Transfer queued');
    document.getElementById('sendAmt').value = '';
    refresh();
  }catch(e){
    st.textContent = e.message;
  }finally{
    btn.disabled = false;
  }
}
function copyRecv(){
  const a = document.getElementById('recvAddr').textContent;
  navigator.clipboard.writeText(a).then(()=>toast('Address copied'));
}
async function newAddr(){
  try{
    const j = await api('/api/wallet/new-address', {method:'POST', headers:{'Content-Type':'application/json'}, body:'{}'});
    toast('New address: ' + j.address.slice(0,10) + '…');
    refresh();
  }catch(e){ toast(e.message); }
}
async function revealSeed(){
  if(!confirm('Only reveal your recovery phrase if no one can see your screen. Continue?')) return;
  try{
    const j = await api('/api/wallet/mnemonic');
    if(!j.has_mnemonic){
      document.getElementById('seedMeta').textContent = j.note || 'No BIP39 phrase (imported key wallet).';
      return;
    }
    const words = j.mnemonic.split(/\s+/);
    document.getElementById('seedWords').innerHTML = words.map((w,i)=>
      `<div class="word"><b>${i+1}.</b>${w}</div>`).join('');
    document.getElementById('seedBox').classList.remove('hidden');
    document.getElementById('seedMeta').textContent = 'Path ' + (j.derivation||'') + ' · write these words on paper offline.';
  }catch(e){ toast(e.message); }
}
function hideSeed(){
  document.getElementById('seedBox').classList.add('hidden');
  document.getElementById('seedWords').innerHTML = '';
}
function copySeed(){
  const words = [...document.querySelectorAll('#seedWords .word')].map(el=>el.textContent.replace(/^\d+\.\s*/,'').trim());
  navigator.clipboard.writeText(words.join(' ')).then(()=>toast('Phrase copied — clear clipboard after backup'));
}
async function mine(n){
  const btn = document.getElementById('mineBtn');
  const st = document.getElementById('mineStatus');
  btn.disabled = true; st.textContent = 'Mining…';
  try{
    const j = await api('/api/mine', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({blocks:n})});
    st.textContent = 'Mined to height ' + j.height + ' · ' + j.balance;
    toast('Block mined');
    refresh();
  }catch(e){ st.textContent = e.message; }
  finally{ btn.disabled = false; }
}
async function connectPeer(){
  const peer = document.getElementById('peer').value.trim() || '147.182.223.204:42069';
  try{
    const j = await api('/api/connect', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({peer})});
    toast(j.message||'Connecting');
    refresh();
  }catch(e){ toast(e.message); }
}

// boot
if(isSession() && hasPin()) showApp();
else showLock();
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

    def _wallet_activity(self, limit: int = 30) -> List[Dict[str, Any]]:
        """Recent txs involving any address in this wallet."""
        addrs = set(self.wallet.list_addresses())
        items: List[Dict[str, Any]] = []
        # mempool first
        for tx in reversed(self.chain.mempool):
            if tx.get("type") == "coinbase":
                continue
            frm, to = tx.get("from"), tx.get("to")
            if frm not in addrs and to not in addrs:
                continue
            direction = "out" if frm in addrs else "in"
            items.append(
                {
                    "txid": tx.get("txid"),
                    "type": tx.get("type", "transfer"),
                    "direction": direction,
                    "amount": tx.get("amount", 0),
                    "amount_fmt": format_howl(int(tx.get("amount", 0))),
                    "fee": tx.get("fee", 0),
                    "confirmed": False,
                    "block_height": None,
                }
            )
        # confirmed (newest blocks first)
        for block in reversed(self.chain.blocks):
            h = block.get("height", 0)
            for tx in reversed(block.get("transactions") or []):
                if tx.get("type") == "coinbase":
                    if tx.get("to") in addrs:
                        items.append(
                            {
                                "txid": tx.get("txid"),
                                "type": "coinbase",
                                "direction": "in",
                                "amount": tx.get("amount", 0),
                                "amount_fmt": format_howl(int(tx.get("amount", 0))),
                                "fee": 0,
                                "confirmed": True,
                                "block_height": h,
                            }
                        )
                    continue
                frm, to = tx.get("from"), tx.get("to")
                if frm not in addrs and to not in addrs:
                    continue
                direction = "out" if frm in addrs else "in"
                items.append(
                    {
                        "txid": tx.get("txid"),
                        "type": "transfer",
                        "direction": direction,
                        "amount": tx.get("amount", 0),
                        "amount_fmt": format_howl(int(tx.get("amount", 0))),
                        "fee": tx.get("fee", 0),
                        "confirmed": True,
                        "block_height": h,
                    }
                )
            if len(items) >= limit:
                break
        return items[:limit]

    def _status(self) -> Dict[str, Any]:
        s = self.chain.summary()
        rich = sorted(self.chain.balances.items(), key=lambda x: -x[1])[:8]
        addresses = []
        for a in self.wallet.list_addresses():
            addresses.append(
                {
                    "address": a,
                    "balance": format_howl(self.chain.balance(a)),
                    "balance_howlies": self.chain.balance(a),
                }
            )
        primary = self.wallet.address
        s["wallet"] = {
            "address": primary,
            "balance": format_howl(self.chain.balance(primary)),
            "balance_howlies": self.chain.balance(primary),
            "has_mnemonic": self.wallet.has_mnemonic,
            "addresses": addresses,
            "address_count": len(addresses),
        }
        s["fees"] = {
            "min_fee": format_howl(MIN_TX_FEE_HOWLIES),
            "min_fee_howlies": MIN_TX_FEE_HOWLIES,
            "default_fee": format_howl(DEFAULT_TX_FEE_HOWLIES),
            "default_fee_howlies": DEFAULT_TX_FEE_HOWLIES,
            "default_fee_howl": DEFAULT_TX_FEE_HOWLIES / 100_000_000,
            "note": "Fees are paid to the miner who confirms the transaction",
        }
        s["activity"] = self._wallet_activity()
        s["peers"] = self.node.peer_status() if self.node else []
        s["node_running"] = self.node is not None
        s["p2p_port"] = self.p2p_port
        s["rpc_port"] = self.port
        s["version"] = VERSION
        s["name"] = COIN_NAME
        s["ticker"] = TICKER
        s["richlist"] = [{"address": a, "balance": format_howl(b)} for a, b in rich]
        return s

    def make_handler(self):
        dash = self

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

            def do_OPTIONS(self):
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def do_GET(self):
                path = urllib.parse.urlparse(self.path).path
                if path in ("/", "/index.html", "/wallet"):
                    return self._bytes(200, DASHBOARD_HTML.encode(), "text/html; charset=utf-8")
                if path.startswith("/assets/"):
                    name = path[len("/assets/") :]
                    if ".." in name or name.startswith("/"):
                        return self._json(400, {"error": "bad path"})
                    f = ASSETS_DIR / name
                    if not f.is_file():
                        return self._json(404, {"error": "not found"})
                    ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    return self._bytes(200, f.read_bytes(), ctype)
                if path == "/api/status":
                    return self._json(200, dash._status())
                if path == "/api/wallet/mnemonic":
                    # Localhost wallet only — never expose remotely without auth
                    if dash.host not in ("127.0.0.1", "localhost", "::1"):
                        return self._json(403, {"error": "mnemonic only on localhost wallet"})
                    if not dash.wallet.has_mnemonic:
                        return self._json(
                            200,
                            {
                                "has_mnemonic": False,
                                "note": "This wallet was imported from a private key (no BIP39 phrase).",
                            },
                        )
                    return self._json(
                        200,
                        {
                            "has_mnemonic": True,
                            "mnemonic": dash.wallet.mnemonic,
                            "derivation": dash.wallet.derivation,
                            "warning": "Anyone with these words can steal your HOWL. Store offline.",
                        },
                    )
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
                        to = body["to"].strip()
                        amount = parse_howl(str(body["amount"]))
                        if body.get("fee") is None or str(body.get("fee", "")).strip() == "":
                            fee = DEFAULT_TX_FEE_HOWLIES
                        else:
                            fee = parse_howl(str(body["fee"]))
                        if fee < MIN_TX_FEE_HOWLIES:
                            return self._json(
                                400,
                                {
                                    "error": (
                                        f"fee too low (min {format_howl(MIN_TX_FEE_HOWLIES)}; "
                                        "fees pay the miner who confirms your tx)"
                                    )
                                },
                            )
                        memo = str(body.get("memo") or "")
                        sender = (body.get("from") or "").strip()
                        key = dash.wallet.primary
                        if sender:
                            found = dash.wallet.get_key_by_address(sender)
                            if not found:
                                return self._json(400, {"error": f"sender not in wallet: {sender}"})
                            key = found
                        nonce = dash.chain.next_nonce(key.address)
                        bal = dash.chain.balance(key.address)
                        if bal < amount + fee:
                            return self._json(
                                400,
                                {
                                    "error": (
                                        f"insufficient balance (have {format_howl(bal)}, "
                                        f"need {format_howl(amount + fee)})"
                                    )
                                },
                            )
                        tx = dash.wallet.build_tx(
                            to, amount, nonce, fee=fee, memo=memo, key=key
                        )
                        if dash.node:
                            with dash.node.chain_lock:
                                ok, msg = dash.chain.add_to_mempool(tx)
                            if ok:
                                dash.node.announce_tx(tx)
                        else:
                            ok, msg = dash.chain.add_to_mempool(tx)
                        if not ok:
                            return self._json(400, {"error": msg})
                        return self._json(
                            200,
                            {
                                "ok": True,
                                "txid": msg,
                                "fee": format_howl(fee),
                                "amount": format_howl(amount),
                            },
                        )
                    except Exception as e:
                        return self._json(400, {"error": str(e)})

                if path == "/api/wallet/new-address":
                    try:
                        kp = dash.wallet.new_address()
                        return self._json(
                            200,
                            {
                                "ok": True,
                                "address": kp.address,
                                "count": len(dash.wallet.keys),
                            },
                        )
                    except Exception as e:
                        return self._json(400, {"error": str(e)})

                if path == "/api/connect":
                    if not dash.node:
                        return self._json(
                            400, {"error": "P2P node not running — start with: howl node"}
                        )
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
        print(f"Howlcoin wallet → http://{self.host}:{self.port}/")
        print(f"  chain height {self.chain.height()} · wallet {self.wallet.address}")
        self._httpd.serve_forever()

    def start_background(self) -> threading.Thread:
        t = threading.Thread(target=self.serve_forever, name="howl-dashboard", daemon=True)
        t.start()
        return t
