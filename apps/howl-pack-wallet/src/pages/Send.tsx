import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { isAddress, type Address } from 'viem'
import { Button, Card, Field, PageTitle } from '@/components/ui'
import { useBalances } from '@/hooks/useBalances'
import { sendToken } from '@/hooks/useSend'
import { PACK_CHAINS } from '@/lib/chains'
import { useSettings } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'

export function Send() {
  const nav = useNavigate()
  const chainKey = useSettings((s) => s.chainKey)
  const customRpc = useSettings((s) => s.customRpcs[chainKey])
  const touch = useWallet((s) => s.touch)
  const { data } = useBalances()
  const tokens = data?.rows || []
  const [tokenIdx, setTokenIdx] = useState(0)
  const [to, setTo] = useState('')
  const [amount, setAmount] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const [hash, setHash] = useState('')
  const token = tokens[tokenIdx]?.token
  const explorer = PACK_CHAINS[chainKey].explorer
  const canSend = useMemo(() => token && isAddress(to) && Number(amount) > 0 && !busy, [token, to, amount, busy])

  async function onSend() {
    if (!token) return
    setErr('')
    setBusy(true)
    touch()
    try {
      if (!isAddress(to)) throw new Error('Invalid address')
      setHash(await sendToken({ token, to: to as Address, amount, chainKey, customRpc }))
    } catch (e) {
      setErr(e instanceof Error ? e.message : 'Send failed')
    } finally {
      setBusy(false)
    }
  }

  if (hash) {
    return (
      <div>
        <PageTitle title="Sent" subtitle="Transaction submitted" />
        <Card className="mb-4">
          <p className="muted m-0 text-xs">Tx hash</p>
          <p className="mono m-0 mt-1 text-[var(--mint)]">{hash}</p>
          <a href={`${explorer}/tx/${hash}`} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm font-bold">View on explorer →</a>
        </Card>
        <Button onClick={() => nav('/')}>Done</Button>
      </div>
    )
  }

  return (
    <div>
      <PageTitle title="Send" subtitle={PACK_CHAINS[chainKey].label}
        right={<Link to="/" className="muted text-sm font-semibold no-underline">Cancel</Link>} />
      <Card className="mb-3">
        <label className="muted mb-1 block text-xs font-bold uppercase">Asset</label>
        <select className="field" value={tokenIdx} onChange={(e) => setTokenIdx(Number(e.target.value))}>
          {tokens.map((r, i) => <option key={i} value={i}>{r.token.symbol} · bal {r.amount}</option>)}
        </select>
      </Card>
      <Card className="mb-3">
        <label className="muted mb-1 block text-xs font-bold uppercase">To</label>
        <Field value={to} onChange={(e) => setTo(e.target.value.trim())} placeholder="0x…" autoComplete="off" spellCheck={false} />
      </Card>
      <Card className="mb-4">
        <label className="muted mb-1 block text-xs font-bold uppercase">Amount</label>
        <Field value={amount} onChange={(e) => setAmount(e.target.value)} placeholder="0.0" inputMode="decimal" />
      </Card>
      {err ? <p className="mb-3 text-sm text-[var(--red)]">{err}</p> : null}
      <Button disabled={!canSend} onClick={onSend}>{busy ? 'Sending…' : 'Review & send'}</Button>
    </div>
  )
}
