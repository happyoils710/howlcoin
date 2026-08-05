import { useMemo, useState } from 'react'
import { ExternalLink, Search } from 'lucide-react'
import { Card, Field, PageTitle } from '@/components/ui'
import { CATEGORY_LABELS, DAPPS, type DappCategory } from '@/lib/dapps'
import clsx from 'clsx'

const ALL = 'all' as const
type Cat = typeof ALL | DappCategory

export function Discover() {
  const [q, setQ] = useState('')
  const [cat, setCat] = useState<Cat>(ALL)
  const [browserUrl, setBrowserUrl] = useState<string | null>(null)
  const cats: { id: Cat; label: string }[] = [
    { id: ALL, label: 'All' },
    ...(Object.keys(CATEGORY_LABELS) as DappCategory[]).map((id) => ({ id, label: CATEGORY_LABELS[id] })),
  ]
  const list = useMemo(() => {
    const qq = q.trim().toLowerCase()
    return DAPPS.filter((d) => {
      if (cat !== ALL && d.category !== cat) return false
      if (!qq) return true
      return d.name.toLowerCase().includes(qq) || d.description.toLowerCase().includes(qq)
    })
  }, [q, cat])

  if (browserUrl) {
    return (
      <div className="flex min-h-[70dvh] flex-col">
        <div className="mb-2 flex items-center gap-2">
          <button type="button" className="btn btn-ghost !w-auto px-3" onClick={() => setBrowserUrl(null)}>← Back</button>
          <a href={browserUrl} target="_blank" rel="noreferrer" className="muted flex items-center gap-1 truncate text-xs no-underline">
            {browserUrl} <ExternalLink size={12} />
          </a>
        </div>
        <Card className="min-h-[60dvh] flex-1 !p-0 overflow-hidden">
          <iframe title="dapp" src={browserUrl} className="h-[65dvh] w-full border-0 bg-white" />
        </Card>
        <p className="muted mt-2 text-center text-xs">Some sites block iframes — use external open if blank.</p>
      </div>
    )
  }

  return (
    <div>
      <PageTitle title="Discover" subtitle="Base dApps · curated by Howl" />
      <div className="relative mb-3">
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-[var(--muted)]" />
        <Field className="!pl-9" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search DeFi, NFTs, social…" />
      </div>
      <div className="no-scrollbar mb-4 flex gap-2 overflow-x-auto">
        {cats.map((c) => (
          <button key={c.id} type="button" onClick={() => setCat(c.id)}
            className={clsx('shrink-0 rounded-full border px-3 py-1.5 text-xs font-bold',
              cat === c.id ? 'border-[var(--mint)] bg-[var(--mint-dim)] text-[var(--mint)]' : 'border-[var(--border)] bg-[var(--panel)] text-[var(--muted)]')}>
            {c.label}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {list.map((d) => (
          <Card key={d.id} className="flex flex-col gap-2">
            <div className="font-bold">{d.name}</div>
            <div className="badge badge-muted">{CATEGORY_LABELS[d.category]}</div>
            <p className="muted m-0 flex-1 text-sm">{d.description}</p>
            <div className="flex gap-2">
              <button type="button" className="btn btn-primary !min-h-10 flex-1 text-sm" onClick={() => setBrowserUrl(d.url)}>Open</button>
              <a href={d.url} target="_blank" rel="noreferrer" className="btn btn-ghost !min-h-10 !w-auto px-3 no-underline"><ExternalLink size={16} /></a>
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
