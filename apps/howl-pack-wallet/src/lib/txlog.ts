export interface LocalTx {
  hash: string
  chainId: number
  from: string
  to?: string
  value?: string
  label: string
  status: 'pending' | 'confirmed' | 'failed'
  timestamp: number
  kind: 'send' | 'swap' | 'approve' | 'contract' | 'other'
}

const KEY = 'howl_pack_txlog_v1'

export function loadTxLog(): LocalTx[] {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return []
    return JSON.parse(raw) as LocalTx[]
  } catch {
    return []
  }
}

export function pushTx(tx: LocalTx): void {
  const list = loadTxLog().filter((t) => t.hash !== tx.hash)
  list.unshift(tx)
  localStorage.setItem(KEY, JSON.stringify(list.slice(0, 200)))
}

export function updateTxStatus(hash: string, status: LocalTx['status']): void {
  const list = loadTxLog().map((t) => (t.hash === hash ? { ...t, status } : t))
  localStorage.setItem(KEY, JSON.stringify(list))
}
