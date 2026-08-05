import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Button, Card, Field, PageTitle } from '@/components/ui'
import { useWallet } from '@/stores/wallet'
import { isValidMnemonic } from '@/lib/crypto/derive'

export function Onboarding() {
  const nav = useNavigate()
  const createWallet = useWallet((s) => s.createWallet)
  const importWallet = useWallet((s) => s.importWallet)
  const [mode, setMode] = useState<'welcome' | 'create' | 'import' | 'backup'>('welcome')
  const [password, setPassword] = useState('')
  const [password2, setPassword2] = useState('')
  const [phrase, setPhrase] = useState('')
  const [shown, setShown] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)

  async function onCreate() {
    setErr('')
    if (password !== password2) return setErr('Passwords do not match')
    setBusy(true)
    try {
      setShown(await createWallet(password))
      setMode('backup')
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  async function onImport() {
    setErr('')
    if (password !== password2) return setErr('Passwords do not match')
    if (!isValidMnemonic(phrase)) return setErr('Invalid recovery phrase')
    setBusy(true)
    try {
      await importWallet(phrase, password)
      nav('/', { replace: true })
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Failed')
    } finally {
      setBusy(false)
    }
  }

  if (mode === 'welcome') {
    return (
      <div className="mx-auto flex min-h-dvh max-w-lg flex-col justify-center px-4 py-10">
        <div className="mb-8 text-center">
          <img src="https://howlscan.org/assets/howlcoin-logo-meme-pup-coin.jpg" alt="Howlcoin"
            className="mx-auto mb-4 h-20 w-20 rounded-full border-2 border-[color-mix(in_srgb,var(--mint)_45%,transparent)] object-cover" />
          <h1 className="font-display m-0 text-3xl font-semibold">Howl Pack</h1>
          <p className="muted mt-2 text-sm">Base-first self-custody wallet for the pack. Your keys, your howl.</p>
        </div>
        <div className="flex flex-col gap-3">
          <Button onClick={() => setMode('create')}>Create new wallet</Button>
          <Button variant="secondary" onClick={() => setMode('import')}>Import recovery phrase</Button>
          <a href="/classic" className="muted mt-2 text-center text-sm no-underline hover:text-[var(--mint)]">Classic Howl L1 wallet →</a>
        </div>
      </div>
    )
  }

  if (mode === 'backup') {
    return (
      <div className="mx-auto max-w-lg px-4 py-10">
        <PageTitle title="Backup phrase" subtitle="Write these words down. Anyone with them can move your funds." />
        <Card className="mb-4"><p className="mono m-0 text-center text-base leading-relaxed text-[var(--mint)]">{shown}</p></Card>
        <Card className="mb-4"><p className="m-0 text-sm text-[var(--amber)]">Never share this phrase. Howl never stores it on a server.</p></Card>
        <Button onClick={() => nav('/', { replace: true })}>I saved it — continue</Button>
      </div>
    )
  }

  const isImport = mode === 'import'
  return (
    <div className="mx-auto max-w-lg px-4 py-10">
      <PageTitle title={isImport ? 'Import wallet' : 'Create wallet'} subtitle="Encrypts your vault on this device." />
      {isImport ? (
        <div className="mb-3">
          <label className="muted mb-1 block text-xs font-bold uppercase">Recovery phrase</label>
          <Field multiline value={phrase} onChange={(e) => setPhrase(e.target.value)} placeholder="twelve words…" autoComplete="off" />
        </div>
      ) : null}
      <div className="mb-3">
        <label className="muted mb-1 block text-xs font-bold uppercase">Password</label>
        <Field type="password" value={password} onChange={(e) => setPassword(e.target.value)} placeholder="min 6 characters" />
      </div>
      <div className="mb-4">
        <label className="muted mb-1 block text-xs font-bold uppercase">Confirm password</label>
        <Field type="password" value={password2} onChange={(e) => setPassword2(e.target.value)} placeholder="repeat password" />
      </div>
      {err ? <p className="mb-3 text-sm text-[var(--red)]">{err}</p> : null}
      <div className="flex flex-col gap-2">
        <Button disabled={busy} onClick={isImport ? onImport : onCreate}>{busy ? 'Working…' : isImport ? 'Import' : 'Create'}</Button>
        <Button variant="ghost" onClick={() => setMode('welcome')}>Back</Button>
      </div>
    </div>
  )
}
