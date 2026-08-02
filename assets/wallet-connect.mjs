/**
 * Howlcoin WalletConnect v2 (wallet mode) + EIP-1193 helpers.
 * External dApps pair via WC QR / deep link; signing uses the unlocked ETH key.
 *
 * Requires a free Reown Cloud projectId:
 *   https://cloud.reown.com  → set HOWL_WC_PROJECT_ID on the server
 *   or window.HOWL_WC_PROJECT_ID
 */
import { keccak_256 } from "https://esm.sh/@noble/hashes@1.4.0/sha3";
import * as secp from "https://esm.sh/@noble/secp256k1@1.7.1";

const META = {
  name: "Howlcoin Wallet",
  description: "Howlcoin multi-chain wallet · HOWL · ETH · SOL",
  url: "https://howlscan.org",
  icons: ["https://howlscan.org/assets/howlcoin-logo-meme-pup-coin.jpg"],
};

/** CAIP-2 chains we advertise */
export const WC_CHAINS = {
  "eip155:1": { name: "Ethereum", evmId: "eth", chainId: 1, rpc: "https://cloudflare-eth.com" },
  "eip155:10": { name: "Optimism", evmId: "op", chainId: 10, rpc: "https://mainnet.optimism.io" },
  "eip155:8453": { name: "Base", evmId: "base", chainId: 8453, rpc: "https://mainnet.base.org" },
  "eip155:56": { name: "BNB Chain", evmId: "bnb", chainId: 56, rpc: "https://bsc-dataseed.binance.org" },
  "eip155:43114": { name: "Avalanche", evmId: "avax", chainId: 43114, rpc: "https://api.avax.network/ext/bc/C/rpc" },
};

const METHODS = [
  "eth_accounts",
  "eth_requestAccounts",
  "eth_chainId",
  "personal_sign",
  "eth_sign",
  "eth_signTypedData",
  "eth_signTypedData_v4",
  "eth_sendTransaction",
  "eth_signTransaction",
  "wallet_switchEthereumChain",
  "wallet_addEthereumChain",
];
const EVENTS = ["chainChanged", "accountsChanged"];

function hexToBytes(h) {
  const s = String(h || "").replace(/^0x/i, "");
  if (s.length % 2) throw new Error("bad hex");
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  return out;
}
function bytesToHex(b) {
  return Array.from(b)
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("");
}
function utf8(s) {
  return new TextEncoder().encode(String(s));
}

function signDigestRecoverable(msgHash, privateKeyHex) {
  const priv = hexToBytes(privateKeyHex);
  let sigBytes;
  let recovery = 0;
  if (typeof secp.signSync === "function") {
    const out = secp.signSync(msgHash, priv, { recovered: true, der: false });
    if (Array.isArray(out)) {
      sigBytes = out[0];
      recovery = out[1];
    } else sigBytes = out;
  } else {
    throw new Error("secp signSync required");
  }
  const r = sigBytes.slice(0, 32);
  const s = sigBytes.slice(32, 64);
  const v = 27 + recovery;
  return "0x" + bytesToHex(r) + bytesToHex(s) + v.toString(16).padStart(2, "0");
}

/** EIP-191 personal_sign */
export function personalSign(message, privateKeyHex) {
  let msgBytes;
  if (typeof message === "string" && message.startsWith("0x")) {
    try {
      msgBytes = hexToBytes(message);
    } catch {
      msgBytes = utf8(message);
    }
  } else {
    msgBytes = utf8(message);
  }
  const prefix = utf8(`\x19Ethereum Signed Message:\n${msgBytes.length}`);
  const body = new Uint8Array(prefix.length + msgBytes.length);
  body.set(prefix, 0);
  body.set(msgBytes, prefix.length);
  const hash = keccak_256(body);
  return signDigestRecoverable(hash, privateKeyHex);
}

/** eth_sign (legacy hash) */
export function ethSignHash(hashHex, privateKeyHex) {
  const hash = hexToBytes(hashHex);
  if (hash.length !== 32) throw new Error("eth_sign expects 32-byte hash");
  return signDigestRecoverable(hash, privateKeyHex);
}

function rlpEncode(input) {
  if (input instanceof Uint8Array) return rlpEncodeBytes(input);
  if (typeof input === "string") {
    if (input.startsWith("0x")) return rlpEncodeBytes(hexToBytes(input === "0x" ? "" : input));
    return rlpEncodeBytes(utf8(input));
  }
  if (typeof input === "number" || typeof input === "bigint") {
    const n = BigInt(input);
    if (n === 0n) return rlpEncodeBytes(new Uint8Array(0));
    let hex = n.toString(16);
    if (hex.length % 2) hex = "0" + hex;
    return rlpEncodeBytes(hexToBytes(hex));
  }
  if (Array.isArray(input)) {
    const parts = input.map(rlpEncode);
    const payload = concatBytes(...parts);
    return concatBytes(rlpLengthPrefix(payload.length, 0xc0), payload);
  }
  throw new Error("rlp bad type");
}
function rlpEncodeBytes(bytes) {
  if (bytes.length === 1 && bytes[0] < 0x80) return bytes;
  return concatBytes(rlpLengthPrefix(bytes.length, 0x80), bytes);
}
function rlpLengthPrefix(len, offset) {
  if (len < 56) return new Uint8Array([offset + len]);
  let hex = len.toString(16);
  if (hex.length % 2) hex = "0" + hex;
  const lenBytes = hexToBytes(hex);
  return concatBytes(new Uint8Array([offset + 55 + lenBytes.length]), lenBytes);
}
function concatBytes(...parts) {
  const n = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

async function ethRpc(rpc, method, params) {
  const r = await fetch(rpc, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const j = await r.json();
  if (j.error) throw new Error(j.error.message || "RPC error");
  return j.result;
}

export async function sendEthTransaction({
  privateKeyHex,
  from,
  tx,
  rpc,
  chainId = 1,
}) {
  if (!rpc) throw new Error("RPC required");
  const to = tx.to || tx.To;
  if (!to) throw new Error("tx.to required");
  const data = tx.data || tx.input || "0x";
  const value = BigInt(tx.value || "0x0");
  let nonce;
  if (tx.nonce != null) nonce = BigInt(tx.nonce);
  else nonce = BigInt(await ethRpc(rpc, "eth_getTransactionCount", [from, "pending"]));
  let gasLimit;
  if (tx.gas != null || tx.gasLimit != null) gasLimit = BigInt(tx.gas || tx.gasLimit);
  else {
    try {
      const est = await ethRpc(rpc, "eth_estimateGas", [{ from, to, data, value: "0x" + value.toString(16) }]);
      gasLimit = (BigInt(est) * 12n) / 10n;
    } catch {
      gasLimit = 210000n;
    }
  }
  let gasPrice;
  if (tx.gasPrice != null) gasPrice = BigInt(tx.gasPrice);
  else {
    const gp = await ethRpc(rpc, "eth_gasPrice", []);
    gasPrice = (BigInt(gp || "0x3b9aca00") * 12n) / 10n;
  }
  const toBytes = hexToBytes(to);
  const dataBytes = data === "0x" || !data ? new Uint8Array(0) : hexToBytes(data);
  const unsigned = [nonce, gasPrice, gasLimit, toBytes, value, dataBytes, BigInt(chainId), 0n, 0n];
  const msgHash = keccak_256(rlpEncode(unsigned));
  const priv = hexToBytes(privateKeyHex);
  const out = secp.signSync(msgHash, priv, { recovered: true, der: false });
  const sigBytes = Array.isArray(out) ? out[0] : out;
  const recovery = Array.isArray(out) ? out[1] : 0;
  const r = sigBytes.slice(0, 32);
  const s = sigBytes.slice(32, 64);
  const v = BigInt(recovery) + 35n + BigInt(chainId) * 2n;
  const raw = "0x" + bytesToHex(rlpEncode([nonce, gasPrice, gasLimit, toBytes, value, dataBytes, v, r, s]));
  return ethRpc(rpc, "eth_sendRawTransaction", [raw]);
}

/**
 * Minimal EIP-1193 provider bound to Howl ETH account.
 * Exposed as window.ethereum when enabled so same-origin / injected contexts can connect.
 */
export function createHowlEip1193Provider({
  getAddress,
  getPrivateKey,
  getChainId,
  getRpc,
  onRequestApproval,
}) {
  let chainId = Number(getChainId?.() || 1);
  const listeners = new Map();

  function emit(event, data) {
    const set = listeners.get(event);
    if (set) for (const fn of set) try { fn(data); } catch (_) {}
  }

  async function request({ method, params }) {
    const p = params || [];
    const address = getAddress?.();
    if (!address && method !== "eth_chainId") {
      throw { code: 4100, message: "Howlcoin wallet locked or no ETH key" };
    }

    switch (method) {
      case "eth_chainId":
        return "0x" + Number(getChainId?.() || chainId).toString(16);
      case "eth_accounts":
      case "eth_requestAccounts": {
        if (method === "eth_requestAccounts" && onRequestApproval) {
          const ok = await onRequestApproval({
            type: "connect",
            method,
            message: "Allow this site to see your Howlcoin ETH address?",
          });
          if (!ok) throw { code: 4001, message: "User rejected" };
        }
        return [getAddress()];
      }
      case "wallet_switchEthereumChain": {
        const idHex = p[0]?.chainId;
        if (!idHex) throw { code: -32602, message: "chainId required" };
        const id = parseInt(idHex, 16);
        const known = Object.values(WC_CHAINS).find((c) => c.chainId === id);
        if (!known) throw { code: 4902, message: "Unrecognized chain" };
        chainId = id;
        emit("chainChanged", "0x" + id.toString(16));
        return null;
      }
      case "wallet_addEthereumChain":
        return null;
      case "personal_sign": {
        // params: [data, address] or [address, data]
        let msg = p[0];
        let addr = p[1];
        if (typeof msg === "string" && msg.startsWith("0x") && msg.length === 42 && p[1]) {
          addr = p[0];
          msg = p[1];
        }
        if (onRequestApproval) {
          const ok = await onRequestApproval({
            type: "sign",
            method,
            message: typeof msg === "string" && msg.startsWith("0x")
              ? `Sign message (${msg.slice(0, 18)}…)`
              : String(msg).slice(0, 200),
          });
          if (!ok) throw { code: 4001, message: "User rejected" };
        }
        return personalSign(msg, getPrivateKey());
      }
      case "eth_sign": {
        if (onRequestApproval) {
          const ok = await onRequestApproval({ type: "sign", method, message: "Sign hash " + String(p[1]).slice(0, 20) });
          if (!ok) throw { code: 4001, message: "User rejected" };
        }
        return ethSignHash(p[1], getPrivateKey());
      }
      case "eth_signTypedData":
      case "eth_signTypedData_v4": {
        // Minimal: hash EIP-712 domain+message is complex; reject with clear error if not implemented fully
        // Try to sign the JSON string as personal_sign fallback is wrong — better error
        if (onRequestApproval) {
          const ok = await onRequestApproval({
            type: "sign",
            method,
            message: "Sign typed data (EIP-712)",
            detail: typeof p[1] === "string" ? p[1].slice(0, 300) : JSON.stringify(p[1] || {}).slice(0, 300),
          });
          if (!ok) throw { code: 4001, message: "User rejected" };
        }
        // naive: personal_sign of JSON — many dapps need real EIP-712; implement basic EIP-712 hash
        const typed = typeof p[1] === "string" ? JSON.parse(p[1]) : p[1];
        const hash = eip712Hash(typed);
        return signDigestRecoverable(hash, getPrivateKey());
      }
      case "eth_sendTransaction": {
        const tx = p[0] || {};
        if (onRequestApproval) {
          const ok = await onRequestApproval({
            type: "tx",
            method,
            message: `Send tx to ${(tx.to || "?").slice(0, 12)}…`,
            detail: JSON.stringify({ to: tx.to, value: tx.value, data: (tx.data || "").slice(0, 66) }),
          });
          if (!ok) throw { code: 4001, message: "User rejected" };
        }
        return sendEthTransaction({
          privateKeyHex: getPrivateKey(),
          from: getAddress(),
          tx,
          rpc: getRpc?.(chainId) || getRpc?.(),
          chainId: Number(getChainId?.() || chainId),
        });
      }
      default:
        throw { code: 4200, message: "Method not supported: " + method };
    }
  }

  const provider = {
    isHowlcoin: true,
    isMetaMask: false, // do not spoof MetaMask
    providerInfo: {
      uuid: "howlcoin-wallet",
      name: "Howlcoin",
      icon: META.icons[0],
      rdns: "org.howlscan.wallet",
    },
    request,
    on(event, fn) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event).add(fn);
    },
    removeListener(event, fn) {
      listeners.get(event)?.delete(fn);
    },
    emit,
    // legacy web3
    enable: async () => request({ method: "eth_requestAccounts" }),
    send(payload, cb) {
      if (typeof payload === "string") {
        return request({ method: payload, params: cb });
      }
      request(payload)
        .then((result) => cb?.(null, { id: payload.id, jsonrpc: "2.0", result }))
        .catch((err) => cb?.(err));
    },
    sendAsync(payload, cb) {
      request(payload)
        .then((result) => cb?.(null, { id: payload.id, jsonrpc: "2.0", result }))
        .catch((err) => cb?.(err, { id: payload.id, jsonrpc: "2.0", error: err }));
    },
  };
  return provider;
}

/** Minimal EIP-712 structural hash (supports common dapp payloads) */
function eip712Hash(typedData) {
  // Use ethers-style encoding via recursive ABI — simplified for common cases
  // Full EIP-712: keccak256("\x19\x01" ‖ domainSeparator ‖ hashStruct(message))
  const { domain, types, primaryType, message } = typedData || {};
  if (!domain || !types || !primaryType || !message) {
    throw new Error("Invalid typed data");
  }
  const domainTypes = { EIP712Domain: types.EIP712Domain || defaultDomainTypes(domain) };
  const domainSep = hashStruct("EIP712Domain", domain, { ...domainTypes });
  const msgHash = hashStruct(primaryType, message, types);
  const buf = new Uint8Array(2 + 32 + 32);
  buf[0] = 0x19;
  buf[1] = 0x01;
  buf.set(domainSep, 2);
  buf.set(msgHash, 34);
  return keccak_256(buf);
}
function defaultDomainTypes(domain) {
  const fields = [];
  if (domain.name != null) fields.push({ name: "name", type: "string" });
  if (domain.version != null) fields.push({ name: "version", type: "string" });
  if (domain.chainId != null) fields.push({ name: "chainId", type: "uint256" });
  if (domain.verifyingContract != null) fields.push({ name: "verifyingContract", type: "address" });
  if (domain.salt != null) fields.push({ name: "salt", type: "bytes32" });
  return fields;
}
function hashStruct(primaryType, data, types) {
  const enc = encodeData(primaryType, data, types);
  return keccak_256(enc);
}
function typeHash(primaryType, types) {
  return keccak_256(utf8(encodeType(primaryType, types)));
}
function encodeType(primaryType, types) {
  let deps = findTypeDependencies(primaryType, types);
  deps = deps.filter((t) => t !== primaryType);
  deps = [primaryType, ...deps.sort()];
  return deps
    .map((type) => {
      const fields = types[type];
      return `${type}(${fields.map((f) => `${f.type} ${f.name}`).join(",")})`;
    })
    .join("");
}
function findTypeDependencies(primaryType, types, found = new Set()) {
  if (found.has(primaryType) || !types[primaryType]) return Array.from(found);
  found.add(primaryType);
  for (const field of types[primaryType]) {
    const base = field.type.replace(/\[\d*\]$/, "");
    if (types[base]) findTypeDependencies(base, types, found);
  }
  return Array.from(found);
}
function encodeData(primaryType, data, types) {
  const parts = [typeHash(primaryType, types)];
  for (const field of types[primaryType]) {
    parts.push(encodeValue(field.type, data[field.name], types));
  }
  return concatBytes(...parts);
}
function encodeValue(type, value, types) {
  if (types[type]) return hashStruct(type, value, types);
  if (type === "string") return keccak_256(utf8(value || ""));
  if (type === "bytes") return keccak_256(typeof value === "string" ? hexToBytes(value) : value);
  if (type === "bool") return padUint(value ? 1n : 0n);
  if (type.startsWith("uint") || type.startsWith("int")) return padUint(BigInt(value || 0));
  if (type === "address") {
    const h = String(value || "").replace(/^0x/i, "").toLowerCase().padStart(40, "0");
    const b = new Uint8Array(32);
    b.set(hexToBytes(h), 12);
    return b;
  }
  if (type.startsWith("bytes") && type !== "bytes") {
    const size = parseInt(type.slice(5), 10) || 32;
    const raw = hexToBytes(value || "0x");
    const b = new Uint8Array(32);
    b.set(raw.slice(0, size), 0);
    return b;
  }
  if (type.endsWith("[]")) {
    const base = type.slice(0, -2);
    const arr = value || [];
    const enc = arr.map((v) => encodeValue(base, v, types));
    return keccak_256(concatBytes(...enc));
  }
  throw new Error("Unsupported EIP-712 type: " + type);
}
function padUint(n) {
  let hex = BigInt(n).toString(16);
  if (hex.startsWith("-")) throw new Error("negative int");
  hex = hex.padStart(64, "0");
  return hexToBytes(hex);
}

/* —— WalletConnect SignClient (wallet mode) —— */
let signClient = null;
let wcReady = false;
let wcError = "";
let handlers = {};
let listenersBound = false;

export function getWcStatus() {
  return { ready: wcReady, error: wcError, hasWallet: !!signClient, projectBound: !!signClient };
}

async function loadSignClientCtor() {
  // Prefer SignClient — lighter and more reliable than Web3Wallet via CDN
  const urls = [
    "https://esm.sh/@walletconnect/sign-client@2.17.3",
    "https://esm.sh/@walletconnect/sign-client@2.17.3?bundle",
    "https://cdn.jsdelivr.net/npm/@walletconnect/sign-client@2.17.3/+esm",
  ];
  let lastErr;
  for (const url of urls) {
    try {
      const mod = await import(url);
      const Ctor = mod.SignClient || mod.default?.SignClient || mod.default;
      if (Ctor && (typeof Ctor.init === "function" || typeof Ctor === "function")) {
        return { Ctor, url };
      }
      lastErr = new Error("SignClient export missing from " + url);
    } catch (e) {
      lastErr = e;
    }
  }
  throw lastErr || new Error("Could not load WalletConnect SignClient");
}

export async function initWalletConnect({
  projectId,
  getAddress,
  getPrivateKey,
  getChainId,
  getRpc,
  onSessionProposal,
  onSessionRequest,
  onSessionDelete,
}) {
  handlers = { getAddress, getPrivateKey, getChainId, getRpc, onSessionProposal, onSessionRequest, onSessionDelete };
  const pid = String(projectId || "").trim();
  if (!pid) {
    wcError = "Missing WalletConnect projectId";
    wcReady = false;
    return { ok: false, error: wcError };
  }
  // Already ready
  if (signClient && wcReady) {
    return { ok: true, wallet: signClient };
  }
  try {
    const { Ctor, url } = await loadSignClientCtor();
    const initFn = Ctor.init ? Ctor.init.bind(Ctor) : null;
    if (!initFn) throw new Error("SignClient.init not found");

    signClient = await initFn({
      projectId: pid,
      metadata: META,
      // relayUrl default is fine (wss://relay.walletconnect.com)
    });

    if (!listenersBound) {
      listenersBound = true;
      signClient.on("session_proposal", async (proposal) => {
        try {
          if (handlers.onSessionProposal) {
            const ok = await handlers.onSessionProposal(proposal);
            if (!ok) {
              await signClient.reject({
                id: proposal.id,
                reason: { code: 5000, message: "User rejected." },
              });
              return;
            }
          }
          await approveSessionProposal(proposal);
        } catch (e) {
          console.warn("session_proposal", e);
          try {
            await signClient.reject({
              id: proposal.id,
              reason: { code: 5000, message: e.message || "Rejected" },
            });
          } catch (_) {}
        }
      });

      signClient.on("session_request", async (event) => {
        try {
          await handleSessionRequest(event);
        } catch (e) {
          console.warn("session_request", e);
          try {
            await signClient.respond({
              topic: event.topic,
              response: {
                id: event.id,
                jsonrpc: "2.0",
                error: { code: 5000, message: e.message || "Rejected" },
              },
            });
          } catch (_) {}
        }
      });

      signClient.on("session_delete", (ev) => {
        handlers.onSessionDelete?.(ev);
      });
    }

    wcReady = true;
    wcError = "";
    console.info("Howl WalletConnect ready via", url);
    return { ok: true, wallet: signClient };
  } catch (e) {
    signClient = null;
    wcReady = false;
    listenersBound = false;
    wcError = e?.message || String(e);
    console.error("WalletConnect init failed", e);
    return { ok: false, error: wcError };
  }
}

async function approveSessionProposal(proposal) {
  const address = handlers.getAddress?.();
  if (!address) throw new Error("Unlock wallet first");
  const chainId = Number(handlers.getChainId?.() || 1);
  const caip = `eip155:${chainId}`;
  const required = proposal.params?.requiredNamespaces || {};
  const optional = proposal.params?.optionalNamespaces || {};
  const eip155Req = required.eip155 || optional.eip155 || {};
  let chains = (eip155Req.chains || []).filter((c) => String(c).startsWith("eip155:"));
  if (!chains.length) chains = Object.keys(WC_CHAINS);
  if (!chains.includes(caip)) chains = [caip, ...chains];
  // de-dupe
  chains = [...new Set(chains)];

  const accounts = chains.map((c) => `${c}:${address}`);
  const methods = eip155Req.methods?.length ? eip155Req.methods : METHODS;
  const events = eip155Req.events?.length ? eip155Req.events : EVENTS;

  const namespaces = {
    eip155: {
      chains,
      accounts,
      methods,
      events,
    },
  };

  const { acknowledged } = await signClient.approve({
    id: proposal.id,
    namespaces,
  });
  try {
    await acknowledged?.();
  } catch (_) {}
  return true;
}

async function handleSessionRequest(event) {
  const { topic, params, id } = event;
  const { request, chainId: caip } = params;
  const method = request.method;
  const p = request.params || [];
  const address = handlers.getAddress?.();
  const chainNum = caip && String(caip).includes(":")
    ? parseInt(String(caip).split(":")[1], 10)
    : Number(handlers.getChainId?.() || 1);
  const rpc =
    handlers.getRpc?.(chainNum) ||
    Object.values(WC_CHAINS).find((c) => c.chainId === chainNum)?.rpc;

  let detail = "";
  try {
    detail = JSON.stringify(p).slice(0, 400);
  } catch {
    detail = String(p);
  }

  let peerMeta = null;
  try {
    peerMeta = signClient.session.get(topic)?.peer?.metadata;
  } catch (_) {}

  if (handlers.onSessionRequest) {
    const ok = await handlers.onSessionRequest({
      id,
      topic,
      method,
      params: p,
      chainId: chainNum,
      detail,
      dapp: peerMeta,
    });
    if (!ok) {
      await signClient.respond({
        topic,
        response: {
          id,
          jsonrpc: "2.0",
          error: { code: 5000, message: "User rejected." },
        },
      });
      return;
    }
  }

  let result;
  switch (method) {
    case "eth_chainId":
      result = "0x" + chainNum.toString(16);
      break;
    case "eth_accounts":
    case "eth_requestAccounts":
      result = [address];
      break;
    case "personal_sign": {
      let msg = p[0];
      if (typeof p[0] === "string" && p[0].startsWith("0x") && p[0].length === 42) msg = p[1];
      result = personalSign(msg, handlers.getPrivateKey());
      break;
    }
    case "eth_sign":
      result = ethSignHash(p[1], handlers.getPrivateKey());
      break;
    case "eth_signTypedData":
    case "eth_signTypedData_v4": {
      const typed = typeof p[1] === "string" ? JSON.parse(p[1]) : p[1];
      const hash = eip712Hash(typed);
      result = signDigestRecoverable(hash, handlers.getPrivateKey());
      break;
    }
    case "eth_sendTransaction":
      result = await sendEthTransaction({
        privateKeyHex: handlers.getPrivateKey(),
        from: address,
        tx: p[0] || {},
        rpc,
        chainId: chainNum,
      });
      break;
    case "wallet_switchEthereumChain":
      result = null;
      break;
    default:
      throw new Error("Unsupported method: " + method);
  }

  await signClient.respond({
    topic,
    response: { id, jsonrpc: "2.0", result },
  });
}

/** Pair with dApp using wc: URI (from QR paste or deep link) */
export async function pairWithUri(uri) {
  if (!signClient || !wcReady) throw new Error("WalletConnect not ready — open WalletConnect page and wait for Ready");
  const u = String(uri || "").trim();
  if (!u.startsWith("wc:")) throw new Error("Invalid WalletConnect URI (must start with wc:)");
  await signClient.pair({ uri: u });
  return true;
}

export function listSessions() {
  if (!signClient) return [];
  try {
    return signClient.session.getAll?.() || [];
  } catch {
    return [];
  }
}

export async function disconnectSession(topic) {
  if (!signClient || !topic) return;
  try {
    await signClient.disconnect({
      topic,
      reason: { code: 6000, message: "User disconnected." },
    });
  } catch (e) {
    console.warn(e);
  }
}

/** Force re-init on next ensureWalletConnect call */
export function resetWalletConnect() {
  signClient = null;
  wcReady = false;
  wcError = "";
  listenersBound = false;
}

/** Install window.ethereum EIP-1193 provider (Howlcoin) for in-page dApps */
export function installWindowEthereum(provider, { announce = true } = {}) {
  if (typeof window === "undefined") return;
  // Don't overwrite MetaMask if present — expose howlcoin + multi-provider
  window.howlcoin = provider;
  window.howlcoinEthereum = provider;
  if (!window.ethereum) {
    window.ethereum = provider;
  } else if (!window.ethereum.isHowlcoin) {
    // EIP-6963 multi injected provider
    const existing = window.ethereum;
    if (!existing.providers) {
      existing.providers = [existing];
    }
    if (!existing.providers.find((p) => p.isHowlcoin)) {
      existing.providers.push(provider);
    }
  }
  if (announce) {
    try {
      window.dispatchEvent(
        new CustomEvent("eip6963:announceProvider", {
          detail: Object.freeze({ info: provider.providerInfo, provider }),
        })
      );
      window.addEventListener("eip6963:requestProvider", () => {
        window.dispatchEvent(
          new CustomEvent("eip6963:announceProvider", {
            detail: Object.freeze({ info: provider.providerInfo, provider }),
          })
        );
      });
    } catch (_) {}
  }
  try {
    window.dispatchEvent(new Event("ethereum#initialized"));
  } catch (_) {}
}

export { META as WC_METADATA };
