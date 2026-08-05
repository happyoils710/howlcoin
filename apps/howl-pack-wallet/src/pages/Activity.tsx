import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Card, PageTitle, Spinner } from '@/components/ui'
import { loadTxLog, type LocalTx } from '@/lib/txlog'
import { PACK_CHAINS } from '@/lib/chains'
import { shortAddress } from '@/lib/crypto/derive'
import { useSettings } from '@/stores/settings'
import { useWallet } from '@/stores/wallet'
import clsx from 'clsx'

type Filter = 'all' | 'send' | 'swap' | 'contract' | 'other'

async function fetchBasescan(address: string, chainId: number): Promise<LocalTx[]> {
  const key = import.meta.env.VITE_BASESCAN_KEY as string | undefined
  if (!key || chainId !== 8453) return []
  try {
    const url = `https://api.basescan.org/api?module=account&action=txlist&address=${address}&startblock=0&endblock=99999999&page=1&offset=40&sort=desc&apikey=${key}`
    const res = await fetch(url)
    const json = await res.json()
    if (!Array.isArray(json.result)) return []
    return json.result.map((t: { hash: string; from: string; to: string; value: string; isError: string; timeStamp: string; functionName?: string; input?: string }): LocalTx => {
      const valEth = Number(t.value) / 1e18
      const incoming = t.to?.toLowerCase() === address.toLowerCase()
      const kind: LocalTx['kind'] = t.input && t.input !== '0x' ? (t.functionName?.includes('swap') ? 'swap' : 'contract') : 'send'
      return {
        hash: t.hash, chainId, from: t.from, to: t.to, value: valEth ? valEth.toFixed(6) : undefined,
        label: kind === 'contract' ? (t.functionName?.split('(')[0] || 'Contract call')
          : incoming ? `Received ${valEth ? valEth.toFixed(4) + ' ETH' : 'transfer'}`
            : `Sent ${valEth ? valEth.toFixed(4) + ' ETH' : 'transfer'}`,
        status: t.isError === '1' ? 'failed' : 'confirmed',
        timestamp: Number(t.timeStamp) * 1000, kind,
      }
    })
  } catch { return [] }
}

export function Activity() {
  const address = useWallet((s) => s.derived?.address)
  const chainKey = useSettings((s) => s.chainKey)
  const chain = PACK_CHAINS[chainKey]
  const [filter, setFilter] = useState<Filter>('all')
  const q = useQuery({
    queryKey: ['activity', address, chain.chain.id],
    enabled: !!address,
    refetchInterval: 45_000,
    queryFn: async () => {
      const local = loadTxLog().filter((t) => t.chainId === chain.chain.id)
      const remote = address ? await fetchBasescan(address, chain.chain.id) : []
      const map = new Map<string, LocalTx>()
      for (const t of [...remote, ...local]) map.set(t.hash.toLowerCase(), t)
      return [...map.values()].sort((a, b) => b.timestamp - a.timestamp)
    },
  })
  const rows = useMemo(() => {
    const list = q.data || []
    return filter === 'all' ? list : list.filter((t) => t.kind === filter)
  }, [q.data, filter])
  const filters: { id: Filter; label: string }[] = [
    { id: 'all', label: 'All' }, { id: 'send', label: 'Send' }, { id: 'swap', label: 'Swap' },
    { id: 'contract', label: 'Contract' }, { id: 'other', label: 'Other' },
  ]

  return (
    <div>
      <PageTitle title="Activity" subtitle={chain.label} />
      <div className="no-scrollbar mb-4 flex gap-2 overflow-x-auto">
        {filters.map((f) => (
          <button key={f.id} type="button" onClick={() => setFilter(f.id)}
            className={clsx('shrink-0 rounded-full border px-3 py-1.5 text-xs font-bold',
              filter === f.id ? 'border-[var(--mint)] bg-[var(--mint-dim)] text-[var(--mint)]' : 'border-[var(--border)] bg-[var(--panel)] text-[var(--muted)]')}>
            {f.label}
          </button>
        ))}
      </div>
      {q.isLoading ? <Spinner /> : null}
      {!q.isLoading && !rows.length ? (
        <Card><p className="muted m-0 text-sm">No activity yet. Sends from this wallet appear here.</p></Card>
      ) : null}
      <div className="flex flex-col gap-2">
        {rows.map((t) => (
          <a key={t.hash} href={`${chain.explorer}/tx/${t.hash}`} target="_blank" rel="noreferrer"
            className="card block px-4 py-3 text-[var(--text)] no-underline">
            <div className="flex items-start justify-between gap-2">
              <div>
                <div className="font-bold">{t.label}</div>
                <div className="muted mt-0.5 text-xs">{t.to ? shortAddress(t.to, 4) : '—'} · {new Date(t.timestamp).toLocaleString()}</div>
              </div>
              <span className={clsx('badge', t.status === 'confirmed' && 'badge-ok', t.status === 'pending' && 'badge-warn', t.status === 'failed' && 'badge-muted')}>
                {t.status}
              </span>
            </div>
          </a>
        ))}
      </div>
    </div>
  )
}
