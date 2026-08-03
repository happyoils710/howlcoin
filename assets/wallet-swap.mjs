/**
 * Solana swaps via Jupiter + tx signing helpers for the public wallet.
 * Retries + multi-RPC for reliability.
 */
import { Connection, VersionedTransaction, Keypair } from "https://esm.sh/@solana/web3.js@1.95.4?target=es2022";

export const SOL_RPC = "https://api.mainnet-beta.solana.com";
export const SOL_RPC_FALLBACKS = [
  "https://api.mainnet-beta.solana.com",
  "https://solana-mainnet.rpc.extrnode.com",
  "https://rpc.ankr.com/solana",
];

/** Jupiter quote/swap hosts (try in order). */
export const JUP_QUOTE_URLS = [
  "https://lite-api.jup.ag/swap/v1/quote",
  "https://api.jup.ag/swap/v1/quote",
];
export const JUP_SWAP_URLS = [
  "https://lite-api.jup.ag/swap/v1/swap",
  "https://api.jup.ag/swap/v1/swap",
];

export const WSOL = "So11111111111111111111111111111111111111112";
export const USDC_SOL = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v";
export const USDT_SOL = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB";

export const SWAP_TOKENS = [
  { id: "sol", symbol: "SOL", mint: WSOL, decimals: 9 },
  { id: "usdc", symbol: "USDC", mint: USDC_SOL, decimals: 6 },
  { id: "usdt", symbol: "USDT", mint: USDT_SOL, decimals: 6 },
];

function hexToBytes(h) {
  const s = h.replace(/^0x/, "");
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  return out;
}

export function amountToRaw(amountStr, decimals) {
  const t = String(amountStr).trim();
  if (!t || Number(t) <= 0) throw new Error("Enter an amount");
  if (t.includes(".")) {
    let [a, b] = t.split(".");
    b = (b + "0".repeat(decimals)).slice(0, decimals);
    return BigInt(a || "0") * 10n ** BigInt(decimals) + BigInt(b || "0");
  }
  return BigInt(t) * 10n ** BigInt(decimals);
}

export function rawToUi(raw, decimals) {
  const n = BigInt(raw);
  const base = 10n ** BigInt(decimals);
  const whole = n / base;
  const frac = (n % base).toString().padStart(decimals, "0").replace(/0+$/, "");
  return frac ? `${whole}.${frac}` : `${whole}`;
}

async function fetchJson(url, opts = {}, timeoutMs = 20000) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const r = await fetch(url, { ...opts, signal: ctrl.signal });
    let j = {};
    try {
      j = await r.json();
    } catch {
      j = {};
    }
    return { r, j };
  } finally {
    clearTimeout(t);
  }
}

function jupErr(j, fallback) {
  if (!j) return fallback;
  if (typeof j.error === "string") return j.error;
  if (j.error && typeof j.error === "object") return j.error.message || JSON.stringify(j.error);
  if (j.message) return j.message;
  return fallback;
}

export async function getJupiterQuote({
  inputMint,
  outputMint,
  amountRaw,
  slippageBps = 50,
}) {
  const q = new URLSearchParams({
    inputMint,
    outputMint,
    amount: String(amountRaw),
    slippageBps: String(slippageBps),
  });
  let lastErr = "Quote failed";
  for (const base of JUP_QUOTE_URLS) {
    try {
      const { r, j } = await fetchJson(`${base}?${q}`, {}, 18000);
      if (!r.ok) {
        lastErr = jupErr(j, `Jupiter quote HTTP ${r.status}`);
        continue;
      }
      if (j.error) {
        lastErr = jupErr(j, "Quote error");
        continue;
      }
      if (!j.outAmount && !j.routePlan) {
        lastErr = "Empty Jupiter route";
        continue;
      }
      return j;
    } catch (e) {
      lastErr = e.name === "AbortError" ? "Jupiter quote timed out" : e.message || String(e);
    }
  }
  throw new Error(lastErr + " — try again in a moment");
}

export async function buildJupiterSwap({
  quoteResponse,
  userPublicKey,
  wrapAndUnwrapSol = true,
}) {
  const body = JSON.stringify({
    quoteResponse,
    userPublicKey,
    wrapAndUnwrapSol,
    dynamicComputeUnitLimit: true,
    prioritizationFeeLamports: "auto",
  });
  let lastErr = "Swap build failed";
  for (const base of JUP_SWAP_URLS) {
    try {
      const { r, j } = await fetchJson(
        base,
        { method: "POST", headers: { "Content-Type": "application/json" }, body },
        25000
      );
      if (!r.ok) {
        lastErr = jupErr(j, `Jupiter swap HTTP ${r.status}`);
        continue;
      }
      if (!j.swapTransaction) {
        lastErr = jupErr(j, "No swap transaction returned");
        continue;
      }
      return j;
    } catch (e) {
      lastErr = e.name === "AbortError" ? "Jupiter build timed out" : e.message || String(e);
    }
  }
  throw new Error(lastErr + " — try a new quote");
}

/** Pick first Solana RPC that answers getHealth / getSlot. */
export async function pickSolRpc(preferred) {
  const list = [];
  if (preferred) list.push(preferred);
  for (const u of SOL_RPC_FALLBACKS) if (!list.includes(u)) list.push(u);
  for (const rpc of list) {
    try {
      const { r, j } = await fetchJson(
        rpc,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ jsonrpc: "2.0", id: 1, method: "getSlot", params: [] }),
        },
        8000
      );
      if (r.ok && j && j.result != null) return rpc;
    } catch {
      /* try next */
    }
  }
  return list[0] || SOL_RPC;
}

/**
 * Sign + send a Jupiter base64 versioned transaction using 32-byte ed25519 seed.
 */
export async function signAndSendSolTx({
  swapTransactionBase64,
  privateKeyHex,
  rpc = SOL_RPC,
}) {
  const useRpc = await pickSolRpc(rpc);
  const connection = new Connection(useRpc, "confirmed");
  const seed = hexToBytes(privateKeyHex);
  if (seed.length !== 32) throw new Error("Invalid Solana private key length");
  const kp = Keypair.fromSeed(seed);

  const raw = Uint8Array.from(atob(swapTransactionBase64), (c) => c.charCodeAt(0));
  const tx = VersionedTransaction.deserialize(raw);
  tx.sign([kp]);

  let lastErr = "Broadcast failed";
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const sig = await connection.sendRawTransaction(tx.serialize(), {
        skipPreflight: false,
        maxRetries: 3,
      });
      try {
        await connection.confirmTransaction(sig, "confirmed");
      } catch {
        /* still return sig */
      }
      return sig;
    } catch (e) {
      lastErr = e.message || String(e);
      // try alternate RPC once
      if (attempt === 0) {
        const alt = await pickSolRpc(null);
        if (alt !== useRpc) {
          try {
            const c2 = new Connection(alt, "confirmed");
            const sig = await c2.sendRawTransaction(tx.serialize(), {
              skipPreflight: false,
              maxRetries: 2,
            });
            return sig;
          } catch (e2) {
            lastErr = e2.message || lastErr;
          }
        }
      }
      await new Promise((r) => setTimeout(r, 400 * (attempt + 1)));
    }
  }
  throw new Error(lastErr);
}
