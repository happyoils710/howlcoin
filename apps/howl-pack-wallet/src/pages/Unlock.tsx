import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Field, PageTitle } from '@/components/ui'
import { loadWalletIndex } from '@/lib/crypto/classicVault'
import { useWallet } from '@/stores/wallet'

export function Unlock() {
  const nav = useNavigate()
  const unlock = useWallet((s) => s.unlock)
  const wipeWallet = useWallet((s) => s.wipeWallet)
  const idx = loadWalletIndex()
  const addr = idx?.wallets?.[0]?.address
  const [password, setPassword] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function onUnlock() {
    setErr('')
    setBusy(true)
    try {
      await unlock(password)
      nav('/', { replace: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Unlock failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center px-4 py-10">
      <div className="mb-6 text-center">
        <img
          src="https://howlscan.org/assets/howlcoin-logo-meme-pup-coin.jpg"
          alt=""
          className="mx-auto mb-3 h-16 w-16 rounded-full object-cover"
        />
        <PageTitle
          title="Welcome back"
          subtitle={addr ? addr.slice(0, 8) + '…' + addr.slice(-6) : 'Howl Pack Wallet · full features'}
        />
      </div>
      <Card className="mb-4">
        <label className="muted mb-1 block text-xs font-bold uppercase">Password / PIN</label>
        <Field
          type="password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onUnlock()}
          placeholder="Same PIN as before"
          autoFocus
        />
        <p className="muted m-0 mt-2 text-xs">
          Unlocks your full wallet: HOWL L1, SOL, Base, City, Play, NFTs, bridge, and more.
        </p>
      </Card>
      {err ? <p className="mb-3 text-sm text-[var(--red)]">{err}</p> : null}
      <Button disabled={busy || !password} onClick={onUnlock}>
        {busy ? 'Unlocking…' : 'Unlock'}
      </Button>
      <button
        type="button"
        className="muted mt-6 border-0 bg-transparent text-center text-xs underline"
        onClick={() => {
          if (confirm('Erase encrypted vault on this device? You need your recovery phrase to restore.')) {
            wipeWallet()
            nav('/onboarding', { replace: true })
          }
        }}
      >
        Forgot password — erase vault on this device
      </button>
    </div>
  )
}
