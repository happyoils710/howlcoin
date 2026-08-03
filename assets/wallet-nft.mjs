/**
 * Multi-chain NFT helpers for Howl public wallet.
 * Solana: list NFT-like token accounts + transfer amount 1.
 * EVM: ERC-721 tokenURI / transferFrom encoding + legacy eth tx sign/send.
 */
import {
  Connection,
  Keypair,
  PublicKey,
  Transaction,
  SystemProgram,
} from "https://esm.sh/@solana/web3.js@1.95.4?target=es2022";
import {
  TOKEN_PROGRAM_ID,
  ASSOCIATED_TOKEN_PROGRAM_ID,
  getAssociatedTokenAddress,
  createAssociatedTokenAccountInstruction,
  createTransferInstruction,
  getAccount,
} from "https://esm.sh/@solana/spl-token@0.4.9?target=es2022";
import { keccak_256 } from "https://esm.sh/@noble/hashes@1.4.0/sha3";
import * as secp from "https://esm.sh/@noble/secp256k1@1.7.1";

const TOKEN_PROG = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA";

function hexToBytes(h) {
  const s = String(h || "").replace(/^0x/, "");
  const out = new Uint8Array(s.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(s.slice(i * 2, i * 2 + 2), 16);
  return out;
}
function bytesToHex(b) {
  return Array.from(b).map((x) => x.toString(16).padStart(2, "0")).join("");
}
function pad32(hexOrAddr) {
  let h = String(hexOrAddr || "").replace(/^0x/, "").toLowerCase();
  if (h.length > 64) h = h.slice(-64);
  return h.padStart(64, "0");
}

/** List Solana NFT-like balances (decimals 0, amount 1) for owner. */
export async function listSolNfts(owner, { rpc, solRpc } = {}) {
  let accounts = [];
  if (typeof solRpc === "function") {
    const res = await solRpc("getTokenAccountsByOwner", [
      owner,
      { programId: TOKEN_PROG },
      { encoding: "jsonParsed", commitment: "confirmed" },
    ]);
    accounts = res?.value || [];
  } else {
    const connection = new Connection(rpc || "https://api.mainnet-beta.solana.com", "confirmed");
    const res = await connection.getParsedTokenAccountsByOwner(new PublicKey(owner), {
      programId: new PublicKey(TOKEN_PROG),
    });
    accounts = res.value || [];
  }
  const nfts = [];
  for (const row of accounts) {
    // RPC jsonParsed OR web3.js getParsedTokenAccountsByOwner
    const parsed =
      row?.account?.data?.parsed?.info ||
      row?.account?.data?.parsed?.info ||
      null;
    const ta = parsed?.tokenAmount || {};
    const rawAmt = ta.amount != null ? Number(ta.amount) : NaN;
    const uiAmt = ta.uiAmount != null ? Number(ta.uiAmount) : NaN;
    const decimals = Number(ta.decimals ?? 9);
    // classic Metaplex / SPL NFT: 0 decimals and balance 1
    const isNft =
      decimals === 0 &&
      (rawAmt === 1 || uiAmt === 1 || (Number.isFinite(uiAmt) && uiAmt === 1));
    if (!isNft) continue;
    const mint = parsed?.mint || "";
    if (!mint) continue;
    nfts.push({
      chain: "solana",
      kind: "spl-nft",
      mint,
      tokenAccount: row.pubkey?.toString?.() || row.pubkey || "",
      amount: 1,
      name: "Solana NFT",
      uri: "",
      image: "",
      id: mint,
    });
  }
  return nfts;
}

/** Transfer 1 SPL token (NFT) to recipient address. */
export async function transferSolNft({
  privateKeyHex,
  mint,
  toOwner,
  rpc = "https://api.mainnet-beta.solana.com",
}) {
  const seed = hexToBytes(privateKeyHex);
  if (seed.length !== 32) throw new Error("Invalid Solana private key");
  const kp = Keypair.fromSeed(seed);
  const connection = new Connection(rpc, "confirmed");
  const mintPk = new PublicKey(mint);
  const toPk = new PublicKey(toOwner);
  const fromAta = await getAssociatedTokenAddress(mintPk, kp.publicKey);
  const toAta = await getAssociatedTokenAddress(mintPk, toPk);

  const tx = new Transaction();
  // create destination ATA if missing
  let needCreate = false;
  try {
    await getAccount(connection, toAta);
  } catch {
    needCreate = true;
  }
  if (needCreate) {
    tx.add(
      createAssociatedTokenAccountInstruction(
        kp.publicKey,
        toAta,
        toPk,
        mintPk,
        TOKEN_PROGRAM_ID,
        ASSOCIATED_TOKEN_PROGRAM_ID
      )
    );
  }
  tx.add(
    createTransferInstruction(fromAta, toAta, kp.publicKey, 1n, [], TOKEN_PROGRAM_ID)
  );
  const { blockhash, lastValidBlockHeight } = await connection.getLatestBlockhash("confirmed");
  tx.recentBlockhash = blockhash;
  tx.feePayer = kp.publicKey;
  tx.sign(kp);
  const sig = await connection.sendRawTransaction(tx.serialize(), {
    skipPreflight: false,
    maxRetries: 3,
  });
  try {
    await connection.confirmTransaction({ signature: sig, blockhash, lastValidBlockHeight }, "confirmed");
  } catch (_) {}
  return sig;
}

/* —— EVM ERC-721 —— */
export function erc721OwnerOfData(tokenId) {
  const id = BigInt(tokenId);
  return "0x6352211e" + pad32(id.toString(16));
}
export function erc721TokenURIData(tokenId) {
  const id = BigInt(tokenId);
  return "0xc87b56dd" + pad32(id.toString(16));
}
export function erc721TransferFromData(from, to, tokenId) {
  const id = BigInt(tokenId);
  return (
    "0x23b872dd" +
    pad32(from) +
    pad32(to) +
    pad32(id.toString(16))
  );
}

/** Minimal RLP encode for eth legacy txs */
function rlpEncode(input) {
  if (input instanceof Uint8Array) return rlpEncodeBytes(input);
  if (typeof input === "string") {
    if (input.startsWith("0x")) return rlpEncodeBytes(hexToBytes(input));
    return rlpEncodeBytes(new TextEncoder().encode(input));
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
  throw new Error("rlp: bad type");
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

/**
 * Sign + send legacy eth tx (type 0) for ERC-721 transferFrom.
 */
export async function sendErc721Transfer({
  privateKeyHex,
  from,
  to,
  contract,
  tokenId,
  rpc,
  chainId = 1,
  gasLimit = 120000n,
}) {
  if (!rpc) throw new Error("RPC required");
  const data = erc721TransferFromData(from, to, tokenId);
  // nonce
  const nonceHex = await ethRpc(rpc, "eth_getTransactionCount", [from, "latest"]);
  const nonce = BigInt(nonceHex || "0x0");
  // gas price
  let gasPriceHex = await ethRpc(rpc, "eth_gasPrice", []);
  let gasPrice = BigInt(gasPriceHex || "0x3b9aca00"); // 1 gwei fallback
  // bump slightly
  gasPrice = (gasPrice * 12n) / 10n;

  const toBytes = hexToBytes(contract);
  const dataBytes = hexToBytes(data);
  // unsigned payload for EIP-155
  const unsigned = [
    nonce,
    gasPrice,
    gasLimit,
    toBytes,
    0n,
    dataBytes,
    BigInt(chainId),
    0n,
    0n,
  ];
  const rlpUnsigned = rlpEncode(unsigned);
  const msgHash = keccak_256(rlpUnsigned);
  const priv = hexToBytes(privateKeyHex);
  // @noble/secp256k1 v1: recovered sig is [compact64, recovery]
  let sigBytes;
  let recovery = 0;
  if (typeof secp.signSync === "function") {
    const out = secp.signSync(msgHash, priv, { recovered: true, der: false });
    if (Array.isArray(out)) {
      sigBytes = out[0];
      recovery = out[1];
    } else {
      sigBytes = out;
    }
  } else {
    const out = await secp.sign(msgHash, priv, { recovered: true, der: false });
    if (Array.isArray(out)) {
      sigBytes = out[0];
      recovery = out[1];
    } else {
      sigBytes = out;
    }
  }
  if (!(sigBytes instanceof Uint8Array) || sigBytes.length < 64) {
    throw new Error("bad eth signature");
  }
  const r = sigBytes.slice(0, 32);
  const s = sigBytes.slice(32, 64);
  const v = BigInt(recovery) + 35n + BigInt(chainId) * 2n;
  const signed = [nonce, gasPrice, gasLimit, toBytes, 0n, dataBytes, v, r, s];
  const raw = "0x" + bytesToHex(rlpEncode(signed));
  const txHash = await ethRpc(rpc, "eth_sendRawTransaction", [raw]);
  return txHash;
}

async function ethRpc(rpc, method, params) {
  const r = await fetch(rpc, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }),
  });
  const j = await r.json();
  if (j.error) throw new Error(j.error.message || "eth rpc error");
  return j.result;
}

/** eth_call helper */
export async function ethCall(rpc, to, data) {
  return ethRpc(rpc, "eth_call", [{ to, data }, "latest"]);
}

/** Decode ABI string return (tokenURI) best-effort */
export function decodeAbiString(hex) {
  if (!hex || hex === "0x") return "";
  try {
    const h = hex.replace(/^0x/, "");
    // offset at 0, length at 32, data after
    if (h.length < 128) return "";
    const len = parseInt(h.slice(64, 128), 16);
    const data = h.slice(128, 128 + len * 2);
    const bytes = hexToBytes(data);
    return new TextDecoder().decode(bytes).replace(/\0/g, "");
  } catch {
    return "";
  }
}

/**
 * Transfer FA2 NFT on Tezos mainnet (needs small XTZ for fees).
 * Uses BIP39 mnemonic path 44'/1729'/0'/0' (matches Howl wallet tz1).
 */
export async function transferTezosFa2({
  mnemonic,
  to,
  contract,
  tokenId,
  amount = 1,
  rpc = "https://rpc.tzkt.io/mainnet",
}) {
  const phrase = String(mnemonic || "")
    .trim()
    .toLowerCase()
    .split(/\s+/)
    .join(" ");
  if (!phrase) throw new Error("Recovery phrase required for Tezos send");
  const dest = String(to || "").trim();
  if (!/^tz[123][1-9A-HJ-NP-Za-km-z]{33}$/.test(dest)) {
    throw new Error("Recipient must be tz1 / tz2 / tz3 (not KT1)");
  }
  const kt = String(contract || "").trim();
  if (!/^KT1[1-9A-HJ-NP-Za-km-z]{33}$/.test(kt)) {
    throw new Error("Invalid FA2 contract (KT1…)");
  }
  const tid = Number(tokenId);
  if (!Number.isFinite(tid) || tid < 0) throw new Error("Invalid token id");
  const amt = Number(amount);
  if (!Number.isFinite(amt) || amt <= 0) throw new Error("Amount must be > 0");

  const { TezosToolkit } = await import(
    "https://esm.sh/@taquito/taquito@20.0.1?target=es2022&bundle"
  );
  const { InMemorySigner } = await import(
    "https://esm.sh/@taquito/signer@20.0.1?target=es2022&bundle"
  );

  const Tezos = new TezosToolkit(rpc);
  let signer;
  try {
    signer = await InMemorySigner.fromMnemonic({
      mnemonic: phrase,
      password: "",
      derivationPath: "44'/1729'/0'/0'",
    });
  } catch (e) {
    // older taquito path style
    try {
      signer = await InMemorySigner.fromMnemonic({
        mnemonic: phrase,
        password: "",
        derivationPath: "m/44'/1729'/0'/0'",
      });
    } catch (e2) {
      throw new Error(
        "Could not derive Tezos key: " + (e2.message || e.message || e)
      );
    }
  }
  Tezos.setProvider({ signer });
  const from = await signer.publicKeyHash();
  if (!from || !from.startsWith("tz")) {
    throw new Error("Bad Tezos address from seed");
  }

  // Ensure some XTZ for fees
  try {
    const bal = await Tezos.tz.getBalance(from);
    if (bal.toNumber() < 50000) {
      // ~0.05 XTZ mutez floor for safety
      throw new Error(
        "Need a little XTZ for fees on " +
          from.slice(0, 8) +
          "… (deposit ~0.1 XTZ, then retry)"
      );
    }
  } catch (e) {
    if (String(e.message || e).includes("XTZ for fees")) throw e;
    // continue — node may still work
  }

  const c = await Tezos.contract.at(kt);
  // Standard FA2 %transfer
  let op;
  try {
    op = await c.methodsObject
      .transfer([
        {
          from_: from,
          txs: [{ to_: dest, token_id: tid, amount: amt }],
        },
      ])
      .send();
  } catch (e1) {
    // Fallback classic methods.transfer
    try {
      op = await c.methods
        .transfer([
          {
            from_: from,
            txs: [{ to_: dest, token_id: tid, amount: amt }],
          },
        ])
        .send();
    } catch (e2) {
      const msg = e2.message || e1.message || String(e2);
      if (/balance|funds|mutez/i.test(msg)) {
        throw new Error("Not enough XTZ for fees or FA2 transfer failed: " + msg);
      }
      throw new Error("FA2 transfer failed: " + msg);
    }
  }
  try {
    await op.confirmation(1);
  } catch {
    /* hash still useful */
  }
  return { hash: op.hash || op.opHash || "", from, to: dest, contract: kt, tokenId: tid };
}

export { TOKEN_PROG };
