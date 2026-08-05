import { bytesToHex } from '@noble/hashes/utils'
import { sha256 } from '@noble/hashes/sha256'

const VAULT_KEY = 'howl_pack_vault_v1'
const PBKDF2_ITERS = 120_000

export interface VaultAccountMeta {
  index: number
  label: string
  address: string
}

export interface VaultPlain {
  version: 1
  mnemonic: string
  accounts: VaultAccountMeta[]
  createdAt: number
}

export interface VaultBlob {
  v: 1
  salt: string
  iv: string
  cipher: string
  accounts: VaultAccountMeta[]
  createdAt: number
}

function toB64(u8: Uint8Array): string {
  let s = ''
  u8.forEach((b) => { s += String.fromCharCode(b) })
  return btoa(s)
}

function fromB64(b64: string): Uint8Array {
  const s = atob(b64)
  const out = new Uint8Array(s.length)
  for (let i = 0; i < s.length; i++) out[i] = s.charCodeAt(i)
  return out
}

async function deriveKey(password: string, salt: Uint8Array): Promise<CryptoKey> {
  const enc = new TextEncoder()
  const baseKey = await crypto.subtle.importKey('raw', enc.encode(password), 'PBKDF2', false, ['deriveKey'])
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: PBKDF2_ITERS, hash: 'SHA-256' },
    baseKey,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

export function vaultFingerprint(mnemonic: string): string {
  return bytesToHex(sha256(new TextEncoder().encode(mnemonic))).slice(0, 16)
}

export function loadVaultBlob(): VaultBlob | null {
  try {
    const raw = localStorage.getItem(VAULT_KEY)
    if (!raw) return null
    return JSON.parse(raw) as VaultBlob
  } catch {
    return null
  }
}

export function hasVault(): boolean {
  return !!loadVaultBlob()
}

export async function encryptVault(plain: VaultPlain, password: string): Promise<VaultBlob> {
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const key = await deriveKey(password, salt)
  const data = new TextEncoder().encode(JSON.stringify(plain))
  const cipherBuf = await crypto.subtle.encrypt({ name: 'AES-GCM', iv }, key, data)
  const blob: VaultBlob = {
    v: 1,
    salt: toB64(salt),
    iv: toB64(iv),
    cipher: toB64(new Uint8Array(cipherBuf)),
    accounts: plain.accounts,
    createdAt: plain.createdAt,
  }
  localStorage.setItem(VAULT_KEY, JSON.stringify(blob))
  return blob
}

export async function decryptVault(password: string): Promise<VaultPlain> {
  const blob = loadVaultBlob()
  if (!blob) throw new Error('No wallet found')
  const salt = fromB64(blob.salt)
  const iv = fromB64(blob.iv)
  const cipher = fromB64(blob.cipher)
  const key = await deriveKey(password, salt)
  try {
    const plainBuf = await crypto.subtle.decrypt({ name: 'AES-GCM', iv }, key, cipher)
    return JSON.parse(new TextDecoder().decode(plainBuf)) as VaultPlain
  } catch {
    throw new Error('Wrong password')
  }
}

export function clearVaultStorage(): void {
  localStorage.removeItem(VAULT_KEY)
}
