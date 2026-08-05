import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { ChainKey } from '@/lib/chains'

export type ThemeName = 'pack' | 'light' | 'dark' | 'neo' | 'bones'
export type AutoLockMin = 1 | 5 | 15 | 0

interface SettingsState {
  theme: ThemeName
  chainKey: ChainKey
  customRpcs: Partial<Record<ChainKey, string>>
  currency: 'USD'
  autoLockMin: AutoLockMin
  setTheme: (t: ThemeName) => void
  setChainKey: (k: ChainKey) => void
  setCustomRpc: (k: ChainKey, url: string) => void
  clearCustomRpc: (k: ChainKey) => void
  setAutoLockMin: (m: AutoLockMin) => void
}

function applyTheme(t: ThemeName) {
  document.documentElement.setAttribute('data-theme', t)
  try {
    localStorage.setItem('howlscan_theme_v1', t)
    localStorage.setItem('howl_theme_v1', t)
  } catch { /* ignore */ }
  const meta = document.querySelector('meta[name="theme-color"]')
  if (meta) {
    meta.setAttribute(
      'content',
      t === 'light' ? '#f4f6fa' : t === 'neo' ? '#03010a' : t === 'bones' ? '#000000' : t === 'pack' ? '#1a1524' : '#0c0f14',
    )
  }
}

export const useSettings = create<SettingsState>()(
  persist(
    (set) => ({
      theme: 'pack',
      chainKey: 'base',
      customRpcs: {},
      currency: 'USD',
      autoLockMin: 5,
      setTheme: (theme) => { applyTheme(theme); set({ theme }) },
      setChainKey: (chainKey) => set({ chainKey }),
      setCustomRpc: (k, url) => set((s) => ({ customRpcs: { ...s.customRpcs, [k]: url } })),
      clearCustomRpc: (k) => set((s) => {
        const next = { ...s.customRpcs }
        delete next[k]
        return { customRpcs: next }
      }),
      setAutoLockMin: (autoLockMin) => set({ autoLockMin }),
    }),
    {
      name: 'howl_pack_settings_v1',
      onRehydrateStorage: () => (state) => {
        if (state?.theme) applyTheme(state.theme)
        else applyTheme('pack')
      },
    },
  ),
)
