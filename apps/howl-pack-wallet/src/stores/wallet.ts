import { create } from 'zustand'
import {
  createMnemonic, deriveEthAccount, isValidMnemonic, normalizeMnemonic, type DerivedAccount,
} from '@/lib/crypto/derive'
import {
  clearVaultStorage, decryptVault, encryptVault, hasVault, loadVaultBlob,
  type VaultAccountMeta, type VaultPlain,
} from '@/lib/crypto/vault'

interface WalletState {
  hasVault: boolean
  unlocked: boolean
  mnemonic: string | null
  accounts: VaultAccountMeta[]
  activeIndex: number
  derived: DerivedAccount | null
  lastActivity: number
  sessionPassword: string | null
  bootstrap: () => void
  createWallet: (password: string, strength?: 128 | 256) => Promise<string>
  importWallet: (mnemonic: string, password: string) => Promise<void>
  unlock: (password: string) => Promise<void>
  lock: () => void
  touch: () => void
  setActiveIndex: (index: number) => void
  addAccount: (label?: string) => Promise<void>
  exportMnemonic: () => string | null
  exportPrivateKey: () => string | null
  wipeWallet: () => void
}

async function reencrypt(mnemonic: string, accounts: VaultAccountMeta[], password: string) {
  const plain: VaultPlain = {
    version: 1, mnemonic, accounts, createdAt: loadVaultBlob()?.createdAt ?? Date.now(),
  }
  await encryptVault(plain, password)
}

export const useWallet = create<WalletState>((set, get) => ({
  hasVault: false,
  unlocked: false,
  mnemonic: null,
  accounts: [],
  activeIndex: 0,
  derived: null,
  lastActivity: Date.now(),
  sessionPassword: null,

  bootstrap: () => {
    const blob = loadVaultBlob()
    set({
      hasVault: hasVault(), accounts: blob?.accounts ?? [], unlocked: false,
      mnemonic: null, derived: null, sessionPassword: null,
    })
  },

  createWallet: async (password, strength = 128) => {
    if (password.length < 6) throw new Error('Password must be at least 6 characters')
    const mnemonic = createMnemonic(strength)
    const d0 = deriveEthAccount(mnemonic, 0)
    const accounts: VaultAccountMeta[] = [{ index: 0, label: 'Account 1', address: d0.address }]
    await encryptVault({ version: 1, mnemonic, accounts, createdAt: Date.now() }, password)
    set({
      hasVault: true, unlocked: true, mnemonic, accounts, activeIndex: 0,
      derived: d0, sessionPassword: password, lastActivity: Date.now(),
    })
    return mnemonic
  },

  importWallet: async (phrase, password) => {
    if (password.length < 6) throw new Error('Password must be at least 6 characters')
    const mnemonic = normalizeMnemonic(phrase)
    if (!isValidMnemonic(mnemonic)) throw new Error('Invalid recovery phrase')
    const d0 = deriveEthAccount(mnemonic, 0)
    const accounts: VaultAccountMeta[] = [{ index: 0, label: 'Account 1', address: d0.address }]
    await encryptVault({ version: 1, mnemonic, accounts, createdAt: Date.now() }, password)
    set({
      hasVault: true, unlocked: true, mnemonic, accounts, activeIndex: 0,
      derived: d0, sessionPassword: password, lastActivity: Date.now(),
    })
  },

  unlock: async (password) => {
    const plain = await decryptVault(password)
    const d = deriveEthAccount(plain.mnemonic, get().activeIndex)
    set({
      unlocked: true, mnemonic: plain.mnemonic, accounts: plain.accounts,
      derived: d, sessionPassword: password, lastActivity: Date.now(),
    })
  },

  lock: () => set({ unlocked: false, mnemonic: null, derived: null, sessionPassword: null }),
  touch: () => set({ lastActivity: Date.now() }),

  setActiveIndex: (index) => {
    const { mnemonic, accounts } = get()
    if (!mnemonic || !accounts.find((a) => a.index === index)) return
    set({ activeIndex: index, derived: deriveEthAccount(mnemonic, index), lastActivity: Date.now() })
  },

  addAccount: async (label) => {
    const { mnemonic, accounts, sessionPassword } = get()
    if (!mnemonic || !sessionPassword) throw new Error('Unlock wallet first')
    const nextIndex = accounts.reduce((m, a) => Math.max(m, a.index), -1) + 1
    const d = deriveEthAccount(mnemonic, nextIndex)
    const next = [...accounts, { index: nextIndex, label: label || `Account ${nextIndex + 1}`, address: d.address }]
    await reencrypt(mnemonic, next, sessionPassword)
    set({ accounts: next, activeIndex: nextIndex, derived: d })
  },

  exportMnemonic: () => get().mnemonic,
  exportPrivateKey: () => get().derived?.privateKey ?? null,

  wipeWallet: () => {
    clearVaultStorage()
    set({
      hasVault: false, unlocked: false, mnemonic: null, accounts: [],
      activeIndex: 0, derived: null, sessionPassword: null,
    })
  },
}))
