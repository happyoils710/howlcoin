/**
 * Solana swaps via Jupiter + tx signing helpers for the public wallet.
 */
import { Connection, VersionedTransaction, Keypair } from "https://esm.sh/@solana/web3.js@1.95.4?target=es2022";

export const SOL_RPC = "https://api.mainnet-beta.solana.com";
export const JUP_QUOTE = "https://lite-api.jup.ag/swap/v1/quote";
export const JUP_SWAP = "https://lite-api.jup.ag/swap/v1/swap";

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

export async function getJupiterQuote({ inputMint, outputMint, amountRaw, slippageBps = 50 }) {
  const q = new URLSearchParams({
    inputMint,
    outputMint,
    amount: String(amountRaw),
    slippageBps: String(slippageBps),
  });
  const r = await fetch(`${JUP_QUOTE}?${q}`);
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || j.message || "Quote failed");
  if (j.error) throw new Error(typeof j.error === "string" ? j.error : JSON.stringify(j.error));
  return j;
}

export async function buildJupiterSwap({ quoteResponse, userPublicKey, wrapAndUnwrapSol = true }) {
  const r = await fetch(JUP_SWAP, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      quoteResponse,
      userPublicKey,
      wrapAndUnwrapSol,
      dynamicComputeUnitLimit: true,
      prioritizationFeeLamports: "auto",
    }),
  });
  const j = await r.json();
  if (!r.ok) throw new Error(j.error || j.message || "Swap build failed");
  if (!j.swapTransaction) throw new Error(j.error || "No swap transaction returned");
  return j;
}

/**
 * Sign + send a Jupiter base64 versioned transaction using 32-byte ed25519 seed.
 */
export async function signAndSendSolTx({ swapTransactionBase64, privateKeyHex, rpc = SOL_RPC }) {
  const connection = new Connection(rpc, "confirmed");
  const seed = hexToBytes(privateKeyHex);
  if (seed.length !== 32) throw new Error("Invalid Solana private key length");
  const kp = Keypair.fromSeed(seed);

  const raw = Uint8Array.from(atob(swapTransactionBase64), (c) => c.charCodeAt(0));
  const tx = VersionedTransaction.deserialize(raw);
  tx.sign([kp]);

  const sig = await connection.sendRawTransaction(tx.serialize(), {
    skipPreflight: false,
    maxRetries: 3,
  });
  // confirm (best-effort)
  try {
    await connection.confirmTransaction(sig, "confirmed");
  } catch (_) {
    /* still return sig */
  }
  return sig;
}
