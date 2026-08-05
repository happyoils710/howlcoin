/**
 * Shared vault with classic /classic wallet (howl_public_wallet_v2).
 * Same PBKDF2 + AES-GCM parameters so one PIN unlocks both UIs.
 */
const LS_ENC = 'howl_public_wallet_v2'
const LS_META = 'howl_public_wallet_meta_v2'
const LS_CREATED = 'howl_public_wallet_ready_v2'
const LS_INDEX = 'howl_wallet_index_v1'
const PBKDF2_ITERS = 120_000

export interface ClassicWalletEntry {
  id: string
  name: string
  mnemonic?: string | null
  privateKeyHex?: string | null
  totpSecret?: string | null
  totpEnabled?: boolean
  createdAt?: number
}

export interface ClassicMultiVault {
  version: 3
  activeId: string
  wallets: ClassicWalletEntry[]
}

function b64(u8: Uint8Array): string {
  let s = ''
  u8.forEach((b) => {
    s += String.fromCharCode(b)
  })
  return btoa(s)
}

function unb64(s: string): Uint8Array {
  const bin = atob(s)
  const out = new Uint8Array(bin.length)
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i)
  return out
}

function newWalletId(): string {
  return (
    'w_' +
    Array.from(crypto.getRandomValues(new Uint8Array(6)))
      .map((b) => b.toString(16).padStart(2, '0'))
      .join('')
  )
}

async function deriveKey(pin: string, salt: Uint8Array): Promise<CryptoKey> {
  const base = await crypto.subtle.importKey('raw', new TextEncoder().encode(pin), 'PBKDF2', false, [
    'deriveKey',
  ])
  return crypto.subtle.deriveKey(
    { name: 'PBKDF2', salt, iterations: PBKDF2_ITERS, hash: 'SHA-256' },
    base,
    { name: 'AES-GCM', length: 256 },
    false,
    ['encrypt', 'decrypt'],
  )
}

export function hasClassicVault(): boolean {
  try {
    return !!(
      localStorage.getItem(LS_ENC) ||
      localStorage.getItem(LS_CREATED) === '1' ||
      localStorage.getItem('howl_pack_vault_v1')
    )
  } catch {
    return false
  }
}

/** If user only has Pack v1 vault, migrate into classic format on unlock. */
export async function tryMigratePackVault(pin: string): Promise<ClassicMultiVault | null> {
  const raw = localStorage.getItem('howl_pack_vault_v1')
  if (!raw) return null
  try {
    const blob = JSON.parse(raw) as { salt: string; iv: string; cipher: string }
    const salt = unb64(blob.salt)
    const iv = unb64(blob.iv)
    const cipher = unb64(blob.cipher)
    const key = await deriveKey(pin, salt)
    const plainBuf = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: iv as BufferSource },
      key,
      cipher as BufferSource,
    )
    const plain = JSON.parse(new TextDecoder().decode(plainBuf)) as { mnemonic?: string }
    if (!plain.mnemonic) return null
    const multi = await createClassicVault(plain.mnemonic, pin)
    return multi
  } catch {
    return null
  }
}

export function loadWalletIndex(): { activeId: string | null; wallets: { id: string; name: string; address: string }[] } | null {
  try {
    return JSON.parse(localStorage.getItem(LS_INDEX) || 'null')
  } catch {
    return null
  }
}

function normalizeMultiVault(obj: unknown): ClassicMultiVault {
  if (typeof obj === 'string') {
    const id = newWalletId()
    return {
      version: 3,
      activeId: id,
      wallets: [{ id, name: 'Main', mnemonic: obj, totpSecret: null, totpEnabled: false, createdAt: Date.now() }],
    }
  }
  const o = obj as ClassicMultiVault & { mnemonic?: string; privateKeyHex?: string }
  if (o && o.version === 3 && Array.isArray(o.wallets) && o.wallets.length) {
    if (!o.activeId || !o.wallets.some((w) => w.id === o.activeId)) o.activeId = o.wallets[0].id
    return o
  }
  const phrase = o?.mnemonic
  const priv = o?.privateKeyHex
  if (!phrase && !priv) throw new Error('Invalid vault')
  const id = newWalletId()
  return {
    version: 3,
    activeId: id,
    wallets: [
      {
        id,
        name: 'Main',
        mnemonic: phrase || null,
        privateKeyHex: priv || null,
        totpSecret: (o as { totpSecret?: string }).totpSecret || null,
        totpEnabled: !!(o as { totpEnabled?: boolean }).totpEnabled,
        createdAt: Date.now(),
      },
    ],
  }
}

export async function loadClassicVault(pin: string): Promise<ClassicMultiVault> {
  const raw = localStorage.getItem(LS_ENC)
  if (!raw) throw new Error('No wallet')
  const { salt, iv, data } = JSON.parse(raw) as { salt: string; iv: string; data: string }
  const key = await deriveKey(pin, unb64(salt))
  try {
    const pt = await crypto.subtle.decrypt(
      { name: 'AES-GCM', iv: unb64(iv) as BufferSource },
      key,
      unb64(data) as BufferSource,
    )
    const obj = JSON.parse(new TextDecoder().decode(pt))
    return normalizeMultiVault(typeof obj === 'string' ? obj : obj)
  } catch {
    throw new Error('Wrong password')
  }
}

export async function saveClassicVault(multi: ClassicMultiVault, pin: string): Promise<void> {
  const normalized = normalizeMultiVault(multi)
  const salt = crypto.getRandomValues(new Uint8Array(16))
  const iv = crypto.getRandomValues(new Uint8Array(12))
  const key = await deriveKey(pin, salt)
  const ct = await crypto.subtle.encrypt(
    { name: 'AES-GCM', iv },
    key,
    new TextEncoder().encode(JSON.stringify(normalized)),
  )
  localStorage.setItem(
    LS_ENC,
    JSON.stringify({ salt: b64(salt), iv: b64(iv), data: b64(new Uint8Array(ct)) }),
  )
  localStorage.setItem(LS_CREATED, '1')
  try {
    const idx = {
      activeId: normalized.activeId,
      wallets: normalized.wallets.map((w) => ({
        id: w.id,
        name: w.name || 'Wallet',
        address: '',
        createdAt: w.createdAt || 0,
      })),
    }
    localStorage.setItem(LS_INDEX, JSON.stringify(idx))
  } catch {
    /* ignore */
  }
  try {
    const meta = JSON.parse(localStorage.getItem(LS_META) || '{}')
    localStorage.setItem(LS_META, JSON.stringify(meta))
  } catch {
    /* ignore */
  }
}

export async function createClassicVault(mnemonic: string, pin: string, name = 'Main'): Promise<ClassicMultiVault> {
  if (pin.length < 4) throw new Error('PIN / password must be at least 4 characters')
  const id = newWalletId()
  const multi: ClassicMultiVault = {
    version: 3,
    activeId: id,
    wallets: [
      {
        id,
        name,
        mnemonic,
        totpSecret: null,
        totpEnabled: false,
        createdAt: Date.now(),
      },
    ],
  }
  await saveClassicVault(multi, pin)
  return multi
}

/** Hand off PIN to classic iframe for one-time auto-unlock (same origin). */
export function setSessionPin(pin: string): void {
  try {
    sessionStorage.setItem('howl_pack_session_pin', pin)
  } catch {
    /* ignore */
  }
}

export function clearSessionPin(): void {
  try {
    sessionStorage.removeItem('howl_pack_session_pin')
  } catch {
    /* ignore */
  }
}

export function wipeClassicVault(): void {
  localStorage.removeItem(LS_ENC)
  localStorage.removeItem(LS_CREATED)
  localStorage.removeItem(LS_INDEX)
  clearSessionPin()
}
