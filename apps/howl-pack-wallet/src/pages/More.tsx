import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Button, Card, Field, PageTitle } from '@/components/ui'
import { PACK_CHAINS, type ChainKey } from '@/lib/chains'
import { shortAddress } from '@/lib/crypto/derive'
import { useSettings, type AutoLockMin, type ThemeName } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'
import clsx from 'clsx'

export function More() {
  const accounts = useWallet((s) => s.accounts)
  const activeIndex = useWallet((s) => s.activeIndex)
  const setActiveIndex = useWallet((s) => s.setActiveIndex)
  const addAccount = useWallet((s) => s.addAccount)
  const lock = useWallet((s) => s.lock)
  const exportMnemonic = useWallet((s) => s.exportMnemonic)
  const exportPrivateKey = useWallet((s) => s.exportPrivateKey)
  const wipeWallet = useWallet((s) => s.wipeWallet)
  const theme = useSettings((s) => s.theme)
  const setTheme = useSettings((s) => s.setTheme)
  const chainKey = useSettings((s) => s.chainKey)
  const setChainKey = useSettings((s) => s.setChainKey)
  const customRpcs = useSettings((s) => s.customRpcs)
  const setCustomRpc = useSettings((s) => s.setCustomRpc)
  const clearCustomRpc = useSettings((s) => s.clearCustomRpc)
  const autoLockMin = useSettings((s) => s.autoLockMin)
  const setAutoLockMin = useSettings((s) => s.setAutoLockMin)
  const [rpcDraft, setRpcDraft] = useState(customRpcs[chainKey] || '')
  const [secret, setSecret] = useState<string | null>(null)
  const [secretKind, setSecretKind] = useState<'seed' | 'key' | null>(null)
  const [confirmText, setConfirmText] = useState('')
  const themes: ThemeName[] = ['pack', 'dark', 'light', 'neo', 'bones']
  const locks: { v: AutoLockMin; l: string }[] = [
    { v: 1, l: '1 min' }, { v: 5, l: '5 min' }, { v: 15, l: '15 min' }, { v: 0, l: 'Never' },
  ]

  function reveal(kind: 'seed' | 'key') {
    if (!confirm(kind === 'seed' ? 'Recovery phrase controls ALL funds. Continue?' : 'Private key controls this account. Continue?')) return
    setSecretKind(kind)
    setSecret(kind === 'seed' ? exportMnemonic() : exportPrivateKey())
  }

  return (
    <div>
      <PageTitle title="More" subtitle="Accounts · security · network" />
      <h2 className="muted mb-2 text-xs font-bold uppercase tracking-wide">Accounts</h2>
      <Card className="mb-4 !p-2">
        {accounts.map((a) => (
          <button key={a.index} type="button" onClick={() => setActiveIndex(a.index)}
            className={clsx('flex w-full items-center justify-between rounded-xl border-0 px-3 py-3 text-left',
              a.index === activeIndex ? 'bg-[var(--mint-dim)]' : 'bg-transparent')}>
            <span className="font-bold">{a.label}</span>
            <span className="mono muted text-xs">{shortAddress(a.address, 4)}</span>
          </button>
        ))}
        <Button variant="ghost" className="mt-1" onClick={() => void addAccount().catch((e) => alert(e.message))}>Add account</Button>
      </Card>

      <h2 className="muted mb-2 text-xs font-bold uppercase tracking-wide">Network</h2>
      <Card className="mb-4">
        <select className="field mb-3" value={chainKey} onChange={(e) => {
          const k = e.target.value as ChainKey
          setChainKey(k)
          setRpcDraft(customRpcs[k] || '')
        }}>
          {(Object.keys(PACK_CHAINS) as ChainKey[]).map((k) => (
            <option key={k} value={k}>{PACK_CHAINS[k].label}</option>
          ))}
        </select>
        <label className="muted mb-1 block text-xs font-bold uppercase">Custom RPC</label>
        <Field value={rpcDraft} onChange={(e) => setRpcDraft(e.target.value)} placeholder={PACK_CHAINS[chainKey].defaultRpc} />
        <div className="mt-2 flex gap-2">
          <Button variant="secondary" className="!min-h-10" onClick={() => { if (rpcDraft.startsWith('http')) setCustomRpc(chainKey, rpcDraft.trim()) }}>Save RPC</Button>
          <Button variant="ghost" className="!min-h-10" onClick={() => { clearCustomRpc(chainKey); setRpcDraft('') }}>Reset</Button>
        </div>
      </Card>

      <h2 className="muted mb-2 text-xs font-bold uppercase tracking-wide">Security</h2>
      <Card className="mb-4">
        <p className="muted m-0 mb-2 text-xs font-bold uppercase">Auto-lock</p>
        <div className="mb-3 flex flex-wrap gap-2">
          {locks.map((l) => (
            <button key={l.v} type="button" onClick={() => setAutoLockMin(l.v)}
              className={clsx('rounded-full border px-3 py-1.5 text-xs font-bold',
                autoLockMin === l.v ? 'border-[var(--mint)] bg-[var(--mint-dim)] text-[var(--mint)]' : 'border-[var(--border)] text-[var(--muted)]')}>
              {l.l}
            </button>
          ))}
        </div>
        <Button variant="secondary" onClick={() => lock()}>Lock wallet now</Button>
      </Card>

      <h2 className="muted mb-2 text-xs font-bold uppercase tracking-wide">Theme</h2>
      <Card className="mb-4">
        <div className="grid grid-cols-2 gap-2">
          {themes.map((t) => (
            <button key={t} type="button" onClick={() => setTheme(t)}
              className={clsx('rounded-xl border px-3 py-2 text-sm font-bold capitalize',
                theme === t ? 'border-[var(--mint)] bg-[var(--mint-dim)] text-[var(--mint)]' : 'border-[var(--border)] text-[var(--text)]')}>
              {t}
            </button>
          ))}
        </div>
      </Card>

      <h2 className="muted mb-2 text-xs font-bold uppercase tracking-wide">Danger zone</h2>
      <Card className="mb-4">
        <Button variant="danger" className="mb-2" onClick={() => reveal('seed')}>Export recovery phrase</Button>
        <Button variant="danger" className="mb-2" onClick={() => reveal('key')}>Export private key</Button>
        {secret ? (
          <div className="mt-3 rounded-xl border border-[var(--red)] bg-[rgba(255,122,144,0.08)] p-3">
            <p className="m-0 mb-2 text-xs font-bold text-[var(--red)]">{secretKind === 'seed' ? 'Recovery phrase' : 'Private key'}</p>
            <p className="mono m-0 text-sm">{secret}</p>
            <Button variant="ghost" className="mt-2 !min-h-10" onClick={() => setSecret(null)}>Hide</Button>
          </div>
        ) : null}
        <div className="mt-4 border-t border-[var(--border)] pt-3">
          <Field value={confirmText} onChange={(e) => setConfirmText(e.target.value)} placeholder="type DELETE to erase vault" />
          <Button variant="danger" className="mt-2" disabled={confirmText !== 'DELETE'} onClick={() => {
            wipeWallet()
            window.location.href = `${import.meta.env.BASE_URL}onboarding`
          }}>Erase vault on this device</Button>
        </div>
      </Card>

      <Card>
        <p className="m-0 text-sm font-bold">Howl Pack Wallet</p>
        <p className="muted m-0 mt-1 text-xs">Non-custodial · Base-first · BIP44 m/44&apos;/60&apos;/0&apos;/0/i (same as classic /app ETH)</p>
        <div className="mt-3 flex flex-col gap-2 text-sm">
          <a href="/whitepaper">White paper</a>
          <a href="/classic">Classic multi-chain wallet</a>
          <a href="https://howlscan.org">Howlscan</a>
          <Link to="/">Home</Link>
        </div>
      </Card>
    </div>
  )
}
