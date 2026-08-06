import { Link } from 'react-router-dom'
import { ArrowDownLeft, ArrowUpRight, ArrowLeftRight, Copy, Check, ChevronDown } from 'lucide-react'
import { useState } from 'react'
import { Card, Spinner } from '@/components/ui'
import { shortAddress } from '@/lib/crypto/derive'
import { PACK_CHAINS } from '@/lib/chains'
import { formatUsd } from '@/lib/prices'
import { useBalances } from '@/hooks/useBalances'
import { useSettings } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'
import clsx from 'clsx'

export function Home() {
  const derived = useWallet((s) => s.derived)
  const accounts = useWallet((s) => s.accounts)
  const activeIndex = useWallet((s) => s.activeIndex)
  const chainKey = useSettings((s) => s.chainKey)
  const setChainKey = useSettings((s) => s.setChainKey)
  const { data, isLoading, isError, refetch, isFetching } = useBalances()
  const [copied, setCopied] = useState(false)
  const [showNets, setShowNets] = useState(false)
  const label = accounts.find((a) => a.index === activeIndex)?.label || 'Account'
  const chain = PACK_CHAINS[chainKey]

  async function copyAddr() {
    if (!derived?.address) return
    await navigator.clipboard.writeText(derived.address)
    setCopied(true)
    setTimeout(() => setCopied(false), 1500)
  }

  return (
    <div>
      <header className="mb-5 flex items-center justify-between gap-3">
        <div className="flex min-w-0 items-center gap-3">
          <img src="https://howlscan.org/assets/howlcoin-logo-meme-pup-coin.jpg" alt=""
            className="h-11 w-11 shrink-0 rounded-full border-2 border-[color-mix(in_srgb,var(--mint)_40%,transparent)] object-cover" />
          <div className="min-w-0">
            <div className="truncate text-sm font-bold">{label}</div>
            <button type="button" onClick={copyAddr} className="muted flex items-center gap-1 border-0 bg-transparent p-0 font-mono text-xs">
              {derived ? shortAddress(derived.address, 5) : '—'}
              {copied ? <Check size={12} className="text-[var(--mint)]" /> : <Copy size={12} />}
            </button>
          </div>
        </div>
        <div className="relative">
          <button type="button" className="badge badge-ok flex items-center gap-1 border-0" onClick={() => setShowNets((v) => !v)}>
            {chain.short} <ChevronDown size={14} />
          </button>
          {showNets ? (
            <div className="card absolute right-0 z-20 mt-2 w-44 overflow-hidden p-1">
              {(Object.keys(PACK_CHAINS) as (keyof typeof PACK_CHAINS)[]).map((k) => (
                <button key={k} type="button"
                  className={clsx('block w-full rounded-lg border-0 bg-transparent px-3 py-2 text-left text-sm',
                    k === chainKey ? 'bg-[var(--mint-dim)] text-[var(--mint)]' : 'text-[var(--text)]')}
                  onClick={() => { setChainKey(k); setShowNets(false) }}>
                  {PACK_CHAINS[k].label}
                </button>
              ))}
            </div>
          ) : null}
        </div>
      </header>

      <Card className="mb-4 text-center relative overflow-hidden">
        <p className="m-0 text-[0.65rem] font-extrabold uppercase tracking-[0.14em] text-[var(--mint)]">Bag value · ser</p>
        <p className="font-display m-0 mt-1 text-4xl font-semibold tracking-tight"
          style={{ background: 'linear-gradient(90deg,#00ff9c,#ffe066,#ff3d9a)', WebkitBackgroundClip: 'text', backgroundClip: 'text', color: 'transparent' }}>
          {isLoading ? '…' : formatUsd(data?.totalUsd ?? 0)}
        </p>
        <p className="muted m-0 mt-1 text-xs">on {chain.label}{isFetching ? ' · refreshing' : ''} · NFA · awoo</p>
      </Card>

      <div className="mb-5 grid grid-cols-3 gap-2">
        <Link to="/send" className="btn btn-primary no-underline"><ArrowUpRight size={18} /> Yeet</Link>
        <Link to="/receive" className="btn btn-secondary no-underline"><ArrowDownLeft size={18} /> Recv</Link>
        <Link to="/swap" className="btn btn-secondary no-underline"><ArrowLeftRight size={18} /> Swap</Link>
      </div>

      <div className="mb-2 flex items-center justify-between">
        <h2 className="m-0 text-sm font-bold uppercase tracking-wide text-[var(--muted)]">Bags</h2>
        <button type="button" className="muted border-0 bg-transparent text-xs font-semibold" onClick={() => refetch()}>Refresh</button>
      </div>
      {isLoading ? <Spinner /> : null}
      {isError ? <Card><p className="m-0 text-sm text-[var(--amber)]">Could not load balances. Check RPC in Settings.</p></Card> : null}
      <div className="flex flex-col gap-2">
        {(data?.rows || []).map((row) => (
          <div key={`${row.token.chainKey}-${row.token.symbol}-${row.token.address}`}
            className="card flex items-center justify-between gap-3 px-4 py-3">
            <div className="flex items-center gap-3">
              <div className="flex h-10 w-10 items-center justify-center rounded-full bg-[var(--mint-dim)] text-sm font-bold text-[var(--mint)]">
                {row.token.symbol.slice(0, 1)}
              </div>
              <div>
                <div className="font-bold">{row.token.symbol}</div>
                <div className="muted text-xs">{row.token.name}</div>
              </div>
            </div>
            <div className="text-right">
              <div className="font-bold">{row.amount}</div>
              <div className="muted text-xs">{row.usdLabel}</div>
            </div>
          </div>
        ))}
      </div>
      <Card className="mt-5">
        <p className="m-0 text-sm">
          <span className="font-bold text-[var(--mint)]">Howl L1</span>
          <span className="muted"> — City, Play, and Scrypt HOWL live in the classic app.</span>
        </p>
        <a href="/classic" className="mt-2 inline-block text-sm font-bold text-[var(--mint)] no-underline">Open classic wallet →</a>
      </Card>
    </div>
  )
}
