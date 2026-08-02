"""Howlcoin P2P — JSON-line TCP so friends can sync & mine the same chain."""

from __future__ import annotations

import json
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .config import DEFAULT_P2P_PORT, PEER_FILE, VERSION
from .blockchain import Blockchain


def _addr_key(host: str, port: int) -> str:
    return f"{host}:{port}"


def parse_peer(s: str) -> Tuple[str, int]:
    s = s.strip()
    if "://" in s:
        s = s.split("://", 1)[1]
    if ":" in s:
        host, port_s = s.rsplit(":", 1)
        return host, int(port_s)
    return s, DEFAULT_P2P_PORT


class PeerConnection:
    def __init__(self, sock: socket.socket, host: str, port: int, inbound: bool):
        self.sock = sock
        self.host = host
        self.port = port  # remote advertised listen port if known
        self.inbound = inbound
        self.remote_height = 0
        self.remote_tip = ""
        self.alive = True
        self.lock = threading.Lock()
        self.buf = b""

    @property
    def key(self) -> str:
        return _addr_key(self.host, self.port)

    def send(self, msg: Dict[str, Any]) -> bool:
        data = (json.dumps(msg, separators=(",", ":")) + "\n").encode()
        with self.lock:
            try:
                self.sock.sendall(data)
                return True
            except OSError:
                self.alive = False
                return False

    def close(self) -> None:
        self.alive = False
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Node:
    """
    Lightweight Howlcoin node:
    - listens on P2P port
    - dials seed peers
    - syncs longer chains
    - relays new blocks + txs
    """

    def __init__(
        self,
        chain: Blockchain,
        host: str = "0.0.0.0",
        port: int = DEFAULT_P2P_PORT,
        seeds: Optional[List[str]] = None,
        on_event: Optional[Callable[[str], None]] = None,
    ):
        self.chain = chain
        self.host = host
        self.port = port
        self.seeds = seeds or []
        self.on_event = on_event or (lambda m: print(m, flush=True))
        self.peers: Dict[str, PeerConnection] = {}
        self.peers_lock = threading.RLock()
        self.chain_lock = threading.RLock()
        self._stop = threading.Event()
        self._server_sock: Optional[socket.socket] = None
        self._seen_blocks: Set[str] = set()
        self._seen_txs: Set[str] = set()
        self._fail_counts: Dict[str, int] = {}
        self._next_try: Dict[str, float] = {}
        self.peer_file = chain.data_dir / PEER_FILE
        self._load_peers_file()

    # ----- peer persistence -----

    def _seed_keys(self) -> Set[str]:
        out: Set[str] = set()
        for s in self.seeds:
            try:
                h, p = parse_peer(s)
                out.add(_addr_key(h, p))
            except ValueError:
                continue
        return out

    def _load_peers_file(self) -> None:
        self.known_peers: Set[str] = set(self.seeds)
        if self.peer_file.exists():
            try:
                data = json.loads(self.peer_file.read_text())
                for p in data.get("peers", []):
                    self.known_peers.add(p)
            except Exception:
                pass

    def _save_peers_file(self) -> None:
        seeds = self._seed_keys()
        with self.peers_lock:
            active = [p.key for p in self.peers.values() if p.alive]
        # Never persist peers that have failed repeatedly (keeps dead IPs out of peers.json)
        keep = set(active) | seeds
        for p in list(self.known_peers):
            if p in seeds or p in active:
                keep.add(p)
            elif self._fail_counts.get(p, 0) < 3:
                keep.add(p)
        self.known_peers = keep
        self.peer_file.parent.mkdir(parents=True, exist_ok=True)
        self.peer_file.write_text(json.dumps({"peers": sorted(keep)}, indent=2))

    def log(self, msg: str) -> None:
        self.on_event(f"[p2p] {msg}")

    # ----- lifecycle -----

    def start(self) -> None:
        t = threading.Thread(target=self._serve, name="howl-p2p-serve", daemon=True)
        t.start()
        threading.Thread(target=self._dial_loop, name="howl-p2p-dial", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="howl-p2p-beat", daemon=True).start()
        self.log(f"listening on {self.host}:{self.port}")

    def stop(self) -> None:
        self._stop.set()
        if self._server_sock:
            try:
                self._server_sock.close()
            except OSError:
                pass
        with self.peers_lock:
            for p in list(self.peers.values()):
                p.close()
            self.peers.clear()
        self._save_peers_file()

    def peer_status(self) -> List[Dict[str, Any]]:
        with self.peers_lock:
            return [
                {
                    "host": p.host,
                    "port": p.port,
                    "inbound": p.inbound,
                    "height": p.remote_height,
                    "tip": p.remote_tip[:16] + "…" if p.remote_tip else "",
                    "alive": p.alive,
                }
                for p in self.peers.values()
            ]

    # ----- server -----

    def _serve(self) -> None:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((self.host, self.port))
            s.listen(32)
            s.settimeout(1.0)
            self._server_sock = s
        except OSError as e:
            self.log(f"failed to bind {self.host}:{self.port}: {e}")
            return
        while not self._stop.is_set():
            try:
                conn, addr = s.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            host, _ephemeral = addr[0], addr[1]
            # temporary port until hello advertises listen port
            peer = PeerConnection(conn, host, self.port, inbound=True)
            threading.Thread(
                target=self._handle_peer,
                args=(peer,),
                name=f"howl-peer-{host}",
                daemon=True,
            ).start()

    def _dial_loop(self) -> None:
        time.sleep(0.3)
        while not self._stop.is_set():
            targets = set(self.known_peers)
            for seed in self.seeds:
                targets.add(seed)
            now = time.time()
            for target in list(targets):
                if self._stop.is_set():
                    break
                try:
                    host, port = parse_peer(target)
                except ValueError:
                    continue
                # skip self
                if port == self.port and host in ("127.0.0.1", "localhost", "0.0.0.0", self.host):
                    # still try localhost only if different port
                    if host in ("127.0.0.1", "localhost") and port == self.port:
                        continue
                key = _addr_key(host, port)
                with self.peers_lock:
                    if key in self.peers and self.peers[key].alive:
                        continue
                # back off dead peers (do not spam "timed out" every few seconds)
                nxt = self._next_try.get(key, 0.0)
                if now < nxt:
                    continue
                self.connect(host, port)
            time.sleep(12)

    def connect(self, host: str, port: int) -> bool:
        key = _addr_key(host, port)
        seeds = self._seed_keys()
        try:
            sock = socket.create_connection((host, port), timeout=5)
            sock.settimeout(30)
        except OSError as e:
            fails = self._fail_counts.get(key, 0) + 1
            self._fail_counts[key] = fails
            # 30s, 60s, 2m … up to 1h
            delay = min(3600.0, 30.0 * (2 ** min(fails - 1, 6)))
            self._next_try[key] = time.time() + delay
            if key not in seeds and fails >= 3:
                self.known_peers.discard(key)
                self._save_peers_file()
                self.log(f"dropped unreachable peer {key} after {fails} fails")
            elif fails <= 2 or fails % 5 == 0:
                # reduce log spam — first two fails + every 5th
                self.log(f"dial {key} failed: {e} (retry in {int(delay)}s)")
            return False
        self._fail_counts[key] = 0
        self._next_try.pop(key, None)
        peer = PeerConnection(sock, host, port, inbound=False)
        with self.peers_lock:
            self.peers[key] = peer
        self.known_peers.add(key)
        self._save_peers_file()
        threading.Thread(
            target=self._handle_peer,
            args=(peer,),
            name=f"howl-out-{key}",
            daemon=True,
        ).start()
        self.log(f"connected → {key}")
        return True

    def add_seed(self, peer: str) -> None:
        host, port = parse_peer(peer)
        key = _addr_key(host, port)
        self.known_peers.add(key)
        self.seeds.append(key)
        self._save_peers_file()
        self.connect(host, port)

    # ----- messaging -----

    def _hello_msg(self) -> Dict[str, Any]:
        with self.chain_lock:
            return {
                "type": "hello",
                "version": VERSION,
                "coin": "HOWL",
                "port": self.port,
                "height": self.chain.height(),
                "tip": self.chain.tip()["hash"],
                "genesis": self.chain.genesis_hash(),
            }

    def _handle_peer(self, peer: PeerConnection) -> None:
        peer.sock.settimeout(60)
        peer.send(self._hello_msg())
        try:
            while not self._stop.is_set() and peer.alive:
                try:
                    chunk = peer.sock.recv(65536)
                except socket.timeout:
                    peer.send({"type": "ping", "t": time.time()})
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                peer.buf += chunk
                while b"\n" in peer.buf:
                    line, peer.buf = peer.buf.split(b"\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line.decode())
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    self._on_message(peer, msg)
        finally:
            peer.close()
            with self.peers_lock:
                # remove if same object
                for k, p in list(self.peers.items()):
                    if p is peer:
                        del self.peers[k]
            self.log(f"peer closed {peer.host}:{peer.port}")

    def _on_message(self, peer: PeerConnection, msg: Dict[str, Any]) -> None:
        mtype = msg.get("type")
        if mtype == "hello":
            if msg.get("coin") != "HOWL":
                self.log("peer is not Howlcoin — disconnecting")
                peer.close()
                return
            if msg.get("genesis") and msg["genesis"] != self.chain.genesis_hash():
                self.log("genesis mismatch — different network")
                peer.close()
                return
            peer.remote_height = int(msg.get("height", 0))
            peer.remote_tip = msg.get("tip", "")
            adv_port = int(msg.get("port", peer.port))
            peer.port = adv_port
            key = _addr_key(peer.host, peer.port)
            with self.peers_lock:
                self.peers[key] = peer
            self.known_peers.add(key)
            # sync if they are ahead
            if peer.remote_height > self.chain.height():
                peer.send({"type": "get_blocks", "from_height": 0})
            elif peer.remote_height < self.chain.height():
                # they will ask us; offer tip inv
                peer.send(
                    {
                        "type": "inv",
                        "height": self.chain.height(),
                        "tip": self.chain.tip()["hash"],
                    }
                )
            return

        if mtype == "ping":
            peer.send({"type": "pong", "t": msg.get("t")})
            return

        if mtype == "pong":
            return

        if mtype == "inv":
            peer.remote_height = int(msg.get("height", peer.remote_height))
            peer.remote_tip = msg.get("tip", peer.remote_tip)
            if peer.remote_height > self.chain.height():
                peer.send({"type": "get_blocks", "from_height": 0})
            return

        if mtype == "get_blocks":
            from_h = int(msg.get("from_height", 0))
            with self.chain_lock:
                blocks = self.chain.get_blocks_from(from_h, limit=200)
            # send in chunks of 20
            for i in range(0, len(blocks), 20):
                peer.send({"type": "blocks", "blocks": blocks[i : i + 20]})
            peer.send(
                {
                    "type": "sync_done",
                    "height": self.chain.height(),
                    "tip": self.chain.tip()["hash"],
                }
            )
            return

        if mtype == "blocks":
            blocks = msg.get("blocks") or []
            self._ingest_blocks(blocks, peer)
            return

        if mtype == "sync_done":
            peer.remote_height = int(msg.get("height", peer.remote_height))
            peer.remote_tip = msg.get("tip", peer.remote_tip)
            return

        if mtype == "block":
            block = msg.get("block")
            if not block:
                return
            self._ingest_single_block(block, relay=True, origin=peer)
            return

        if mtype == "tx":
            tx = msg.get("tx")
            if not tx:
                return
            tid = tx.get("txid", "")
            if tid in self._seen_txs:
                return
            self._seen_txs.add(tid)
            with self.chain_lock:
                ok, reason = self.chain.add_to_mempool(tx)
            if ok:
                self.log(f"mempool +tx {tid[:12]}…")
                self.broadcast({"type": "tx", "tx": tx}, exclude=peer.key)
            return

        if mtype == "get_peers":
            with self.peers_lock:
                plist = [p.key for p in self.peers.values() if p.alive]
            peer.send({"type": "peers", "peers": plist})
            return

        if mtype == "peers":
            for p in msg.get("peers") or []:
                self.known_peers.add(p)
            self._save_peers_file()
            return

    def _ingest_blocks(self, blocks: List[Dict[str, Any]], peer: PeerConnection) -> None:
        if not blocks:
            return
        # Accumulate sync batches from this peer (chunked IBD)
        if not hasattr(peer, "_sync_buf"):
            peer._sync_buf = []  # type: ignore[attr-defined]
        buf: List[Dict[str, Any]] = peer._sync_buf  # type: ignore[attr-defined]
        for b in blocks:
            h = int(b.get("height", -1))
            # reset buffer if peer restarts sync from genesis
            if h == 0:
                buf = []
                peer._sync_buf = buf  # type: ignore[attr-defined]
            buf.append(b)

        with self.chain_lock:
            applied = 0
            # Prefer tip-extend for live blocks
            for b in blocks:
                hsh = b.get("hash", "")
                if any(x["hash"] == hsh for x in self.chain.blocks):
                    self._seen_blocks.add(hsh)
                    continue
                ok, _msg = self.chain.try_add_block(b)
                if ok:
                    self._seen_blocks.add(hsh)
                    applied += 1

            # Full-chain adopt when we have a longer contiguous buffer from genesis
            if buf and buf[0].get("height") == 0 and len(buf) > len(self.chain.blocks):
                # ensure contiguous heights
                contiguous = True
                for i, b in enumerate(buf):
                    if int(b.get("height", -1)) != i:
                        contiguous = False
                        break
                if contiguous:
                    ok, msg = self.chain.adopt_chain(buf)
                    if ok:
                        self.log(msg)
                        for b in buf:
                            self._seen_blocks.add(b["hash"])
                        peer._sync_buf = []  # type: ignore[attr-defined]
                        return

            if applied:
                self.log(f"synced +{applied} blocks → height {self.chain.height()}")

        # If still behind after batch, request full chain again
        if peer.remote_height > self.chain.height():
            peer.send({"type": "get_blocks", "from_height": 0})

    def _ingest_single_block(
        self, block: Dict[str, Any], relay: bool = True, origin: Optional[PeerConnection] = None
    ) -> bool:
        h = block.get("hash", "")
        if not h or h in self._seen_blocks:
            return False
        with self.chain_lock:
            if any(b["hash"] == h for b in self.chain.blocks):
                self._seen_blocks.add(h)
                return True
            ok, msg = self.chain.try_add_block(block)
            if not ok:
                # maybe we're behind — request full sync from origin
                if origin and "not next height" in msg:
                    origin.send({"type": "get_blocks", "from_height": 0})
                self.log(f"reject block: {msg}")
                return False
            self._seen_blocks.add(h)
            self.log(f"accepted block #{block.get('height')} {h[:14]}…")
        if relay:
            excl = origin.key if origin else None
            self.broadcast({"type": "block", "block": block}, exclude=excl)
        return True

    def broadcast(self, msg: Dict[str, Any], exclude: Optional[str] = None) -> int:
        n = 0
        with self.peers_lock:
            peers = list(self.peers.values())
        for p in peers:
            if not p.alive:
                continue
            if exclude and p.key == exclude:
                continue
            if p.send(msg):
                n += 1
        return n

    def announce_block(self, block: Dict[str, Any]) -> None:
        self._seen_blocks.add(block["hash"])
        n = self.broadcast({"type": "block", "block": block})
        self.log(f"broadcast block #{block.get('height')} to {n} peer(s)")

    def announce_tx(self, tx: Dict[str, Any]) -> None:
        tid = tx.get("txid", "")
        if tid:
            self._seen_txs.add(tid)
        n = self.broadcast({"type": "tx", "tx": tx})
        self.log(f"broadcast tx to {n} peer(s)")

    def _heartbeat(self) -> None:
        while not self._stop.is_set():
            time.sleep(20)
            with self.chain_lock:
                inv = {
                    "type": "inv",
                    "height": self.chain.height(),
                    "tip": self.chain.tip()["hash"],
                }
            self.broadcast(inv)
            # gossip peers
            with self.peers_lock:
                plist = [p.key for p in self.peers.values() if p.alive]
            if plist:
                self.broadcast({"type": "peers", "peers": plist})
