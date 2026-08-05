/**
 * Howlcoin browser crypto — matches howl/crypto.py + howl/bip39util.py
 * Uses @noble / @scure (CDN).
 */
import { sha256 } from "https://esm.sh/@noble/hashes@1.4.0/sha256";
import { ripemd160 } from "https://esm.sh/@noble/hashes@1.4.0/ripemd160";
import { hmac } from "https://esm.sh/@noble/hashes@1.4.0/hmac";
import { sha512 } from "https://esm.sh/@noble/hashes@1.4.0/sha512";
import { sha1 } from "https://esm.sh/@noble/hashes@1.4.0/sha1";
import { keccak_256 } from "https://esm.sh/@noble/hashes@1.4.0/sha3";
import { blake2b } from "https://esm.sh/@noble/hashes@1.4.0/blake2b";
import * as secp from "https://esm.sh/@noble/secp256k1@1.7.1";
import { ed25519 } from "https://esm.sh/@noble/curves@1.4.2/ed25519.js";
import { generateMnemonic, validateMnemonic, mnemonicToSeedSync } from "https://esm.sh/@scure/bip39@1.3.0";
import { wordlist } from "https://esm.sh/@scure/bip39@1.3.0/wordlists/english";

const ADDRESS_VERSION = 0x28;
const HOWL_COIN_TYPE = 42069;
const CURVE_ORDER = BigInt("0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141");

const B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz";

function bytesToHex(b) {
  return Array.from(b).map((x) => x.toString(16).padStart(2, "0")).join("");
}
function hexToBytes(h) {
  const s = h.replace(/^0x/, "");
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  return out;
}
function concat(...parts) {
  const n = parts.reduce((a, p) => a + p.length, 0);
  const out = new Uint8Array(n);
  let o = 0;
  for (const p of parts) {
    out.set(p, o);
    o += p.length;
  }
  return out;
}

function base58Encode(bytes) {
  let zeros = 0;
  while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
  const digits = [0];
  for (let i = zeros; i < bytes.length; i++) {
    let carry = bytes[i];
    for (let j = 0; j < digits.length; j++) {
      carry += digits[j] << 8;
      digits[j] = carry % 58;
      carry = (carry / 58) | 0;
    }
    while (carry) {
      digits.push(carry % 58);
      carry = (carry / 58) | 0;
    }
  }
  let str = "1".repeat(zeros);
  for (let i = digits.length - 1; i >= 0; i--) str += B58[digits[i]];
  return str;
}

function base58Decode(str) {
  let zeros = 0;
  while (zeros < str.length && str[zeros] === "1") zeros++;
  const bytes = [0];
  for (let i = zeros; i < str.length; i++) {
    const v = B58.indexOf(str[i]);
    if (v < 0) throw new Error("invalid base58");
    let carry = v;
    for (let j = 0; j < bytes.length; j++) {
      carry += bytes[j] * 58;
      bytes[j] = carry & 0xff;
      carry >>= 8;
    }
    while (carry) {
      bytes.push(carry & 0xff);
      carry >>= 8;
    }
  }
  for (let i = 0; i < zeros; i++) bytes.push(0);
  return new Uint8Array(bytes.reverse());
}

function hash160(data) {
  return ripemd160(sha256(data));
}
function doubleSha256(data) {
  return sha256(sha256(data));
}

export function pubkeyToAddress(pubkey) {
  // uncompressed 65-byte 04||X||Y or raw
  const pub = typeof pubkey === "string" ? hexToBytes(pubkey) : pubkey;
  const payload = concat(new Uint8Array([ADDRESS_VERSION]), hash160(pub));
  const checksum = doubleSha256(payload).slice(0, 4);
  return base58Encode(concat(payload, checksum));
}

export function isValidAddress(address) {
  try {
    const raw = base58Decode(address);
    if (raw.length !== 25) return false;
    const payload = raw.slice(0, 21);
    const checksum = raw.slice(21);
    if (bytesToHex(doubleSha256(payload).slice(0, 4)) !== bytesToHex(checksum)) return false;
    return payload[0] === ADDRESS_VERSION;
  } catch {
    return false;
  }
}

function ser32(i) {
  return new Uint8Array([(i >>> 24) & 255, (i >>> 16) & 255, (i >>> 8) & 255, i & 255]);
}

function pointCompressed(privBytes) {
  // noble Point from private
  const pub = secp.getPublicKey(privBytes, true); // compressed
  return pub;
}

function ckdPriv(parentKey, parentChain, index) {
  let data;
  if (index >= 0x80000000) {
    data = concat(new Uint8Array([0]), parentKey, ser32(index));
  } else {
    data = concat(pointCompressed(parentKey), ser32(index));
  }
  const I = hmac(sha512, parentChain, data);
  const IL = I.slice(0, 32);
  const IR = I.slice(32);
  const il = BigInt("0x" + bytesToHex(IL));
  const parent = BigInt("0x" + bytesToHex(parentKey));
  if (il >= CURVE_ORDER) throw new Error("invalid child");
  const child = (il + parent) % CURVE_ORDER;
  if (child === 0n) throw new Error("invalid child zero");
  const childBytes = hexToBytes(child.toString(16).padStart(64, "0"));
  return [childBytes, IR];
}

function masterFromSeed(seed) {
  const I = hmac(sha512, new TextEncoder().encode("Bitcoin seed"), seed);
  return [I.slice(0, 32), I.slice(32)];
}

function derivePath(seed, path) {
  let [key, chain] = masterFromSeed(seed);
  for (const index of path) {
    [key, chain] = ckdPriv(key, chain, index);
  }
  return key;
}

export function howlPath(index = 0) {
  return [
    44 | 0x80000000,
    HOWL_COIN_TYPE | 0x80000000,
    0 | 0x80000000,
    0,
    index,
  ];
}

export function createMnemonic() {
  return generateMnemonic(wordlist, 128);
}

export function checkMnemonic(phrase) {
  return validateMnemonic(phrase.trim().toLowerCase().split(/\s+/).join(" "), wordlist);
}

export function ethPath(index = 0) {
  // BIP44 Ethereum: m/44'/60'/0'/0/index
  return [44 | 0x80000000, 60 | 0x80000000, 0 | 0x80000000, 0, index];
}

export function keypairFromMnemonic(phrase, index = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = derivePath(seed, howlPath(index));
  const privHex = bytesToHex(priv);
  // uncompressed pubkey 04||x||y (65 bytes) — matches Python ecdsa
  const pubUncompressed = secp.getPublicKey(priv, false);
  const pubHex = bytesToHex(pubUncompressed);
  const address = pubkeyToAddress(pubUncompressed);
  return { privateKeyHex: privHex, publicKeyHex: pubHex, address, index, seedHex: bytesToHex(seed) };
}

/** Normalize + validate a raw secp256k1 private key (64 hex chars, optional 0x). */
export function normalizePrivateKeyHex(privateKeyHex) {
  const keyHex = String(privateKeyHex || "")
    .trim()
    .toLowerCase()
    .replace(/^0x/, "")
    .replace(/\s+/g, "");
  if (!/^[0-9a-f]{64}$/.test(keyHex)) {
    throw new Error("Private key must be 64 hex characters (32 bytes)");
  }
  const n = BigInt("0x" + keyHex);
  if (n === 0n || n >= CURVE_ORDER) {
    throw new Error("Invalid private key value");
  }
  return keyHex;
}

/**
 * HOWL keypair from a raw secp256k1 private key (matches KeyPair.from_private_hex).
 * No BIP39 seed — multi-chain paths (SOL/BTC/…) are not available.
 */
export function keypairFromPrivateHex(privateKeyHex) {
  const keyHex = normalizePrivateKeyHex(privateKeyHex);
  const priv = hexToBytes(keyHex);
  const pubUncompressed = secp.getPublicKey(priv, false);
  const pubHex = bytesToHex(pubUncompressed);
  const address = pubkeyToAddress(pubUncompressed);
  return {
    privateKeyHex: keyHex,
    publicKeyHex: pubHex,
    address,
    index: null,
    imported: true,
  };
}

/** Ethereum address from the same raw secp256k1 private key (not BIP44-derived). */
export function ethAddressFromPrivateHex(privateKeyHex) {
  const keyHex = normalizePrivateKeyHex(privateKeyHex);
  const priv = hexToBytes(keyHex);
  const pubUncompressed = secp.getPublicKey(priv, false);
  const hash = keccak_256(pubUncompressed.slice(1));
  const addr = "0x" + bytesToHex(hash.slice(-20));
  return {
    address: addr,
    privateKeyHex: keyHex,
    path: "imported",
  };
}

/** Ethereum address from same mnemonic (for ETH + ERC-20 stables / custom tokens). */
export function ethAddressFromMnemonic(phrase, index = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = derivePath(seed, ethPath(index));
  const pubUncompressed = secp.getPublicKey(priv, false); // 65 bytes 04||x||y
  const hash = keccak_256(pubUncompressed.slice(1));
  const addr = "0x" + bytesToHex(hash.slice(-20));
  return {
    address: addr,
    privateKeyHex: bytesToHex(priv),
    path: `m/44'/60'/0'/0/${index}`,
  };
}

// --- Solana (ed25519 / SLIP-0010) — Phantom path m/44'/501'/0'/0' ---
function slip10MasterEd25519(seed) {
  const I = hmac(sha512, new TextEncoder().encode("ed25519 seed"), seed);
  return [I.slice(0, 32), I.slice(32)];
}

function slip10CkdEd25519(parentKey, parentChain, index) {
  // Ed25519 SLIP-0010: only hardened derivation
  if (index < 0x80000000) index = index | 0x80000000;
  const data = concat(new Uint8Array([0]), parentKey, ser32(index >>> 0));
  const I = hmac(sha512, parentChain, data);
  return [I.slice(0, 32), I.slice(32)];
}

function deriveEd25519Path(seed, path) {
  let [key, chain] = slip10MasterEd25519(seed);
  for (const index of path) {
    [key, chain] = slip10CkdEd25519(key, chain, index);
  }
  return key;
}

export function solPath(account = 0) {
  // m/44'/501'/account'/0'  (Phantom-compatible)
  return [
    44 | 0x80000000,
    501 | 0x80000000,
    account | 0x80000000,
    0 | 0x80000000,
  ];
}

/** Solana address from same mnemonic (ed25519). */
export function solAddressFromMnemonic(phrase, account = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = deriveEd25519Path(seed, solPath(account));
  const pub = ed25519.getPublicKey(priv);
  // Solana address = base58(publicKey)
  const address = base58Encode(pub);
  return {
    address,
    privateKeyHex: bytesToHex(priv),
    publicKeyHex: bytesToHex(pub),
    path: `m/44'/501'/${account}'/0'`,
  };
}

// --- Google Authenticator TOTP (RFC 6238) ---
const B32 = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";

export function generateTotpSecret(bytes = 20) {
  const raw = crypto.getRandomValues(new Uint8Array(bytes));
  let bits = "";
  for (const b of raw) bits += b.toString(2).padStart(8, "0");
  let out = "";
  for (let i = 0; i + 5 <= bits.length; i += 5) {
    out += B32[parseInt(bits.slice(i, i + 5), 2)];
  }
  return out;
}

function base32Decode(s) {
  const clean = s.replace(/=+$/, "").toUpperCase().replace(/[^A-Z2-7]/g, "");
  let bits = "";
  for (const c of clean) {
    const v = B32.indexOf(c);
    if (v < 0) continue;
    bits += v.toString(2).padStart(5, "0");
  }
  const out = [];
  for (let i = 0; i + 8 <= bits.length; i += 8) {
    out.push(parseInt(bits.slice(i, i + 8), 2));
  }
  return new Uint8Array(out);
}

export function totpCode(secretBase32, timeMs = Date.now(), step = 30, digits = 6) {
  const key = base32Decode(secretBase32);
  const counter = Math.floor(timeMs / 1000 / step);
  const buf = new Uint8Array(8);
  let c = counter;
  for (let i = 7; i >= 0; i--) {
    buf[i] = c & 0xff;
    c = Math.floor(c / 256);
  }
  const mac = hmac(sha1, key, buf);
  const offset = mac[mac.length - 1] & 0xf;
  const bin =
    ((mac[offset] & 0x7f) << 24) |
    ((mac[offset + 1] & 0xff) << 16) |
    ((mac[offset + 2] & 0xff) << 8) |
    (mac[offset + 3] & 0xff);
  const otp = (bin % 10 ** digits).toString().padStart(digits, "0");
  return otp;
}

export function verifyTotp(secretBase32, code, window = 1) {
  const clean = String(code || "").replace(/\s/g, "");
  if (!/^\d{6}$/.test(clean)) return false;
  const now = Date.now();
  for (let w = -window; w <= window; w++) {
    if (totpCode(secretBase32, now + w * 30000) === clean) return true;
  }
  return false;
}

export function totpOtpauthUrl(secret, accountName = "Howlcoin", issuer = "Howlcoin") {
  const label = encodeURIComponent(`${issuer}:${accountName}`);
  const q = new URLSearchParams({
    secret,
    issuer,
    algorithm: "SHA1",
    digits: "6",
    period: "30",
  });
  return `otpauth://totp/${label}?${q.toString()}`;
}

/** BIP44 + P2PKH version for UTXO deposit chains */
export const UTXO_COINS = {
  btc: { coinType: 0, version: 0x00, network: "Bitcoin", explorer: "https://mempool.space/address/" },
  ltc: { coinType: 2, version: 0x30, network: "Litecoin", explorer: "https://litecoinspace.org/address/" },
  doge: { coinType: 3, version: 0x1e, network: "Dogecoin", explorer: "https://dogechain.info/address/" },
  bch: { coinType: 145, version: 0x00, network: "Bitcoin Cash (legacy P2PKH)", explorer: "https://www.blockchain.com/explorer/addresses/bch/" },
};

/** EVM-compatible chains — same 0x address as Ethereum */
export const EVM_CHAINS = {
  eth: { network: "Ethereum", explorer: "https://etherscan.io/address/", rpc: "https://cloudflare-eth.com" },
  op: { network: "Optimism", explorer: "https://optimistic.etherscan.io/address/", rpc: "https://mainnet.optimism.io" },
  base: { network: "Base", explorer: "https://basescan.org/address/", rpc: "https://mainnet.base.org" },
  bnb: { network: "BNB Chain", explorer: "https://bscscan.com/address/", rpc: "https://bsc-dataseed.binance.org" },
  avax: { network: "Avalanche C-Chain", explorer: "https://snowtrace.io/address/", rpc: "https://api.avax.network/ext/bc/C/rpc" },
  hype: { network: "HyperEVM", explorer: "https://purrsec.com/address/", rpc: "" },
};

function utxoPath(coinType, index = 0) {
  return [44 | 0x80000000, coinType | 0x80000000, 0 | 0x80000000, 0, index];
}

export function utxoAddressFromMnemonic(phrase, coinId = "btc", index = 0, passphrase = "") {
  const coin = UTXO_COINS[coinId];
  if (!coin) throw new Error("unknown UTXO coin " + coinId);
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = derivePath(seed, utxoPath(coin.coinType, index));
  const pub = secp.getPublicKey(priv, true);
  const payload = concat(new Uint8Array([coin.version]), hash160(pub));
  return {
    address: base58CheckEncode(payload),
    privateKeyHex: bytesToHex(priv),
    path: `m/44'/${coin.coinType}'/0'/0/${index}`,
    coinId,
    network: coin.network,
  };
}

export function btcAddressFromMnemonic(phrase, index = 0, passphrase = "") {
  return utxoAddressFromMnemonic(phrase, "btc", index, passphrase);
}

/** TRX m/44'/195'/0'/0/0 */
export function trxAddressFromMnemonic(phrase, index = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = derivePath(seed, [44 | 0x80000000, 195 | 0x80000000, 0 | 0x80000000, 0, index]);
  const pubUncompressed = secp.getPublicKey(priv, false);
  const hash = keccak_256(pubUncompressed.slice(1));
  const payload = concat(new Uint8Array([0x41]), hash.slice(-20));
  return {
    address: base58CheckEncode(payload),
    privateKeyHex: bytesToHex(priv),
    path: `m/44'/195'/0'/0/${index}`,
  };
}

const XRP_B58 = "rpshnaf39wBUDNEGHJKLM4PQRST7VWXYZ2bcdeCg65jkm8oFqi1tuvAxyz";
function xrpBase58Encode(bytes) {
  let zeros = 0;
  while (zeros < bytes.length && bytes[zeros] === 0) zeros++;
  const digits = [0];
  for (let i = zeros; i < bytes.length; i++) {
    let carry = bytes[i];
    for (let j = 0; j < digits.length; j++) {
      carry += digits[j] << 8;
      digits[j] = carry % 58;
      carry = (carry / 58) | 0;
    }
    while (carry) {
      digits.push(carry % 58);
      carry = (carry / 58) | 0;
    }
  }
  let str = XRP_B58[0].repeat(zeros);
  for (let i = digits.length - 1; i >= 0; i--) str += XRP_B58[digits[i]];
  return str;
}

/** XRP m/44'/144'/0'/0/0 */
export function xrpAddressFromMnemonic(phrase, index = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = derivePath(seed, [44 | 0x80000000, 144 | 0x80000000, 0 | 0x80000000, 0, index]);
  const pub = secp.getPublicKey(priv, true);
  const payload = concat(new Uint8Array([0x00]), hash160(pub));
  const checksum = doubleSha256(payload).slice(0, 4);
  return {
    address: xrpBase58Encode(concat(payload, checksum)),
    privateKeyHex: bytesToHex(priv),
    path: `m/44'/144'/0'/0/${index}`,
  };
}

function stellarStrKeyEncode(versionByte, keyBytes) {
  let crc = 0;
  const data = concat(new Uint8Array([versionByte]), keyBytes);
  for (let i = 0; i < data.length; i++) {
    crc ^= data[i] << 8;
    for (let b = 0; b < 8; b++) {
      if (crc & 0x8000) crc = ((crc << 1) ^ 0x1021) & 0xffff;
      else crc = (crc << 1) & 0xffff;
    }
  }
  const crcBytes = new Uint8Array([crc & 0xff, (crc >> 8) & 0xff]);
  const full = concat(data, crcBytes);
  const B32S = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const b of full) bits += b.toString(2).padStart(8, "0");
  let out = "";
  for (let i = 0; i + 5 <= bits.length; i += 5) out += B32S[parseInt(bits.slice(i, i + 5), 2)];
  return out;
}

/** Stellar XLM SEP-0005 m/44'/148'/0' */
export function xlmAddressFromMnemonic(phrase, account = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = deriveEd25519Path(seed, [44 | 0x80000000, 148 | 0x80000000, account | 0x80000000]);
  const pub = ed25519.getPublicKey(priv);
  return {
    address: stellarStrKeyEncode(6 << 3, pub),
    privateKeyHex: bytesToHex(priv),
    path: `m/44'/148'/${account}'`,
  };
}

/** Multi-chain deposit assets (receive addresses + balances where APIs allow). */
export const PRESET_ASSETS = [
  { id: "howl", symbol: "HOWL", name: "Howlcoin", network: "Howlcoin", kind: "howl", logo: "/assets/token-logos/howl.png" },
  { logo: "/assets/token-logos/btc.png", id: "btc", symbol: "BTC", name: "Bitcoin", network: "Bitcoin", kind: "utxo", coinId: "btc" },
  { logo: "/assets/token-logos/ltc.png", id: "ltc", symbol: "LTC", name: "Litecoin", network: "Litecoin", kind: "utxo", coinId: "ltc" },
  { logo: "/assets/token-logos/bch.png", id: "bch", symbol: "BCH", name: "Bitcoin Cash", network: "Bitcoin Cash", kind: "utxo", coinId: "bch" },
  { logo: "/assets/token-logos/doge.png", id: "doge", symbol: "DOGE", name: "Dogecoin", network: "Dogecoin", kind: "utxo", coinId: "doge" },
  { logo: "/assets/token-logos/sol.png", id: "sol", symbol: "SOL", name: "Solana", network: "Solana", kind: "sol" },
  { logo: "/assets/token-logos/usdt_sol.png", id: "usdt_sol", symbol: "USDT", name: "Tether (Solana)", network: "Solana (SPL)", kind: "spl", mint: "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", decimals: 6 },
  { logo: "/assets/token-logos/usdc_sol.png", id: "usdc_sol", symbol: "USDC", name: "USD Coin (Solana)", network: "Solana (SPL)", kind: "spl", mint: "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", decimals: 6 },
  { id: "whowl", symbol: "wHOWL", name: "Wrapped HOWL", network: "Solana (SPL)", kind: "spl", mint: "HYRKhV2Y9HEtKCCHSgH18Zfo4U9Ln9vAg2dCmBJSLWaG", decimals: 8, chartId: "howlcoin", coingeckoId: "howlcoin", note: "1 wHOWL ≈ 1 HOWL via Howl Wrap", logo: "/assets/token-logos/whowl.png" },
  { logo: "/assets/token-logos/eth.png", id: "eth", symbol: "ETH", name: "Ethereum", network: "Ethereum", kind: "evm", evmId: "eth" },
  { logo: "/assets/token-logos/op.png", id: "op", symbol: "ETH", name: "Ether (Optimism)", network: "Optimism", kind: "evm", evmId: "op", displaySymbol: "OP-ETH" },
  { logo: "/assets/token-logos/base.png", id: "base", symbol: "ETH", name: "Ether (Base)", network: "Base", kind: "evm", evmId: "base", displaySymbol: "BASE-ETH" },
  { logo: "/assets/token-logos/bnb.png", id: "bnb", symbol: "BNB", name: "BNB", network: "BNB Chain", kind: "evm", evmId: "bnb" },
  { logo: "/assets/token-logos/avax.png", id: "avax", symbol: "AVAX", name: "Avalanche", network: "Avalanche C-Chain", kind: "evm", evmId: "avax" },
  { id: "hype", symbol: "HYPE", name: "Hyperliquid", network: "HyperEVM", kind: "evm", evmId: "hype", logo: "/assets/token-logos/eth.png" },
  { logo: "/assets/token-logos/usdt.png", id: "usdt", symbol: "USDT", name: "Tether", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0xdac17f958d2ee523a2206206994597c13d831ec7", decimals: 6 },
  { logo: "/assets/token-logos/usdc.png", id: "usdc", symbol: "USDC", name: "USD Coin", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48", decimals: 6 },
  { logo: "/assets/token-logos/dai.png", id: "dai", symbol: "DAI", name: "Dai", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0x6b175474e89094c44da98b954eedeac495271d0f", decimals: 18 },
  { logo: "/assets/token-logos/shib.png", id: "shib", symbol: "SHIB", name: "Shiba Inu", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0x95ad61b0a150d79219dcf64e1e6cc01f0b64c4ce", decimals: 18 },
  { logo: "/assets/token-logos/leo.png", id: "leo", symbol: "LEO", name: "UNUS SED LEO", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0x2af5d2ad76741191d15dfe7bf6ac92d4bd912ca3", decimals: 18 },
  { logo: "/assets/token-logos/wbtc.png", id: "wbtc", symbol: "WBTC", name: "Wrapped Bitcoin", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0x2260fac5e5542a773aa44fbcfedf7c193bc2c599", decimals: 8 },
  { logo: "/assets/token-logos/link.png", id: "link", symbol: "LINK", name: "Chainlink", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0x514910771af9ca656af840dff83e8264ecf986ca", decimals: 18 },
  { logo: "/assets/token-logos/uni.png", id: "uni", symbol: "UNI", name: "Uniswap", network: "Ethereum (ERC-20)", kind: "erc20", contract: "0x1f9840a85d5af5bf1d1762f925bdaddc4201f984", decimals: 18 },
  { logo: "/assets/token-logos/xtz.png", id: "xtz", symbol: "XTZ", name: "Tezos", network: "Tezos", kind: "xtz" },
  { logo: "/assets/token-logos/trx.png", id: "trx", symbol: "TRX", name: "TRON", network: "TRON", kind: "trx" },
  { logo: "/assets/token-logos/xrp.png", id: "xrp", symbol: "XRP", name: "XRP", network: "XRP Ledger", kind: "xrp" },
  { logo: "/assets/token-logos/xlm.png", id: "xlm", symbol: "XLM", name: "Stellar", network: "Stellar", kind: "xlm" },
  { id: "hbar", symbol: "HBAR", name: "Hedera", network: "Hedera (EVM alias)", kind: "evm", evmId: "eth", logo: "/assets/token-logos/hbar.png", note: "EVM 0x alias — link in HashPack if needed." },
  { id: "ada", symbol: "ADA", name: "Cardano", network: "Cardano", kind: "external", explorer: "https://cardanoscan.io/", logo: "/assets/token-logos/ada.png", note: "Use Yoroi/Eternl with this seed if supported." },
  { id: "xmr", symbol: "XMR", name: "Monero", network: "Monero", kind: "external", explorer: "https://xmrchain.net/", logo: "/assets/token-logos/xmr.png", note: "Use an official Monero wallet (different address scheme)." },
  { id: "zec", symbol: "ZEC", name: "Zcash", network: "Zcash", kind: "external", explorer: "https://explorer.zcha.in/", logo: "/assets/token-logos/zec.png", note: "Use a Zcash wallet for transparent/shielded addresses." },
];

export function txSighash(txBody) {
  const body = {
    amount: txBody.amount,
    fee: txBody.fee ?? 0,
    from: txBody.from,
    memo: txBody.memo ?? "",
    nonce: txBody.nonce,
    to: txBody.to,
  };
  const txType = txBody.type || "transfer";
  if (txType && txType !== "transfer") body.type = txType;
  for (const k of [
    "nft_id",
    "name",
    "uri",
    "oracle_key",
    "oracle_value",
    "source_chain",
    "observed_at",
    "contract_id",
    "contract_kind",
    "method",
    "unlock_height",
    "counterparty",
    "arbiter",
    "call_value",
    "bond_phrase",
    "min_join",
  ]) {
    if (txBody[k] != null && txBody[k] !== "") body[k] = txBody[k];
  }
  const keys = Object.keys(body).sort();
  const json =
    "{" + keys.map((k) => JSON.stringify(k) + ":" + JSON.stringify(body[k])).join(",") + "}";
  return sha256(new TextEncoder().encode(json));
}

export async function signTxBody(privateKeyHex, txBody) {
  const msg = txSighash(txBody);
  // Python ecdsa sign_deterministic(..., hashfunc=sha256) hashes message again
  const digest = sha256(msg);
  const priv = hexToBytes(privateKeyHex);
  // noble v1 returns compact 64-byte sig Uint8Array
  const sig = await secp.sign(digest, priv, { der: false, recovered: false });
  const sigBytes = sig instanceof Uint8Array ? sig : sig;
  return bytesToHex(sigBytes);
}

export function txId(tx) {
  const keys = Object.keys(tx).sort();
  const json = "{" + keys.map((k) => JSON.stringify(k) + ":" + JSON.stringify(tx[k])).join(",") + "}";
  return bytesToHex(sha256(new TextEncoder().encode(json)));
}

export async function buildSignedTx({
  keypair,
  to,
  amountHowlies,
  feeHowlies,
  nonce,
  memo = "",
  type = "transfer",
  extra = {},
}) {
  if (!isValidAddress(to)) throw new Error("Invalid HOWL address");
  if (type === "transfer" && amountHowlies <= 0) throw new Error("Amount must be positive");
  const body = {
    from: keypair.address,
    to,
    amount: amountHowlies,
    fee: feeHowlies,
    nonce,
    memo,
    public_key: keypair.publicKeyHex,
  };
  if (type && type !== "transfer") body.type = type;
  for (const [k, v] of Object.entries(extra || {})) {
    if (v != null && v !== "") body[k] = v;
  }
  const signature = await signTxBody(keypair.privateKeyHex, body);
  const tx = { ...body, signature };
  tx.txid = txId(tx);
  return tx;
}

/** Deterministic nft id from creator + name + uri + nonce */
export function makeNftId(creator, name, uri, nonce) {
  const raw = `${creator}|${name}|${uri}|${nonce}`;
  return bytesToHex(sha256(new TextEncoder().encode(raw))).slice(0, 32);
}

/** Deterministic Howl contract id from creator + kind + name + nonce */
export function makeContractId(creator, kind, name, nonce) {
  const raw = `hc|${creator}|${kind}|${name}|${nonce}`;
  return "hc" + bytesToHex(sha256(new TextEncoder().encode(raw))).slice(0, 30);
}

/** Tezos tz1 from same mnemonic (ed25519 SLIP-0010 m/44'/1729'/0'/0') */
export function tezosPath(account = 0) {
  return [
    44 | 0x80000000,
    1729 | 0x80000000,
    account | 0x80000000,
    0 | 0x80000000,
  ];
}

function base58CheckEncode(payload) {
  const checksum = doubleSha256(payload).slice(0, 4);
  return base58Encode(concat(payload, checksum));
}

export function tezosAddressFromMnemonic(phrase, account = 0, passphrase = "") {
  const norm = phrase.trim().toLowerCase().split(/\s+/).join(" ");
  if (!validateMnemonic(norm, wordlist)) throw new Error("Invalid BIP39 mnemonic");
  const seed = mnemonicToSeedSync(norm, passphrase);
  const priv = deriveEd25519Path(seed, tezosPath(account));
  const pub = ed25519.getPublicKey(priv);
  // tz1 = base58check( 0x06 0xa1 0x9f || blake2b-160(pubkey) )
  let pkh;
  try {
    pkh = blake2b(pub, { dkLen: 20 });
  } catch {
    // some CDN builds only expose the hasher factory
    pkh = blake2b.create({ dkLen: 20 }).update(pub).digest();
  }
  if (!pkh || pkh.length !== 20) {
    throw new Error("Tezos blake2b-160 failed (got " + (pkh && pkh.length) + " bytes)");
  }
  const payload = concat(new Uint8Array([6, 161, 159]), pkh);
  const address = base58CheckEncode(payload);
  if (!address.startsWith("tz1")) {
    throw new Error("Unexpected Tezos address prefix: " + address.slice(0, 4));
  }
  return {
    address,
    privateKeyHex: bytesToHex(priv),
    publicKeyHex: bytesToHex(pub),
    path: `m/44'/1729'/${account}'/0'`,
  };
}

export function parseHowl(text) {
  let t = String(text).trim().toUpperCase().replace("HOWL", "").trim();
  if (!t) throw new Error("empty amount");
  if (t.includes(".")) {
    let [left, right] = t.split(".");
    right = (right + "00000000").slice(0, 8);
    return BigInt(left || "0") * 100000000n + BigInt(right);
  }
  return BigInt(t) * 100000000n;
}

export function formatHowl(howlies) {
  const n = BigInt(howlies);
  const neg = n < 0n;
  const v = neg ? -n : n;
  const whole = v / 100000000n;
  const frac = (v % 100000000n).toString().padStart(8, "0");
  return `${neg ? "-" : ""}${whole}.${frac} HOWL`;
}

export const COIN = 100000000n;
export { bytesToHex, hexToBytes };
