const CACHE_MS = 60_000
let cache: { at: number; map: Record<string, number> } | null = null
const FALLBACK: Record<string, number> = { ethereum: 3200, 'usd-coin': 1, weth: 3200 }

export async function fetchUsdPrices(coingeckoIds: string[]): Promise<Record<string, number>> {
  const ids = [...new Set(coingeckoIds.filter(Boolean))]
  if (!ids.length) return {}
  if (cache && Date.now() - cache.at < CACHE_MS) {
    const out: Record<string, number> = {}
    for (const id of ids) out[id] = cache.map[id] ?? FALLBACK[id] ?? 0
    return out
  }
  try {
    const coins = ids.map((id) => `coingecko:${id}`).join(',')
    const res = await fetch(`https://coins.llama.fi/prices/current/${coins}`)
    if (!res.ok) throw new Error('price api')
    const json = (await res.json()) as { coins?: Record<string, { price?: number }> }
    const map: Record<string, number> = { ...FALLBACK }
    for (const [k, v] of Object.entries(json.coins || {})) {
      const id = k.replace(/^coingecko:/, '')
      if (v?.price != null) map[id] = v.price
    }
    cache = { at: Date.now(), map }
    const out: Record<string, number> = {}
    for (const id of ids) out[id] = map[id] ?? FALLBACK[id] ?? 0
    return out
  } catch {
    const out: Record<string, number> = {}
    for (const id of ids) out[id] = FALLBACK[id] ?? 0
    return out
  }
}

export function formatUsd(n: number): string {
  if (!Number.isFinite(n)) return '$0.00'
  return new Intl.NumberFormat('en-US', {
    style: 'currency', currency: 'USD', maximumFractionDigits: n < 1 ? 4 : 2,
  }).format(n)
}

export function formatTokenAmount(raw: bigint, decimals: number, maxFrac = 6): string {
  const neg = raw < 0n
  const v = neg ? -raw : raw
  const base = 10n ** BigInt(decimals)
  const whole = v / base
  const frac = v % base
  const fracStr = frac.toString().padStart(decimals, '0').slice(0, maxFrac).replace(/0+$/, '')
  const s = fracStr ? `${whole}.${fracStr}` : whole.toString()
  return neg ? `-${s}` : s
}
