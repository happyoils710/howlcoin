import { create } from 'zustand'
import { createMnemonic, isValidMnemonic, normalizeMnemonic } from '@/lib/crypto/derive'
import {
  clearSessionPin,
  createClassicVault,
  hasClassicVault,
  loadClassicVault,
  loadWalletIndex,
  setSessionPin,
  tryMigratePackVault,
  wipeClassicVault,
  type ClassicMultiVault,
} from '@/lib/crypto/classicVault'

interface WalletState {
  hasVault: boolean
  unlocked: boolean
  multi: ClassicMultiVault | null
  sessionPin: string | null
  lastActivity: number
  bootstrap: () => void
  createWallet: (password: string) => Promise<string>
  importWallet: (mnemonic: string, password: string) => Promise<void>
  unlock: (password: string) => Promise<void>
  lock: () => void
  touch: () => void
  wipeWallet: () => void
  /** Prepare classic iframe auto-unlock */
  armClassicUnlock: () => void
}

export const useWallet = create<WalletState>((set, get) => ({
  hasVault: false,
  unlocked: false,
  multi: null,
  sessionPin: null,
  lastActivity: Date.now(),

  bootstrap: () => {
    const idx = loadWalletIndex()
    set({
      hasVault: hasClassicVault(),
      unlocked: false,
      multi: null,
      sessionPin: null,
      // surface address list from index if present
    })
    void idx
  },

  createWallet: async (password) => {
    if (password.length < 4) throw new Error('Password must be at least 4 characters')
    const mnemonic = createMnemonic(128)
    const multi = await createClassicVault(mnemonic, password)
    setSessionPin(password)
    set({
      hasVault: true,
      unlocked: true,
      multi,
      sessionPin: password,
      lastActivity: Date.now(),
    })
    return mnemonic
  },

  importWallet: async (phrase, password) => {
    if (password.length < 4) throw new Error('Password must be at least 4 characters')
    const mnemonic = normalizeMnemonic(phrase)
    if (!isValidMnemonic(mnemonic)) throw new Error('Invalid recovery phrase')
    const multi = await createClassicVault(mnemonic, password)
    setSessionPin(password)
    set({
      hasVault: true,
      unlocked: true,
      multi,
      sessionPin: password,
      lastActivity: Date.now(),
    })
  },

  unlock: async (password) => {
    let multi: ClassicMultiVault
    try {
      multi = await loadClassicVault(password)
    } catch (e) {
      const migrated = await tryMigratePackVault(password)
      if (!migrated) throw e
      multi = migrated
    }
    setSessionPin(password)
    set({
      unlocked: true,
      multi,
      sessionPin: password,
      lastActivity: Date.now(),
    })
  },

  lock: () => {
    clearSessionPin()
    set({ unlocked: false, multi: null, sessionPin: null })
  },

  touch: () => set({ lastActivity: Date.now() }),

  wipeWallet: () => {
    wipeClassicVault()
    set({ hasVault: false, unlocked: false, multi: null, sessionPin: null })
  },

  armClassicUnlock: () => {
    const pin = get().sessionPin
    if (pin) setSessionPin(pin)
  },
}))
